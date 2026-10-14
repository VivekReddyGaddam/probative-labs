"""Command-line interface: ``truelift --help``."""

from __future__ import annotations

import secrets
import webbrowser
from datetime import date
from pathlib import Path
from typing import Annotated

import pandas as pd
import typer
import yaml

from truelift import __version__
from truelift import schema as S
from truelift.config import ProjectConfig
from truelift.guardrails import GuardrailSpec
from truelift.ingest import Pseudonymizer, apply_mapping, check_work_units, read_tables, resolve_mapping
from truelift.pipeline import AnalysisError, analyze
from truelift.playbooks import get_playbook
from truelift.report import write_pack
from truelift.rollout import NoiseModel, lock_registration, noise_from_units, plan_waves, power_table

app = typer.Typer(
    help="TrueLift: measure what AI actually changed, with graded, reproducible evidence.",
    no_args_is_help=True,
    add_completion=False,
)
plan_app = typer.Typer(help="Rollout designer: plan measurement before go-live.", no_args_is_help=True)
app.add_typer(plan_app, name="plan")

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
        typer.secho(f"No configuration at {config}. Run `truelift init` or `truelift demo` first.", fg="red")
        raise typer.Exit(2)
    return ProjectConfig.load(config)


@app.callback(invoke_without_command=True)
def _main(version: Annotated[bool, typer.Option("--version", help="Show the version.")] = False) -> None:
    if version:
        typer.echo(f"truelift {__version__}")
        raise typer.Exit()


@app.command()
def demo(
    directory: Annotated[Path, typer.Argument(help="Where to write the demo project.")] = Path("truelift-demo"),
    effect: Annotated[float, typer.Option(help="Planted relative effect on agent hours per ticket.")] = -0.15,
    reopen_effect: Annotated[float, typer.Option(help="Planted change in reopen probability (try 0.03).")] = 0.0,
    reps: Annotated[int, typer.Option(help="Bootstrap draws.")] = 199,
    seed: Annotated[int, typer.Option()] = 7,
    open_report: Annotated[bool, typer.Option("--open", help="Open the HTML evidence pack.")] = False,
) -> None:
    """Simulate a service desk with a known AI effect and measure it end to end."""
    from truelift.simulate import SimSpec, demo_config, generate

    typer.secho(f"Simulating a 40-team service desk (planted effect {effect:+.0%}) in {directory}/", bold=True)
    sim = generate(SimSpec(effort_effect=effect, reopen_effect=reopen_effect, seed=seed))
    sim.write(directory / "data")
    raw = demo_config("data", sim.truth)
    raw["analysis"]["bootstrap_reps"] = reps
    cfg_path = directory / "truelift.yaml"
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
    t = sim.truth
    typer.echo("")
    typer.secho(result["headline"], bold=True)
    typer.echo(
        f"\nPlanted truth: {t['effort_att_hours_per_ticket']:+.3f} h per ticket "
        f"({t['effort_relative']:+.1%} after the learning curve). "
        f"Estimate: {result['primary']['estimate']:+.3f} h "
        f"[{result['primary']['ci_low']:+.3f}, {result['primary']['ci_high']:+.3f}]."
    )
    typer.echo(f"\nEvidence pack: {paths['html']}\nMarkdown:      {paths['markdown']}\nResults JSON:  {paths['json']}")
    if open_report:
        webbrowser.open(paths["html"].resolve().as_uri())


TEMPLATE = """\
# TrueLift project: one file per AI program. Docs: docs/configuration.md
program:
  id: my-ai-program
  name: Now Assist for ITSM
  vendor: ServiceNow
  tool: Now Assist
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
  mapping_preset: {preset}             # canonical | servicenow
  mapping: {{}}                          # override: canonical_field: source_column
  exposure: data/rollout.csv          # team_id, start_date[, end_date, kind, randomized]
  self_service: null                  # optional: virtual agent sessions
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
"""


@app.command()
def init(
    directory: Annotated[Path, typer.Argument()] = Path("."),
    preset: Annotated[str, typer.Option(help="Field mapping preset: servicenow or canonical.")] = "servicenow",
) -> None:
    """Write a starter truelift.yaml."""
    path = directory / "truelift.yaml"
    if path.exists():
        typer.secho(f"{path} already exists; not overwriting.", fg="yellow")
        raise typer.Exit(1)
    directory.mkdir(parents=True, exist_ok=True)
    path.write_text(TEMPLATE.format(preset=preset))
    typer.echo(f"Wrote {path}. Edit the data paths, then run `truelift health`.")


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
) -> None:
    """Write synthetic ServiceNow-style exports with a planted effect."""
    from truelift.simulate import SimSpec, generate

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
    paths = sim.write(out)
    for k, p in paths.items():
        typer.echo(f"{k:9s} {p}")


@app.command()
def health(config: ConfigOpt = Path("truelift.yaml")) -> None:
    """Check data quality and field mapping before analysis."""
    cfg = _load(config)
    raw, files, warnings = read_tables([cfg.resolve(p) for p in cfg.data.work_units])
    mapping = resolve_mapping(cfg.data.mapping_preset, cfg.data.mapping)
    units, rep = apply_mapping(raw, mapping, S.WORK_UNIT_COLUMNS, required=S.REQUIRED_WORK_UNIT_COLUMNS)
    pseud = Pseudonymizer.from_env_or_file(cfg.base_dir / ".truelift" / "pseudonym.key")
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
    config: ConfigOpt = Path("truelift.yaml"),
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
    config: ConfigOpt = Path("truelift.yaml"),
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
    typer.echo(f"\nWrote {out} (seed {seed}). Register the analysis now: `truelift register`.")


@app.command()
def validate(
    runs: Annotated[int, typer.Option(help="Simulated datasets per planted effect.")] = 10,
    reps: Annotated[int, typer.Option(help="Bootstrap draws per run.")] = 99,
    effects: Annotated[str, typer.Option(help="Planted relative effects, comma-separated.")] = "-0.15,0",
    seed: Annotated[int, typer.Option()] = 1,
    out: Annotated[Path | None, typer.Option(help="Write per-run results to this CSV.")] = None,
) -> None:
    """Check the estimator against planted effects, including zero."""
    from truelift.validation import run_validation, summarize

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
