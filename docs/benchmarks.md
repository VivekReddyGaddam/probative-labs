# Benchmarks

One company's result cannot say whether it is typical, and AI vendors do not publish how their products perform across customers. Benchmarks answer "is ours normal?" without anyone sharing data.

## What a contribution contains

`probative benchmark` turns any of your results (evidence pack, resolution audit, action audit, bill reconciliation) into a small JSON file:

```bash
probative benchmark --results out/results.json --audit out/audit/audit.json \
    --closures out/closures/closures.json --reconciliation out/reconcile/reconciliation.json --platform servicenow
```

| Field | Example | Notes |
| --- | --- | --- |
| `month` | `2026-10` | Month only |
| `function`, `vendor`, `tool`, `platform` | `it_service_management`, `Example vendor`, `Agent assist`, `servicenow` | As written in `probative.yaml`. Edit or remove them before sharing if you prefer |
| `effect.design`, `effect.grade` | `staggered`, `B` | |
| `effect.relative` and its range | `-0.12` (−12%) | Change in the primary metric |
| `effect.resolution_headline_relative`, `effect.resolution_mix_adjusted_relative` | `+0.02`, `-0.07` | The deflection trap, in two numbers |
| `effect.guardrails` | `{"reopen_rate": "pass"}` | Status only |
| `effect.teams_band`, `effect.weekly_tickets_band` | `20–49`, `500–1,999` | Bands, never exact counts |
| `resolution_audit.failed_share` and its range | `0.12` | Claimed resolutions that did not hold |
| `resolution_audit.confidence`, `claims_band` | `high`, `1,000–9,999` | |
| `closure_audit.failed_share` and its range | `0.08` | AI closures that did not stick (`--closures`) |
| `billing.billing_error_share` | `0.02` | Share of billed items with billing errors (`--reconciliation`); no amounts |
| `sha256` | | Fingerprint of the contents |

It never contains company, program, team or person names, ticket text or identifiers, money amounts (costs, prices, ROI in currency), exact dates or exact volumes.

## How to contribute

1. Run `probative benchmark` and read the file. Nothing is sent anywhere.
2. Remove anything you would rather not share. Fields are optional.
3. Share it with Probative Labs, for example as part of a pilot. Cloud customers can opt in from the Benchmarks page instead.

## Rules

- A benchmark figure is shown only when **at least five organizations** have contributed for the same product and measure.
- Figures are shown as a median and a middle range (25th to 75th percentile), never as individual contributions.
- Contributions are never sold, and vendors cannot buy, preview or suppress benchmark figures. See [INDEPENDENCE.md](../INDEPENDENCE.md).
- Contributors see the benchmarks they contributed to.

## Status

The benchmark is open for contributions. No figures are published yet, because no product has five contributing organizations.
