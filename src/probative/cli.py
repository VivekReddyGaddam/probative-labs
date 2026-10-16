"""Command-line interface: ``probative --help``."""

from __future__ import annotations

import secrets
import webbrowser
from datetime import date
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
import yaml

from probative import __version__
from probative import schema as S
from probative.config import ProjectConfig
from probative.guardrails import GuardrailSpec
from probative.ingest import Pseudonymizer, apply_mapping, check_work_units, read_tables, resolve_mapping
from probative.ingest.mapping import PRESET_INFO, PRESETS
from probative.pipeline import AnalysisError, analyze
from probative.playbooks import get_playbook
from probative.report import write_audit_pack, write_pack
from probative.rollout import NoiseModel, lock_registration, noise_from_units, plan_waves, power_table
from probative.simulate import EXPORT_FORMATS

app = typer.Typer(
    help="Probative Labs: independent verification of what AI delivers. Plan rollouts that can be proven, "
    "measure what changed, and audit the resolutions an AI claims.",
    no_args_is_help=True,
    add_completion=False,
)
bakeoff_app = typer.Typer(
    help="Pre-purchase bake-off: a randomized head-to-head trial of AI vendors on your own work.",
    no_args_is_help=True,
)
plan_app = typer.Typer(
    help="Plan before go-live: power, rollout waves and a measurement plan to sign off.", no_args_is_help=True
)
app.add_typer(plan_app, name="plan")
app.add_typer(bakeoff_app, name="bakeoff")

ConfigOpt = Annotated[Path, typer.Option("--config", "-c", help="Project configuration file.")]


def _say(msg: str) -> None:
    typer.secho(f"  · {msg}", fg=typer.colors.BRIGHT_BLACK)


def _guardrails(cfg: ProjectConfig) -> list[GuardrailSpec]:
    pb = get_playbook(cfg.playbook)
    out = []
    for g in pb.guardrails:
        spec = g.with_overrides(cfg.guardrails.get(g.key))
        if spec is not None:
            out.append(spec)
    return out


def _load(config: Path) -> ProjectConfig:
    if not config.exists():
        typer.secho(f"No configuration at {config}. Run `probative init` or `probative demo` first.", fg="red")
        raise typer.Exit(2)
    return ProjectConfig.load(config)


@app.callback(invoke_without_command=True)
def _main(version: Annotated[bool, typer.Option("--version", help="Show the version.")] = False) -> None:
    if version:
        typer.echo(f"probative {__version__}")
        raise typer.Exit()


@app.command()
def demo(
    directory: Annotated[Path, typer.Argument(help="Where to write the demo project.")] = Path("probative-demo"),
    effect: Annotated[float, typer.Option(help="Planted relative effect on agent hours per ticket.")] = -0.15,
    reopen_effect: Annotated[float, typer.Option(help="Planted change in reopen probability (try 0.03).")] = 0.0,
    reps: Annotated[int, typer.Option(help="Bootstrap draws.")] = 199,
    seed: Annotated[int, typer.Option()] = 7,
    fmt: Annotated[
        str, typer.Option("--format", help="Export format to simulate: " + ", ".join(EXPORT_FORMATS) + ".")
    ] = "servicenow",
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML evidence pack.")] = False,
) -> None:
    """Simulate a service desk with a known AI effect and measure it end to end."""
    from probative.simulate import SimSpec, demo_config, generate

    if fmt not in EXPORT_FORMATS:
        typer.secho(f"Unknown format {fmt!r}. Choose from: {', '.join(EXPORT_FORMATS)}", fg="red")
        raise typer.Exit(2)

    typer.secho(f"Simulating a 40-team service desk (planted effect {effect:+.0%}) in {directory}/", bold=True)
    sim = generate(SimSpec(effort_effect=effect, reopen_effect=reopen_effect, seed=seed))
    sim.write(directory / "data", fmt)
    raw = demo_config("data", sim.truth, fmt)
    raw["analysis"]["bootstrap_reps"] = reps
    cfg_path = directory / "probative.yaml"
    cfg_path.write_text(yaml.safe_dump(raw, sort_keys=False))
    cfg = ProjectConfig.load(cfg_path)
    reg_path = cfg.resolve(cfg.registration)
    if not reg_path.exists():
        lock_registration(cfg, _guardrails(cfg), reg_path)
    try:
        result = analyze(cfg, progress=_say).to_dict()
    except AnalysisError as exc:
        typer.secho(f"Analysis failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    paths = write_pack(result, directory / "out")
    audit_result = _demo_audit(cfg, reps=max(reps, 199))
    audit_paths = write_audit_pack(audit_result, directory / "out" / "audit")
    t = sim.truth
    typer.echo("")
    typer.secho(result["headline"], bold=True)
    p = result["primary"]
    if p["metric"] == "effort_hours":
        truth = (
            f"{t['effort_att_hours_per_ticket']:+.3f} h per ticket "
            f"({t['effort_relative']:+.1%} after the learning curve)"
        )
    else:
        truth = (
            f"{t['resolution_att_hours_per_ticket']:+.3f} h of resolution time per ticket "
            f"({fmt} exports carry no agent time)"
        )
    typer.echo(f"\nPlanted truth: {truth}. Estimate: {p['estimate']:+.3f} h [{p['ci_low']:+.3f}, {p['ci_high']:+.3f}].")
    fa = audit_result["failed_share"]
    planted = t["false_deflections"] / max(1, t["false_deflections"] + t["verified_deflections"])
    typer.echo("")
    typer.secho(audit_result["headline"], bold=True)
    typer.echo(
        f"\nPlanted: {planted:.1%} of the virtual agent's resolutions came back. "
        f"Audit: {fa['estimate']:.1%} [{fa['ci_low']:.1%}, {fa['ci_high']:.1%}]."
    )
    typer.echo(
        f"\nEvidence pack:    {paths['html']}\nResolution audit: {audit_paths['html']}\n"
        f"Markdown:         {paths['markdown']}\nResults JSON:     {paths['json']}"
    )
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


def _program_info(cfg: ProjectConfig | None) -> dict:
    if cfg is None:
        return {}
    p = cfg.program
    return {"name": p.name, "vendor": p.vendor, "tool": p.tool, "function": p.function}


def _demo_audit(cfg: ProjectConfig, reps: int) -> dict:
    from probative.audit import load_audit_inputs, run_audit

    assert cfg.data.self_service is not None
    inputs = load_audit_inputs(
        [cfg.resolve(p) for p in cfg.data.work_units],
        cfg.resolve(cfg.data.self_service),
        preset=cfg.data.mapping_preset,
        typing_fields=cfg.typing.fields,
        pseudonymizer=Pseudonymizer.from_env_or_file(cfg.base_dir / ".probative" / "pseudonym.key"),
    )
    return run_audit(
        inputs,
        window_days=cfg.audit.window_days,
        match=cfg.audit.match,
        price=cfg.audit.price_per_resolution,
        currency=cfg.value.currency,
        reps=reps,
        program=_program_info(cfg),
    ).to_dict()


TEMPLATE = """\
# Probative Labs project: one file per AI program. Docs: docs/configuration.md
program:
  id: my-ai-program
  name: AI agent assist
  vendor: Your vendor
  tool: Agent assist
  function: it_service_management
  owner: Head of IT Service Management
  contract_ref: null

hypothesis:
  statement: >-
    AI summaries and resolution suggestions cut agent hours per ticket by at least
    10% without lowering quality.
  primary_metric: effort_hours        # or resolution_hours
  expected_effect: -0.10

playbook: service_desk

data:
  work_units: data/incidents.csv      # CSV or Parquet; a list of files is fine
  mapping_preset: {preset}             # see `probative presets`
  mapping: {{}}                          # override: canonical_field: source_column
  exposure: data/rollout.csv          # team_id, start_date[, end_date, kind, randomized]
  self_service: null                  # optional: virtual agent sessions or the AI's claimed resolutions
  teams: null                         # optional: team_id, parent_unit, function, size_band
  staffing: null                      # optional: team_id, week_start, hours (if time worked is not recorded)

analysis:
  start: null                         # default: first record
  end: null                           # default: last record
  bootstrap_reps: 499
  min_group_size: 5                   # teams with fewer people are left out

guardrails: {{}}                        # e.g. reopen_rate: {{threshold: 0.02}}

value:
  currency: USD
  loaded_hourly_cost: {{low: 50, mid: 60, high: 75}}
  realization_rate: {{low: 0.0, mid: 0.0, high: 0.0}}   # share of freed hours provably redeployed
  realized_savings: []                # {{description, amount, evidence}}
  owner: null                         # who agreed these numbers
  agreed_on: null

costs:
  - component: license
    description: AI licence uplift
    amount: 100000
    basis: annual                     # period | annual | monthly | one_time

claimed: []                           # vendor or dashboard estimates, shown beside the measured value

audit:                                # `probative audit`: did the AI's claimed resolutions hold?
  window_days: 7                      # a ticket from the same person within this many days counts as a return
  match: same_type                    # or any: count a ticket about anything
  price_per_resolution: null          # e.g. 0.99 if the AI is billed per resolution
"""


@app.command()
def init(
    directory: Annotated[Path, typer.Argument()] = Path("."),
    preset: Annotated[str, typer.Option(help="Field mapping preset: " + ", ".join(PRESETS) + ".")] = "servicenow",
) -> None:
    """Write a starter probative.yaml."""
    if preset not in PRESETS:
        typer.secho(f"Unknown preset {preset!r}. Choose from: {', '.join(PRESETS)}", fg="red")
        raise typer.Exit(2)
    path = directory / "probative.yaml"
    if path.exists():
        typer.secho(f"{path} already exists; not overwriting.", fg="yellow")
        raise typer.Exit(1)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(preset=preset))
    typer.echo(f"Wrote {path}. Edit the data paths, then run `probative health`.")


@app.command()
def simulate(
    out: Annotated[Path, typer.Argument(help="Output directory.")] = Path("data"),
    effect: Annotated[float, typer.Option()] = -0.15,
    resolution_effect: Annotated[float, typer.Option()] = -0.08,
    reopen_effect: Annotated[float, typer.Option()] = 0.0,
    teams: Annotated[int, typer.Option()] = 40,
    weeks: Annotated[int, typer.Option()] = 78,
    deflection: Annotated[bool, typer.Option()] = True,
    seed: Annotated[int, typer.Option()] = 7,
    fmt: Annotated[str, typer.Option("--format", help="Ticket export format: " + ", ".join(EXPORT_FORMATS) + ".")] = (
        "servicenow"
    ),
) -> None:
    """Write synthetic exports (ServiceNow, Jira, Zendesk, ...) with a planted effect."""
    from probative.simulate import SimSpec, generate

    waves = (int(weeks * 0.56), int(weeks * 0.67), int(weeks * 0.77))
    sim = generate(
        SimSpec(
            n_teams=teams,
            n_weeks=weeks,
            wave_weeks=waves,
            effort_effect=effect,
            resolution_effect=resolution_effect,
            reopen_effect=reopen_effect,
            deflection=deflection,
            seed=seed,
        )
    )
    paths = sim.write(out, fmt)
    for k, p in paths.items():
        typer.echo(f"{k:9s} {p}")


@app.command()
def presets() -> None:
    """List the built-in field mappings for platform exports."""
    for name, info in PRESET_INFO.items():
        note = "" if info["basis"] == "documented" else "  (typical export: check your column names)"
        typer.echo(f"{name:26s} {info['label']}{note}")
    typer.echo("\nUse one with `probative init --preset NAME` or `data.mapping_preset` in probative.yaml.")


@app.command()
def health(config: ConfigOpt = Path("probative.yaml")) -> None:
    """Check data quality and field mapping before analysis."""
    cfg = _load(config)
    raw, files, warnings = read_tables([cfg.resolve(p) for p in cfg.data.work_units])
    mapping = resolve_mapping(cfg.data.mapping_preset, cfg.data.mapping)
    units, rep = apply_mapping(raw, mapping, S.WORK_UNIT_COLUMNS, required=S.REQUIRED_WORK_UNIT_COLUMNS)
    pseud = Pseudonymizer.from_env_or_file(cfg.base_dir / ".probative" / "pseudonym.key")
    units = pseud.apply(units, (*S.PERSON_COLUMNS, *S.SOURCE_ID_COLUMNS))
    clean, report = check_work_units(units)
    for w in warnings:
        report.add("schema_change", "warning", w)
    typer.secho(f"{sum(f.rows for f in files):,} rows in {len(files)} file(s)", bold=True)
    typer.echo(f"Mapped fields: {', '.join(rep.mapped) or 'none'}")
    if rep.missing_optional:
        typer.echo(f"Not available: {', '.join(rep.missing_optional)}")
    for k, n in rep.coercion_failures.items():
        typer.secho(f"  {n:,} values in {k} could not be parsed", fg="yellow")
    colors = {"error": "red", "warning": "yellow", "info": "bright_black"}
    for issue in report.issues:
        typer.secho(f"  [{issue.severity}] {issue.message}", fg=colors[issue.severity])
    typer.echo(f"Usable rows: {len(clean):,} ({report.rows_excluded:,} excluded)")
    raise typer.Exit(0 if report.ok else 1)


@app.command()
def register(
    config: ConfigOpt = Path("probative.yaml"),
    force: Annotated[bool, typer.Option(help="Replace an existing registration.")] = False,
) -> None:
    """Lock the hypothesis, metrics, guardrails and window before results exist."""
    cfg = _load(config)
    path = cfg.resolve(cfg.registration)
    try:
        rec = lock_registration(cfg, _guardrails(cfg), path, force=force)
    except FileExistsError as exc:
        typer.secho(str(exc), fg="yellow")
        raise typer.Exit(1) from exc
    typer.echo(f"Registered at {rec['registered_at']} → {path}\nSHA-256 {rec['sha256']}")
    typer.echo("Commit this file to version control so its timestamp can be verified.")


@app.command("analyze")
def analyze_cmd(
    config: ConfigOpt = Path("probative.yaml"),
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("out"),
    reps: Annotated[int | None, typer.Option(help="Override bootstrap draws.")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML evidence pack.")] = False,
) -> None:
    """Run the analysis and write the evidence pack."""
    cfg = _load(config)
    if reps:
        cfg.analysis.bootstrap_reps = reps
    try:
        result = analyze(cfg, progress=_say).to_dict()
    except AnalysisError as exc:
        typer.secho(f"Analysis failed: {exc}", fg="red")
        if exc.health:
            for issue in exc.health.issues:
                typer.echo(f"  [{issue.severity}] {issue.message}")
        raise typer.Exit(1) from exc
    out_dir = out if out.is_absolute() else cfg.base_dir / out
    paths = write_pack(result, out_dir)
    typer.secho(result["headline"], bold=True)
    typer.echo(f"Recommendation: {result['recommendation']['action']}. {result['recommendation']['reason']}")
    typer.echo(f"\nEvidence pack: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


def _ints(s: str) -> list[int]:
    return [int(x) for x in s.split(",") if x.strip()]


@plan_app.command("power")
def plan_power(
    effect: Annotated[float, typer.Option(help="Hoped-for relative effect, e.g. -0.10.")] = -0.10,
    teams: Annotated[str, typer.Option(help="Team counts to try, comma-separated.")] = "12,20,30,40",
    weeks_post: Annotated[str, typer.Option(help="Weeks after the last wave, comma-separated.")] = "8,12,16",
    waves: Annotated[int, typer.Option()] = 3,
    weeks_pre: Annotated[int, typer.Option(help="Weeks of history before the first wave.")] = 26,
    spacing: Annotated[int, typer.Option(help="Weeks between waves.")] = 4,
    holdout: Annotated[float, typer.Option(help="Share of teams held back.")] = 0.2,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Calibrate noise from this project's history.")
    ] = None,
    sims: Annotated[int, typer.Option()] = 200,
) -> None:
    """How many teams and weeks are needed to detect the hoped-for effect."""
    if config:
        noise = _noise_from_history(_load(config))
        typer.echo(
            f"Calibrated from history: mean {noise.mean:.3f}, team sd {noise.sd_team:.3f}, "
            f"week sd {noise.sd_week:.3f}, cell sd {noise.sd_cell:.3f}, autocorrelation {noise.rho:.2f}"
        )
    else:
        noise = NoiseModel()
        typer.echo("Using default noise (pass --config to calibrate from your own history).")
    rows = power_table(
        team_options=_ints(teams),
        post_options=_ints(weeks_post),
        n_waves=waves,
        weeks_pre=weeks_pre,
        effect=effect * noise.mean,
        noise=noise,
        wave_spacing=spacing,
        holdout_share=holdout,
        sims=sims,
    )
    typer.secho(f"\nPower to detect {effect:+.0%} ({waves} waves, {holdout:.0%} held back):", bold=True)
    typer.echo(f"{'teams':>6} {'weeks after':>12} {'power':>7} {'smallest detectable':>21}")
    for r in rows:
        mark = "  ✓" if r.power >= 0.8 else ""
        typer.echo(f"{r.n_teams:>6} {r.weeks_post:>12} {r.power:>7.0%} {r.mde_relative:>20.1%}{mark}")
    typer.echo("\n✓ = at least 80% power. If nothing qualifies, the program is too small to measure as planned.")


def _noise_from_history(cfg: ProjectConfig) -> NoiseModel:
    raw, _, _ = read_tables([cfg.resolve(p) for p in cfg.data.work_units])
    units, _ = apply_mapping(
        raw,
        resolve_mapping(cfg.data.mapping_preset, cfg.data.mapping),
        S.WORK_UNIT_COLUMNS,
        required=S.REQUIRED_WORK_UNIT_COLUMNS,
    )
    units, _ = check_work_units(units)
    units = get_playbook(cfg.playbook).derive(units)
    return noise_from_units(units, cfg.hypothesis.primary_metric)


@plan_app.command("pack")
def plan_pack(
    config: ConfigOpt = Path("probative.yaml"),
    sims: Annotated[int, typer.Option(help="Power simulations per row.")] = 200,
    weeks_post: Annotated[str, typer.Option(help="Weeks after the last wave to check, comma-separated.")] = "8,12,16",
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("out") / "plan",
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML plan.")] = False,
) -> None:
    """Write the measurement plan to sign before go-live: waves, power, guardrails, decision rules."""
    from probative.report import write_plan_pack
    from probative.rollout import build_plan

    cfg = _load(config)
    _say("Simulating detectability for the planned rollout")
    plan = build_plan(cfg, _guardrails(cfg), sims=sims, weeks_post=tuple(_ints(weeks_post)))
    out_dir = out if out.is_absolute() else cfg.base_dir / out
    paths = write_plan_pack(plan, out_dir)
    typer.secho(
        f"Expected evidence grade {plan['expected_grade']['grade']}: {plan['expected_grade']['text']}", bold=True
    )
    if plan.get("readout"):
        typer.echo(f"Earliest reliable readout: {plan['readout']['date']}")
    for n in plan["notes"]:
        typer.echo(f"  note: {n}")
    typer.echo(f"\nMeasurement plan: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


@plan_app.command("waves")
def plan_waves_cmd(
    teams_file: Annotated[Path, typer.Argument(help="CSV with a team_id column (and optional strata).")],
    waves: Annotated[int, typer.Option()] = 3,
    start: Annotated[str, typer.Option(help="First wave start date, YYYY-MM-DD.")] = "",
    spacing: Annotated[int, typer.Option(help="Weeks between waves.")] = 4,
    holdout: Annotated[float, typer.Option(help="Share of teams held back.")] = 0.2,
    holdout_weeks: Annotated[int, typer.Option(help="Weeks after the last wave before holdout teams get the AI.")] = 12,
    stratify: Annotated[str | None, typer.Option(help="Column to balance waves on, e.g. parent_unit.")] = None,
    seed: Annotated[int | None, typer.Option(help="Randomization seed (recorded in the output).")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("rollout.csv"),
) -> None:
    """Assign teams to rollout waves by lottery and write an exposure file."""
    teams = pd.read_csv(teams_file, dtype=str)
    if S.TEAM not in teams.columns:
        typer.secho(f"{teams_file} needs a {S.TEAM} column", fg="red")
        raise typer.Exit(2)
    seed = seed if seed is not None else secrets.randbelow(2**31)
    first = date.fromisoformat(start) if start else date.today()
    plan = plan_waves(
        teams,
        n_waves=waves,
        first_start=first,
        spacing_weeks=spacing,
        holdout_share=holdout,
        holdout_weeks=holdout_weeks,
        stratify_by=stratify,
        seed=seed,
    )
    plan.to_csv(out, index=False)
    summary = plan.groupby(["wave", "start_date"]).size().rename("teams").reset_index()
    typer.echo(summary.to_string(index=False))
    typer.echo(f"\nWrote {out} (seed {seed}). Register the analysis now: `probative register`.")


def _pairs(items: list[str] | None, option: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            typer.secho(f'{option} takes field="Source column", got {item!r}', fg="red")
            raise typer.Exit(2)
        key, value = item.split("=", 1)
        out[key.strip()] = value.strip().strip('"')
    return out


@app.command()
def audit(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Take files, mappings and settings from this project.")
    ] = None,
    claims: Annotated[
        Path | None, typer.Option(help="The AI's claimed resolutions: a conversation or automated-resolution export.")
    ] = None,
    tickets: Annotated[
        list[Path] | None,
        typer.Option(help="Ticket export covering the same dates plus a week. Repeat for more files."),
    ] = None,
    preset: Annotated[str | None, typer.Option(help="Ticket export preset: " + ", ".join(PRESETS) + ".")] = None,
    window: Annotated[int | None, typer.Option(help="Days within which a return counts against a claim (7).")] = None,
    match: Annotated[str | None, typer.Option(help="same_type (default) or any.")] = None,
    price: Annotated[float | None, typer.Option(help="Price paid per claimed resolution, if billed that way.")] = None,
    currency: Annotated[str | None, typer.Option(help="Currency of the price (USD).")] = None,
    resolved_value: Annotated[
        str | None, typer.Option(help="Outcome value that marks a claimed resolution (resolved).")
    ] = None,
    claims_column: Annotated[
        list[str] | None, typer.Option("--claims-column", help='Map a claims field, e.g. requester_id="User Email".')
    ] = None,
    tickets_column: Annotated[
        list[str] | None, typer.Option("--tickets-column", help='Map a ticket field, e.g. requester_id="Caller".')
    ] = None,
    reps: Annotated[int, typer.Option(help="Bootstrap draws.")] = 999,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (out/audit).")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML audit.")] = False,
) -> None:
    """Audit the resolutions an AI claims: did people come back within a week?

    Works without a staggered rollout, so it fits desks where everyone got the AI at once.
    """
    from probative.audit import AuditError, run_audit

    cfg = _default_config(config, claims, tickets)
    base = cfg.base_dir if cfg else Path(".")
    a = cfg.audit if cfg else None
    match = match or (a.match if a else "same_type")
    if match not in ("same_type", "any"):
        typer.secho("--match must be same_type or any", fg="red")
        raise typer.Exit(2)
    try:
        inputs = _audit_inputs(cfg, claims, tickets, preset, claims_column, tickets_column, resolved_value)
        _say(f"{len(inputs.claims):,} claimed resolutions and {len(inputs.tickets):,} tickets read")
        result = run_audit(
            inputs,
            window_days=window or (a.window_days if a else 7),
            match=match,  # type: ignore[arg-type]
            price=price if price is not None else (a.price_per_resolution if a else None),
            currency=currency or (cfg.value.currency if cfg else "USD"),
            reps=reps,
            program=_program_info(cfg),
        ).to_dict()
    except (AuditError, ValueError, FileNotFoundError) as exc:
        typer.secho(f"Audit failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    out_dir = out or Path("out") / "audit"
    out_dir = out_dir if out_dir.is_absolute() else base / out_dir
    paths = write_audit_pack(result, out_dir)
    typer.secho(result["headline"], bold=True)
    typer.echo(f"\nResolution audit: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


def _audit_inputs(
    cfg: ProjectConfig | None,
    claims: Path | None,
    tickets: list[Path] | None,
    preset: str | None,
    claims_column: list[str] | None,
    tickets_column: list[str] | None,
    resolved_value: str | None,
):
    """Load the AI's conversations and the tickets, from a project and/or command-line options."""
    from probative.audit import load_audit_inputs

    base = cfg.base_dir if cfg else Path(".")
    a = cfg.audit if cfg else None
    if claims is None and cfg and cfg.data.self_service:
        claims = cfg.resolve(cfg.data.self_service)
    if not tickets and cfg:
        tickets = [cfg.resolve(p) for p in cfg.data.work_units]
    if claims is None or not tickets:
        typer.secho("Give --claims and --tickets, or a project whose data.self_service is set (--config).", fg="red")
        raise typer.Exit(2)
    preset = preset or (cfg.data.mapping_preset if cfg else "canonical")
    if preset not in PRESETS:
        typer.secho(f"Unknown preset {preset!r}. Choose from: {', '.join(PRESETS)}", fg="red")
        raise typer.Exit(2)
    pseud = (
        None
        if cfg and cfg.data.pseudonymized
        else Pseudonymizer.from_env_or_file(base / ".probative" / "pseudonym.key")
    )
    return load_audit_inputs(
        tickets,
        claims,
        preset=preset,
        ticket_mapping={**(cfg.data.mapping if cfg else {}), **_pairs(tickets_column, "--tickets-column")},
        claims_mapping={**(cfg.data.self_service_mapping if cfg else {}), **_pairs(claims_column, "--claims-column")},
        typing_fields=cfg.typing.fields if cfg else None,
        resolved_value=resolved_value or (a.resolved_value if a else "resolved"),
        pseudonymizer=pseud,
    )


def _default_config(config: Path | None, claims: Path | None, tickets: list[Path] | None) -> ProjectConfig | None:
    if config is None and claims is None and not tickets and Path("probative.yaml").exists():
        config = Path("probative.yaml")
    return _load(config) if config else None


@app.command()
def reconcile(
    bill: Annotated[Path, typer.Argument(help="The vendor's bill: one line per billed item, or a date and count.")],
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Take files and settings from a project.")
    ] = None,
    claims: Annotated[Path | None, typer.Option(help="Your record of the AI's conversations.")] = None,
    tickets: Annotated[list[Path] | None, typer.Option(help="Ticket export covering the period plus a week.")] = None,
    preset: Annotated[str | None, typer.Option(help="Ticket export preset.")] = None,
    period: Annotated[str | None, typer.Option(help="Billing period: YYYY-MM, or YYYY-MM-DD:YYYY-MM-DD.")] = None,
    price: Annotated[float | None, typer.Option(help="Price per resolution, if the bill has no amounts.")] = None,
    currency: Annotated[str | None, typer.Option(help="Currency (USD).")] = None,
    contractual: Annotated[
        bool | None,
        typer.Option(
            "--contractual/--no-contractual",
            help="The contract defines a resolution as no repeat ticket within the window: dispute each one.",
        ),
    ] = None,
    window: Annotated[int | None, typer.Option(help="Days within which a repeat ticket counts (7).")] = None,
    match: Annotated[str | None, typer.Option(help="same_type (default) or any.")] = None,
    resolved_value: Annotated[str | None, typer.Option(help="Outcome value that marks a resolution.")] = None,
    bill_column: Annotated[
        list[str] | None, typer.Option("--bill-column", help='Map a bill field, e.g. item_id="Conversation".')
    ] = None,
    claims_column: Annotated[list[str] | None, typer.Option("--claims-column")] = None,
    tickets_column: Annotated[list[str] | None, typer.Option("--tickets-column")] = None,
    reps: Annotated[int, typer.Option(help="Bootstrap draws for the comparison with your agents.")] = 499,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (out/reconcile).")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the dispute pack.")] = False,
) -> None:
    """Reconcile a per-resolution bill: billing errors, resolutions that did not hold, and a dispute pack."""
    from probative.audit import AuditError, load_bill, reconcile_bill

    cfg = _default_config(config, claims, tickets)
    a = cfg.audit if cfg else None
    m = match or (a.match if a else "same_type")
    if m not in ("same_type", "any"):
        typer.secho("--match must be same_type or any", fg="red")
        raise typer.Exit(2)
    try:
        inputs = _audit_inputs(cfg, claims, tickets, preset, claims_column, tickets_column, resolved_value)
        bill_df, bill_meta = load_bill(bill, _pairs(bill_column, "--bill-column"))
        result = reconcile_bill(
            bill_df,
            inputs.conversations,
            inputs.tickets,
            period=period,
            price=price if price is not None else (a.price_per_resolution if a else None),
            currency=currency or (cfg.value.currency if cfg else "USD"),
            window_days=window or (a.window_days if a else 7),
            match=m,  # type: ignore[arg-type]
            contractual=contractual if contractual is not None else (a.contractual if a else False),
            definition_name=a.definition_name if a else "Verified resolution",
            resolved_value=resolved_value or (a.resolved_value if a else "resolved"),
            reps=reps,
            program=_program_info(cfg),
            files=[*inputs.files, bill_meta],
        ).to_dict()
    except (AuditError, ValueError, FileNotFoundError) as exc:
        typer.secho(f"Reconciliation failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    from probative.report import write_reconciliation_pack

    base = cfg.base_dir if cfg else Path(".")
    out_dir = out or Path("out") / "reconcile"
    out_dir = out_dir if out_dir.is_absolute() else base / out_dir
    paths = write_reconciliation_pack(result, out_dir)
    typer.secho(result["headline"], bold=True)
    typer.echo(f"\nDispute pack:   {paths['html']}\nDisputed items: {paths['csv']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


@app.command("watch")
def watch_cmd(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Take files and settings from a project.")
    ] = None,
    claims: Annotated[Path | None, typer.Option(help="Your record of the AI's conversations.")] = None,
    tickets: Annotated[list[Path] | None, typer.Option(help="Ticket export covering the same weeks.")] = None,
    preset: Annotated[str | None, typer.Option(help="Ticket export preset.")] = None,
    events: Annotated[Path | None, typer.Option(help="CSV of known vendor release dates (date, name).")] = None,
    recent_weeks: Annotated[int, typer.Option(help="Latest weeks to check.")] = 4,
    reference_weeks: Annotated[int, typer.Option(help="Weeks before them to compare with.")] = 12,
    min_change: Annotated[
        float, typer.Option(help="Smallest worsening that counts, as a share (0.02 = 2 points).")
    ] = 0.02,
    window: Annotated[int | None, typer.Option(help="Days within which a return counts (7).")] = None,
    match: Annotated[str | None, typer.Option(help="same_type (default) or any.")] = None,
    resolved_value: Annotated[str | None, typer.Option(help="Outcome value that marks a resolution.")] = None,
    claims_column: Annotated[list[str] | None, typer.Option("--claims-column")] = None,
    tickets_column: Annotated[list[str] | None, typer.Option("--tickets-column")] = None,
    reps: Annotated[int, typer.Option(help="Bootstrap draws.")] = 499,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (out/watch).")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the report.")] = False,
) -> None:
    """Watch for silent regressions: did the AI get worse in the latest weeks, or after a release?

    Exits with code 3 when it finds a regression, so it can run on a schedule and alert.
    """
    from probative.audit import AuditError, load_events, watch

    cfg = _default_config(config, claims, tickets)
    a = cfg.audit if cfg else None
    try:
        inputs = _audit_inputs(cfg, claims, tickets, preset, claims_column, tickets_column, resolved_value)
        result = watch(
            inputs.conversations,
            inputs.tickets,
            window_days=window or (a.window_days if a else 7),
            match=(match or (a.match if a else "same_type")),  # type: ignore[arg-type]
            recent_weeks=recent_weeks,
            reference_weeks=reference_weeks,
            min_change=min_change,
            events=load_events(events) if events else None,
            resolved_value=resolved_value or (a.resolved_value if a else "resolved"),
            reps=reps,
            program=_program_info(cfg),
            files=inputs.files,
        ).to_dict()
    except (AuditError, ValueError, FileNotFoundError) as exc:
        typer.secho(f"Watch failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    from probative.report import write_watch_pack

    base = cfg.base_dir if cfg else Path(".")
    out_dir = out or Path("out") / "watch"
    out_dir = out_dir if out_dir.is_absolute() else base / out_dir
    paths = write_watch_pack(result, out_dir)
    color = {"regression": "red", "watch": "yellow"}.get(result["status"])
    typer.secho(result["headline"], bold=True, fg=color)
    typer.echo(f"\nRegression watch: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())
    if result["status"] == "regression":
        raise typer.Exit(3)


@app.command()
def closures(
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Take files and settings from a project.")
    ] = None,
    tickets: Annotated[
        list[Path] | None, typer.Option(help="Ticket export, including who resolved each ticket.")
    ] = None,
    preset: Annotated[str | None, typer.Option(help="Ticket export preset.")] = None,
    ai_resolver: Annotated[
        list[str] | None, typer.Option("--ai-resolver", help="A 'resolved by' value that means the AI closed it.")
    ] = None,
    resolver_column: Annotated[str | None, typer.Option(help="The 'resolved by' column (auto-detected).")] = None,
    ai_flag_column: Annotated[str | None, typer.Option(help="Or a yes/no column marking AI closures.")] = None,
    window: Annotated[int | None, typer.Option(help="Days within which a repeat ticket counts (7).")] = None,
    match: Annotated[str | None, typer.Option(help="same_type (default) or any.")] = None,
    tickets_column: Annotated[list[str] | None, typer.Option("--tickets-column")] = None,
    reps: Annotated[int, typer.Option(help="Bootstrap draws.")] = 999,
    out: Annotated[Path | None, typer.Option("--out", "-o", help="Output directory (out/closures).")] = None,
    open_report: Annotated[bool, typer.Option("--open", help="Open the report.")] = False,
) -> None:
    """Audit tickets the AI closed by itself: how often were they reopened or repeated, against your agents?"""
    from probative.audit import AuditError, audit_closures, load_closure_inputs

    cfg = _default_config(config, None, tickets)
    a = cfg.audit if cfg else None
    if not tickets and cfg:
        tickets = [cfg.resolve(p) for p in cfg.data.work_units]
    if not tickets:
        typer.secho("Give --tickets, or a project (--config).", fg="red")
        raise typer.Exit(2)
    base = cfg.base_dir if cfg else Path(".")
    try:
        inputs = load_closure_inputs(
            tickets,
            ai_resolvers=ai_resolver or (a.ai_resolvers if a else None),
            resolver_column=resolver_column or (a.resolver_column if a else None),
            ai_flag_column=ai_flag_column or (a.ai_flag_column if a else None),
            preset=preset or (cfg.data.mapping_preset if cfg else "canonical"),
            ticket_mapping={**(cfg.data.mapping if cfg else {}), **_pairs(tickets_column, "--tickets-column")},
            typing_fields=cfg.typing.fields if cfg else None,
            pseudonymizer=None
            if cfg and cfg.data.pseudonymized
            else Pseudonymizer.from_env_or_file(base / ".probative" / "pseudonym.key"),
        )
        _say(f"{inputs.ai_closed:,} tickets closed by the AI ({inputs.flag_source})")
        result = audit_closures(
            inputs.tickets,
            window_days=window or (a.window_days if a else 7),
            match=(match or (a.match if a else "same_type")),  # type: ignore[arg-type]
            reps=reps,
            flag_source=inputs.flag_source,
            program=_program_info(cfg),
            files=inputs.files,
        ).to_dict()
    except (AuditError, ValueError, FileNotFoundError) as exc:
        typer.secho(f"Action audit failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    from probative.report import write_closure_pack

    out_dir = out or Path("out") / "closures"
    out_dir = out_dir if out_dir.is_absolute() else base / out_dir
    paths = write_closure_pack(result, out_dir)
    typer.secho(result["headline"], bold=True)
    typer.echo(f"\nAction audit: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


@app.command()
def reproduce(
    result_file: Annotated[Path, typer.Argument(help="A recorded results.json (evidence pack) or audit.json.")],
    config: Annotated[Path | None, typer.Option("--config", "-c", help="The project the result came from.")] = None,
    claims: Annotated[Path | None, typer.Option(help="For an audit: the AI's conversations.")] = None,
    tickets: Annotated[list[Path] | None, typer.Option(help="For an audit: the ticket export.")] = None,
    preset: Annotated[str | None, typer.Option(help="Ticket export preset.")] = None,
) -> None:
    """Rerun a recorded result from the same inputs and confirm every number matches (for auditors)."""
    import json

    from probative.reproduce import compare, count_values, file_mismatches

    recorded = json.loads(result_file.read_text())
    kind = recorded.get("kind", "evidence_pack")
    cfg = _default_config(config, claims, tickets)
    if kind == "evidence_pack":
        if cfg is None:
            typer.secho("Give the project the evidence pack came from (--config).", fg="red")
            raise typer.Exit(2)
        boot = (recorded.get("lineage") or {}).get("bootstrap") or {}
        cfg.analysis.bootstrap_reps = boot.get("reps", cfg.analysis.bootstrap_reps)
        cfg.analysis.seed = boot.get("seed", cfg.analysis.seed)
        rerun = analyze(cfg, progress=_say).to_dict()
        files = rerun["lineage"]["files"]
    elif kind == "resolution_audit":
        from probative.audit import run_audit

        s = recorded["settings"]
        inputs = _audit_inputs(cfg, claims, tickets, preset, None, None, None)
        rerun = run_audit(
            inputs,
            window_days=s["window_days"],
            match=s.get("match_requested", s["match"]),
            price=s["price"],
            currency=s["currency"],
            reps=s["reps"],
            seed=s["seed"],
            level=s["level"],
            min_cell=s["min_cell"],
            program=recorded.get("program") or {},
        ).to_dict()
        files = inputs.files
    else:
        typer.secho(f"Reproducing a {kind.replace('_', ' ')} is not supported yet.", fg="yellow")
        raise typer.Exit(2)
    changed = file_mismatches((recorded.get("lineage") or {}).get("files") or [], files)
    for c in changed:
        typer.secho(f"  input: {c}", fg="yellow")
    diffs = compare(recorded, rerun)
    if diffs:
        typer.secho(f"Not reproduced: {len(diffs)} difference(s).", fg="red", bold=True)
        for d in diffs[:20]:
            typer.echo(f"  {d}")
        raise typer.Exit(1)
    typer.secho(
        f"Reproduced: all {count_values(recorded):,} recorded values match"
        + (" (inputs match their recorded fingerprints)." if not changed else "."),
        fg="green",
        bold=True,
    )


@app.command()
def benchmark(
    results: Annotated[Path | None, typer.Option(help="An evidence pack's results.json.")] = None,
    audit_file: Annotated[Path | None, typer.Option("--audit", help="A resolution audit's audit.json.")] = None,
    closures_file: Annotated[Path | None, typer.Option("--closures", help="An action audit's closures.json.")] = None,
    reconciliation_file: Annotated[
        Path | None, typer.Option("--reconciliation", help="A bill reconciliation's reconciliation.json.")
    ] = None,
    platform: Annotated[str | None, typer.Option(help="Ticket platform, e.g. servicenow.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("benchmark-contribution.json"),
) -> None:
    """Write an anonymous benchmark contribution to review and, if you choose, share.

    Holds rates, ranges, grades and size bands only: no names, ticket text or money.
    """
    import json

    from probative.benchmark import contribution

    if results is None and audit_file is None:
        for candidate, kind in ((Path("out/results.json"), "results"), (Path("out/audit/audit.json"), "audit")):
            if candidate.exists():
                if kind == "results":
                    results = candidate
                else:
                    audit_file = candidate
    if results is None and audit_file is None and closures_file is None and reconciliation_file is None:
        typer.secho("Give --results, --audit, --closures or --reconciliation (JSON files from the reports).", fg="red")
        raise typer.Exit(2)

    def load(p: Path | None) -> dict | None:
        return json.loads(p.read_text()) if p else None

    body = contribution(
        load(results),
        load(audit_file),
        closures=load(closures_file),
        reconciliation=load(reconciliation_file),
        platform=platform,
    )
    out.write_text(json.dumps(body, indent=2))
    typer.echo(json.dumps(body, indent=2))
    typer.secho(f"\nWrote {out}. Review it; nothing is sent anywhere.", bold=True)
    typer.echo("To join the benchmark, share this file with Probative Labs. See docs/benchmarks.md.")


@bakeoff_app.command("plan")
def bakeoff_plan(
    teams_file: Annotated[Path, typer.Argument(help="CSV with a team_id column (and optional strata).")],
    vendors: Annotated[str, typer.Option(help="Vendor names, comma-separated.")],
    start: Annotated[str, typer.Option(help="Trial start date, YYYY-MM-DD.")],
    weeks: Annotated[int, typer.Option(help="Trial length in weeks.")] = 8,
    control: Annotated[float, typer.Option(help="Share of teams kept as a control.")] = 0.25,
    stratify: Annotated[str | None, typer.Option(help="Column to balance arms on, e.g. parent_unit.")] = None,
    effect: Annotated[float, typer.Option(help="Smallest effect worth detecting, e.g. -0.10.")] = -0.10,
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Calibrate detectability from this project's history.")
    ] = None,
    seed: Annotated[int | None, typer.Option(help="Randomization seed (recorded in the output).")] = None,
    sims: Annotated[int, typer.Option(help="Power simulations.")] = 200,
    out: Annotated[Path, typer.Option("--out", "-o")] = Path("bakeoff.csv"),
) -> None:
    """Assign teams to vendors and a control group by lottery, and check each vendor can be measured."""
    from probative.bakeoff import CONTROL, plan_bakeoff

    teams = pd.read_csv(teams_file, dtype=str)
    if S.TEAM not in teams.columns:
        typer.secho(f"{teams_file} needs a {S.TEAM} column", fg="red")
        raise typer.Exit(2)
    names = [v.strip() for v in vendors.split(",") if v.strip()]
    seed = seed if seed is not None else secrets.randbelow(2**31)
    try:
        plan = plan_bakeoff(
            teams,
            names,
            first_start=date.fromisoformat(start),
            weeks=weeks,
            control_share=control,
            stratify_by=stratify,
            seed=seed,
        )
    except ValueError as exc:
        typer.secho(str(exc), fg="red")
        raise typer.Exit(2) from exc
    plan.to_csv(out, index=False)
    counts = plan["arm"].value_counts()
    typer.echo(counts.to_string())
    noise = _noise_from_history(_load(config)) if config else NoiseModel()
    n_control = int(counts.get(CONTROL, 0))
    typer.secho(f"\nChance of detecting {effect:+.0%} for each vendor against control after {weeks} weeks:", bold=True)
    for name in names:
        n_arm = int(counts.get(name, 0))
        rows = power_table(
            team_options=[n_arm + n_control],
            post_options=[weeks],
            n_waves=1,
            weeks_pre=26,
            effect=effect * noise.mean,
            noise=noise,
            wave_spacing=1,
            holdout_share=n_control / (n_arm + n_control),
            sims=sims,
        )
        mark = "" if rows[0].power >= 0.8 else "  (below 80%: add teams or weeks)"
        typer.echo(f"  {name}: {rows[0].power:.0%}, smallest detectable {rows[0].mde_relative:.1%}{mark}")
    typer.echo(f"\nWrote {out} (seed {seed}). Add a bakeoff section to probative.yaml, then run the trial.")


@bakeoff_app.command("analyze")
def bakeoff_analyze(
    config: ConfigOpt = Path("probative.yaml"),
    reps: Annotated[int | None, typer.Option(help="Override bootstrap draws.")] = None,
    out: Annotated[Path, typer.Option("--out", "-o", help="Output directory.")] = Path("out") / "bakeoff",
    open_report: Annotated[bool, typer.Option("--open", help="Open the decision pack.")] = False,
) -> None:
    """Measure every vendor against the control teams and write the decision pack."""
    from probative.bakeoff import run_bakeoff
    from probative.report import write_bakeoff_pack

    cfg = _load(config)
    if cfg.bakeoff is None:
        typer.secho("probative.yaml has no bakeoff section; see docs/bakeoff.md.", fg="red")
        raise typer.Exit(2)
    try:
        result = run_bakeoff(cfg, reps=reps, progress=_say)
    except (ValueError, FileNotFoundError) as exc:
        typer.secho(f"Bake-off failed: {exc}", fg="red")
        raise typer.Exit(1) from exc
    out_dir = out if out.is_absolute() else cfg.base_dir / out
    paths = write_bakeoff_pack(result, out_dir)
    typer.secho(result["decision"]["text"], bold=True)
    typer.echo(f"\nDecision pack: {paths['html']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


@app.command()
def validate(
    runs: Annotated[int, typer.Option(help="Simulated datasets per planted effect.")] = 10,
    reps: Annotated[int, typer.Option(help="Bootstrap draws per run.")] = 99,
    effects: Annotated[str, typer.Option(help="Planted relative effects, comma-separated.")] = "-0.15,0",
    seed: Annotated[int, typer.Option()] = 1,
    out: Annotated[Path | None, typer.Option(help="Write per-run results to this CSV.")] = None,
    audit_check: Annotated[
        bool, typer.Option("--audit", help="Validate the resolution audit instead of the effect estimator.")
    ] = False,
    rates: Annotated[
        str, typer.Option(help="With --audit: planted shares of false resolutions, comma-separated.")
    ] = "0,0.12,0.3",
    watch_check: Annotated[
        bool, typer.Option("--watch", help="Validate the regression watch: false alarms and detection.")
    ] = False,
    closures_check: Annotated[bool, typer.Option("--closures", help="Validate the action audit.")] = False,
    bakeoff_check: Annotated[bool, typer.Option("--bakeoff", help="Validate the bake-off decision.")] = False,
) -> None:
    """Check the estimator (or, with --audit, the resolution audit) against planted truths, including zero."""
    from probative import validation as V
    from probative.validation import (
        run_audit_validation,
        run_validation,
        run_watch_validation,
        summarize,
        summarize_audit,
        summarize_watch,
    )

    for flag, run_f, sum_f, title, note in (
        (
            closures_check,
            V.run_closure_validation,
            V.summarize_closures,
            "Action audit validation",
            "coverage should be near 0.95; flags_excess near 0.025 at a planted rate of 0 and near 1 otherwise.",
        ),
        (
            bakeoff_check,
            V.run_bakeoff_validation,
            V.summarize_bakeoff,
            "Bake-off validation",
            "clear_winner should be high when A is better and low when they are equal; wrong_clear_winner near 0.",
        ),
    ):
        if flag:
            df = run_f(runs=runs, reps=max(reps, 99), seed=seed, progress=_say)
            if out:
                df.to_csv(out, index=False)
            typer.secho(f"\n{title}", bold=True)
            with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 140):
                typer.echo(sum_f(df).to_string(index=False))
            typer.echo(f"\n{note}")
            return
    if watch_check:
        df = run_watch_validation(runs=runs, reps=max(reps, 99), seed=seed, progress=_say)
        if out:
            df.to_csv(out, index=False)
        typer.secho("\nRegression watch validation", bold=True)
        with pd.option_context("display.float_format", "{:.3f}".format, "display.width", 140):
            typer.echo(summarize_watch(df).to_string(index=False))
        typer.echo("\nregression_alarm should be near 0 with no regression and near 1 with one.")
        return
    if audit_check:
        df = run_audit_validation(
            rates=tuple(float(r) for r in rates.split(",")), runs=runs, reps=max(reps, 99), seed=seed, progress=_say
        )
        if out:
            df.to_csv(out, index=False)
        typer.secho("\nResolution audit validation", bold=True)
        with pd.option_context("display.float_format", "{:.4f}".format, "display.width", 140):
            typer.echo(summarize_audit(df).to_string(index=False))
        typer.echo(
            "\ncoverage should be near 0.95; flags_excess should be near 0.025 when the planted rate is 0 "
            "and near 1 otherwise."
        )
        return

    df = run_validation(
        effects=tuple(float(e) for e in effects.split(",")), runs=runs, reps=reps, seed=seed, progress=_say
    )
    if out:
        df.to_csv(out, index=False)
    s = summarize(df)
    typer.secho("\nValidation summary", bold=True)
    with pd.option_context("display.float_format", "{:.4f}".format, "display.width", 140):
        typer.echo(s.to_string(index=False))
    typer.echo(
        "\ncoverage should be near 0.95; rejects_zero should be near 0.05 when the planted effect is 0 "
        "and near 1 otherwise."
    )


if __name__ == "__main__":
    app()
