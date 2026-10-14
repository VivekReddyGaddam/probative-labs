"""Auto-generated technical appendix for data teams and auditors."""

from __future__ import annotations


def appendix_sections(r: dict) -> list[dict]:
    lin = r.get("lineage", {})
    boot = lin.get("bootstrap", {})
    design = r.get("design", {})
    window = r.get("window", {})
    unit = r.get("playbook", {}).get("unit", "unit of work")
    units = r.get("playbook", {}).get("units", "units of work")
    reps = boot.get("reps", "?")
    level = boot.get("level", 0.95)
    sections = [
        {
            "title": "Estimator",
            "paras": [
                "Effects are estimated with the imputation estimator for staggered adoption "
                "(Borusyak, Jaravel and Spiess, Review of Economic Studies, 2024). Team and week effects are "
                "fitted by weighted least squares using only team-weeks without the AI (comparison teams, "
                "and AI teams before their rollout). For every team-week with the AI, the fitted model "
                "predicts the outcome it would have had without it; the effect is the observed minus the "
                "predicted outcome. Effects are averaged with weights equal to the number of "
                f"{units} in each team-week, so the headline is an effect per {unit}.",
                "Because AI team-weeks are only ever compared with untreated data, the estimator avoids the "
                "bias of ordinary two-way fixed-effects regressions under staggered rollouts with effects "
                "that change over time. A single rollout date with a comparison group reduces to "
                "two-group difference-in-differences.",
            ],
            "items": [
                f"Design: {design.get('label', 'n/a')}",
                f"Teams analysed: {design.get('n_teams', 'n/a')} "
                f"({design.get('n_treated_teams', 'n/a')} with the AI, "
                f"{design.get('n_never_treated', 'n/a')} without it in the window; "
                f"{design.get('n_cohorts', 'n/a')} rollout wave(s))",
                f"Window: {window.get('start')} to {window.get('end')} ({window.get('weeks')} weeks)",
                "Rollout weeks that start mid-week are dropped for the team concerned (partly exposed).",
            ],
        },
        {
            "title": "Uncertainty",
            "paras": [
                f"Ranges come from a bootstrap that resamples whole teams ({reps} draws, seed "
                f"{boot.get('seed', '?')}), stratified by rollout wave so every draw keeps the design. "
                f"Intervals are percentile intervals at the {level:.0%} level. Tickets within a team are not "
                "independent, so resampling teams, not tickets, gives honest uncertainty.",
            ],
            "items": [],
        },
        {
            "title": "Work typing and mix adjustment",
            "paras": [
                f"Each {unit} is typed from its category fields (and any custom rules). Types below the "
                "configured share of baseline volume are folded into 'Other'. Mix-adjusted outcomes subtract "
                "each type's baseline mean (computed on untreated team-weeks only) before averaging, so the "
                f"engine compares {units} only with {units} of the same type. The headline (unadjusted) "
                "estimate is always reported beside it.",
                "The descriptive before/after table reweights each period's per-type means to the AI teams' "
                "before-rollout mix; it has no comparison group and is not causal.",
            ],
            "items": [],
        },
        {
            "title": "Diagnostics",
            "paras": [],
            "items": [
                "Pre-trend: lead coefficients for the weeks before rollout are estimated on untreated "
                "team-weeks with team and week effects; their joint Wald test uses the bootstrap covariance. "
                "Fail if p < 0.05.",
                "Placebo dates: data after each team's real rollout is removed and the rollout is moved earlier; "
                "fail if the placebo range excludes zero.",
                "Placebo outcomes: metrics the AI cannot affect (e.g. share of tickets opened after hours) are "
                "estimated with the same design; fail if the range excludes zero.",
                "Leave-one-team-out: the estimate is recomputed dropping each AI team; fail if one team flips "
                "the sign of a significant effect or moves it by more than half.",
                "Category drift: Jensen-Shannon distance of the category mix before and after rollout, AI teams "
                "versus comparison teams; fail if categories appear or vanish.",
                "Power: minimum detectable effect = 2.80 × standard error (two-sided 5% test, 80% power).",
            ],
        },
        {
            "title": "Quality guardrails",
            "paras": [
                "Each guardrail is estimated with the same design (type-adjusted). Pass: the upper end of the "
                "harm range is below the threshold. Breach: the harm exceeds the threshold and its range "
                "excludes zero. Watch: otherwise. Any breach withholds the program's value. The false "
                "deflection rate is judged on its level (Wilson interval) against its threshold.",
            ],
            "items": [],
        },
        {
            "title": "Value and ROI",
            "paras": [
                "Capacity value = (agent hours saved + verified deflections × baseline hours for that type − "
                "false deflections × extra hours) × loaded hourly cost. Cash value = capacity value × "
                "realization share + documented realized savings. ROI = (value − total cost) ÷ total cost.",
                "Hours saved enter through their team-bootstrap draws; each customer assumption enters as a "
                "triangular distribution (low, most likely, high). The simulation reports the 10th, 50th and "
                "90th percentiles. Payback = one-time costs ÷ (monthly value − monthly recurring cost).",
                "Deflection counts only when the evidence grade counts, no guardrail breached, and ticket "
                "volume per team fell measurably for AI teams.",
            ],
            "items": [],
        },
        {
            "title": "Evidence grades",
            "paras": [],
            "items": [
                "A: randomized holdout or randomized wave order, registered before go-live, diagnostics pass.",
                "B: staggered rollout or comparison group with pre-trend and placebo tests passed.",
                "C: valid but weaker (e.g. fewer than 12 teams; synthetic control and interrupted time series "
                "from v2): counts, flagged as weaker.",
                "D: diagnostics failed: indicative only, never added to totals.",
                "E: vendor estimates, surveys and self-reported time: shown as claimed, never added.",
            ],
        },
        {
            "title": "Data handling",
            "paras": [],
            "items": [
                "Person and record identifiers are replaced at ingest with keyed HMAC-SHA256 pseudonyms "
                f"({', '.join(lin.get('pseudonymized_fields', [])) or 'none present'}).",
                "Free text is used only for work typing and dropped before analysis.",
                "Teams with fewer people than the minimum group size are left out; results are team-level only.",
                "Trailing weeks with too many unresolved records are dropped (right-censoring guard).",
                "Exclusions: " + ("; ".join(f"{k}: {v:,}" for k, v in (lin.get("exclusions") or {}).items()) or "none"),
            ],
        },
        {
            "title": "Reproduce this result",
            "paras": [
                "Run `truelift analyze -c truelift.yaml` with the same data files, configuration and "
                "pseudonymization key. Results are deterministic given the seed.",
            ],
            "items": [
                f"TrueLift {lin.get('code', {}).get('truelift')} (git {lin.get('code', {}).get('git') or 'n/a'}), "
                f"Python {lin.get('code', {}).get('python')}, numpy {lin.get('code', {}).get('numpy')}, "
                f"pandas {lin.get('code', {}).get('pandas')}",
                f"Configuration SHA-256: {lin.get('config_sha256')}",
                f"Registration SHA-256: {lin.get('registration_sha256') or 'not registered'}",
            ]
            + [
                f"{f['path'].split('/')[-1]}: {f['rows']:,} rows, SHA-256 {f['sha256'][:16]}…"
                for f in lin.get("files", [])
            ],
        },
    ]
    return sections
