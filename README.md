# Probative Labs

**Measure what AI actually changed.** Probative Labs compares real work outcomes with what would have happened without the AI, checks that quality held, and turns the difference into an ROI *range* with an evidence grade that a CFO can defend.

This repository is the open-source measurement engine. v1 covers the IT service desk.

```
$ probative demo
Now Assist for ITSM (simulated) changed agent hours per ticket by -0.11 h per ticket (-12%; 95% range
-0.13 to -0.09 h, -13% to -10%). Evidence grade B. Guardrails: all pass. Cash ROI 5% to 77% (P10 to P90).

Planted truth: -0.111 h per ticket (-12.3% after the learning curve). Estimate: -0.110 h [-0.127, -0.095].
```

## Why

Most AI ROI figures are estimates: usage × assumed minutes saved × an hourly rate. Careful studies show the same kind of tool can be strongly positive, zero or negative, and people cannot tell which by feel. In [METR's randomized trial](https://arxiv.org/abs/2507.09089), experienced developers took 19% longer with AI while believing they were 20% faster.

Probative Labs answers four questions for every AI program, the same way every time:

1. **Did it change outcomes?** A comparison-group design estimates what would have happened anyway.
2. **By how much, and how sure are we?** Every effect has a range from resampling whole teams, plus diagnostics and an evidence grade (A to E).
3. **Did quality hold?** Guardrails (reopens, escalations, satisfaction, false deflection) can block the value entirely.
4. **Was it worth what it cost?** Capacity ROI and cash ROI are reported separately, as P10/P50/P90 ranges.

## Quickstart

Probative Labs needs Python 3.12 or newer.

```bash
# with uv (recommended)
uv tool install git+https://github.com/VivekReddyGaddam/probative-labs
# or with pip
pip install "probative-labs @ git+https://github.com/VivekReddyGaddam/probative-labs"

probative demo --open
```

`probative demo` simulates a 40-team service desk with a known, planted AI effect. The data includes deflection, seasonality, a staggered randomized rollout and messy records. The demo then measures it end to end and opens the evidence pack, so you can check the estimate against the truth.

## Measure your own program

```bash
probative init                    # writes probative.yaml (ServiceNow field mapping by default)
probative health                  # data quality and field mapping report
probative plan power -c probative.yaml --effect -0.10   # is the rollout big enough to measure?
probative plan waves teams.csv --waves 3 --start 2026-11-02 --holdout 0.2
probative register                # lock hypothesis, metrics and guardrails before results exist
probative analyze --open          # evidence pack: HTML, Markdown and JSON
```

You need two files:

- **Work units:** a ticket export with ID, assignment group, opened and resolved timestamps, and ideally time worked, category, reopen count and survey score. ServiceNow list exports work as-is.
- **Exposure:** which team got the AI on which date. A spreadsheet is fine; `probative plan waves` writes one for you.

Virtual-agent sessions are optional and enable verified deflection. See [docs/data-model.md](docs/data-model.md) and [docs/configuration.md](docs/configuration.md).

## What you get: the evidence pack

One pack per program, in the twelve sections a finance or audit reviewer expects:

1. Headline: effect, range, grade, guardrail status, cash ROI range
2. The question as registered, and any deviations
3. Design: which comparison was used, and why stronger ones were not possible
4. Effects: headline and mix-adjusted side by side, by work type
5. Effect over time: an event-study chart, including the weeks before rollout
6. Quality guardrails
7. Diagnostics: pre-trend, placebo dates, placebo outcomes, leave-one-team-out, category drift, power
8. Value and ROI: capacity and cash, P10/P50/P90, payback, cost per hour released
9. Claimed versus proven: the vendor's number beside the measured one
10. Assumptions, with owner and date
11. Data lineage: file hashes, row counts, every exclusion and why
12. Recommendation: renew, expand, fix or stop, and the reason

## The method, briefly

- **Compare like with like.** Every ticket is typed. When AI absorbs the easy tickets, the average human ticket gets harder and headline resolution time rises even if every type got faster. Probative Labs reports the mix-adjusted effect beside the headline.
- **Compare against the right group.** Staggered rollouts are estimated with the imputation estimator (Borusyak, Jaravel and Spiess, 2024). Each AI team-week is compared only with data that had no AI, which avoids the known bias of ordinary two-way fixed effects.
- **Teams, not tickets, are the unit of uncertainty.** Ranges come from a bootstrap of whole teams, stratified by rollout wave, widened by a small-sample correction and a delete-one-team jackknife.
- **Check before you believe.** The pre-trend test, placebo rollout dates and placebo outcomes share a 5% family-wise error budget. Any failure drops the result to grade D: shown, never added to totals.
- **No design, no number.** If everyone got the AI on the same day and nobody was held back, Probative Labs says so instead of producing a figure.

Full method: [docs/methodology.md](docs/methodology.md). Evidence grades: [docs/evidence-grades.md](docs/evidence-grades.md). Validation on planted effects, including zero: [docs/validation.md](docs/validation.md).

## Privacy by design

- Person and record identifiers are replaced with keyed HMAC-SHA256 pseudonyms at ingest.
- Free text is used only to type work, then dropped.
- Results are team-level only, and teams below a minimum group size are left out. Probative Labs evaluates AI programs, never people.
- Everything runs locally. No data leaves your machine.

## Open source and Probative Labs Cloud

The measurement method and this engine are open source under Apache-2.0, so your data team or auditor can read, rerun and challenge any result. Probative Labs Cloud is the commercial product built on the same engine. It adds what an enterprise needs to run measurement across a whole AI portfolio: live connectors, a hosted portfolio view, the cost ledger, single sign-on and audit logs, renewal memos and board reports. See [OPEN_CORE.md](OPEN_CORE.md) for exactly what is where and why.

## Status and roadmap

v0.1 is alpha. It covers the IT service desk with staggered and two-group designs. Planned next:

- Randomized holdout analysis
- Synthetic control and interrupted time series
- Effect breakdowns by tenure
- Software engineering, customer service, security and finance playbooks

Breaking changes are possible before 1.0.

## Contributing

Issues and pull requests are welcome. Method changes must come with validation on simulated data; see [CONTRIBUTING.md](CONTRIBUTING.md). To report a security issue, see [SECURITY.md](SECURITY.md).

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE). "Probative Labs" is a trademark of its author; the license does not grant rights to the name.
