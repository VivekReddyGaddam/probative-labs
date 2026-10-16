# Regression watch

Bought AI changes underneath you: vendors update models, prompts and knowledge sources, often without notice. `probative watch` checks your own systems for the effect, so it works without access to the vendor's logs.

```bash
probative watch --events vendor-releases.csv --open
```

It exits with code 3 when it finds a regression, so it can run on a schedule (cron, CI or Probative Labs Cloud) and alert someone.

## What it checks

- **Claimed resolutions that did not hold.** As in the resolution audit, against your agents' resolutions in the same weeks and issue types, so a busy or quiet week cannot pose as a change in the AI. The latest 4 weeks are compared with the 12 before them.
- **Conversations the AI resolved.** The share of conversations it resolves, if the export has outcomes. A falling share is a regression too.
- **Volume.** Conversations per week, for context.

A **regression** is a worsening of at least 2 percentage points (`--min-change`) whose 95% range excludes zero. A worsening of that size without a firm range is **watch**. The two windows are bootstrapped separately, which overstates the noise slightly when the same people appear in both: alarms are conservative.

## Known release dates

Give a CSV with a `date` column (and optionally `name`). Each release is tested on the four weeks before against the four weeks after, with a Bonferroni correction across releases. The report also shows the week with the largest step up, as a description, not a test.

## Validation

On 120 simulated service desks, `probative validate --watch` found:

| Scenario | Regression alarm | Change dated within a week |
| --- | --- | --- |
| No change | 1.7% (1 of 60) | n/a |
| Share that did not hold rises from 12% to 24%, six weeks before the end | 100% | 100% |
