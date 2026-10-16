# Validation

A wrong claim destroys trust in an independent check, so the engine is checked the way it is used. Each check runs the full pipeline, from CSV export to evidence pack, on simulated service desks where the true effect is known. One of the planted effects is zero.

Reproduce these numbers with:

```bash
probative validate --runs 30 --reps 199 --effects=-0.15,-0.05,0
```

## The test scenario

Every simulated dataset (`probative.simulate`) has:

- **30 teams over 60 weeks,** with a randomized rollout in three waves at weeks 30, 36 and 42. A quarter of the teams never get the AI.
- **Team differences.** Teams differ in volume, speed and ticket mix, and the AI's effect is proportional to each team's own speed and ticket types. That makes effects heterogeneous: slower teams save more hours.
- **A learning curve.** The effect builds up over the first six weeks after rollout.
- **Deflection.** Self-service absorbs password resets and simple requests, pushing the headline average up. 12% of deflections come back as tickets within the week.
- **Seasonality.** An onboarding spike in September and a year-end dip affect all teams.
- **Messy records.** Duplicate rows, impossible timestamps and missing assignment groups.

"Truth" is the planted expected change in agent hours per ticket for the tickets the AI teams handled.

## Results (Probative Labs 0.1.0, 30 datasets per planted effect)

| Planted effect | Mean truth (h per ticket) | Bias | RMSE | 95% range covers truth | Effect declared (range excludes zero) | Pre-trend test passes | Placebo dates pass | Graded A to C |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| −15% | −0.114 | −0.002 | 0.013 | 87% | 100% | 100% | 100% | 100% |
| −5% | −0.038 | +0.002 | 0.010 | 100% | 87% | 100% | 100% | 100% |
| 0 | 0.000 | −0.000 | 0.010 | 100% | 0% | 97% | 97% | 93% |

### What this shows

- **Point estimates are unbiased.** Bias is under 2% of the effect in every scenario, despite deflection pushing the headline average the wrong way.
- **No false discoveries.** With no real effect, no run declared one.
- **Real effects are found.** A −15% effect was detected every time, a −5% effect 87% of the time.
- **Diagnostics rarely cry wolf.** Valid analyses kept a countable grade in 93% to 100% of runs. Most of the remaining downgrades are false alarms, which are expected given the diagnostics' 5% family-wise error rate.
- **Known limitation: ranges for large, heterogeneous effects are somewhat narrow.** At the −15% planted effect, the 95% range covered the truth in 87% of runs. In this scenario, the true sampling spread is about 1.2 times what the team bootstrap and jackknife estimate. The cause is that effects scale with each team's own speed while deflection shifts each team's ticket mix. At smaller effects the ranges are conservative (100% coverage).

  Until this is closed, treat the edges of a range for a large effect as somewhat optimistic. We are evaluating an analytic variance estimator (Borusyak, Jaravel and Spiess, 2024) and a studentized bootstrap. Results will be published here before any change ships.

## Resolution audit

The audit is checked the same way: simulated service desks where a known share of the virtual agent's resolutions are false, so those people come back with a ticket within the week. One of the planted shares is zero. Runs alternate between ServiceNow-style exports (issues typed by category and subcategory) and Zendesk-style exports (category only).

Reproduce with:

```bash
probative validate --audit --rates 0,0.12,0.3 --runs 60 --reps 199
```

### Results (Probative Labs 0.2.0, 60 datasets per planted share, about 2,400 checked claims each)

| Planted share that did not hold | Bias | RMSE | 95% range covers truth | Excess reported (range above zero) | Audit confidence high |
| --- | --- | --- | --- | --- | --- |
| 0% | +0.0002 | 0.003 | 100% | 0% | 100% |
| 12% | +0.0004 | 0.007 | 98% | 100% | 100% |
| 30% | −0.0012 | 0.010 | 93% | 100% | 100% |

### What this shows

- **Unbiased.** Estimates are within 0.2 percentage points of the planted share on average.
- **No false accusations.** With no false resolutions planted, the audit never reported an excess.
- **Calibrated ranges.** Coverage is 93% to 100%. A separate check of 100 Zendesk-style runs at 12% gave 95% coverage, with the reported uncertainty slightly wider than the true spread.

### How the audit was corrected during validation

1. **Baseline timing.** The first version compared claims with human-resolved tickets from the whole period. Claims cluster later, when the bot deflects more, so the baseline came out slightly high and the estimate slightly low (−0.1 points with nothing planted). The baseline is now standardized by month as well as issue type.
2. **Ranges.** Resampling people alone gave 85% to 93% coverage. Months are shared by everyone in them, so the range is now widened to a leave-one-month-out jackknife when that is larger.

## Bill reconciliation

On simulated bills with known errors (1% of resolutions billed twice, 0.5% unknown IDs, 2% of escalations billed), every planted duplicate, unknown ID and billed escalation was found (`tests/test_billing.py`). Hand-built cases check each reason, the contract definition and reconciliation by day.

## Regression watch

Simulated desks where the share of the AI's resolutions that do not hold rises from 12% to 24% six weeks before the end of the data, and desks with no change. Reproduce with `probative validate --watch --runs 60`.

| Scenario | Runs | Regression alarm | Watch or alarm | Change dated within a week |
| --- | --- | --- | --- | --- |
| No change | 60 | 1.7% | 16.7% | n/a |
| 12% to 24%, six weeks before the end | 60 | 100% | 100% | 100% |

## Action audit

Simulated desks where the AI agent closes 40% of AI-assisted easy tickets by itself, and a planted share of its closures gets reopened. Reproduce with `probative validate --closures --runs 40`.

| Planted share that did not stick | Runs | Bias | RMSE | 95% range covers truth | Excess reported |
| --- | --- | --- | --- | --- | --- |
| 0% | 40 | −0.001 | 0.004 | 97.5% | 0% |
| 10% | 40 | −0.001 | 0.009 | 85% | 100% |
| 25% | 40 | 0.000 | 0.011 | 92.5% | 100% |

The 85% at 10% looked low, so 80 more runs were made at that rate with another seed: coverage 94%, and the reported uncertainty was 0.96 of the actual spread. The first batch's shortfall was chance.

## Bake-off

Simulated desks with no AI, then a 10-week trial on 30 teams: about 10 per vendor and 10 control teams. Reproduce with `probative validate --bakeoff --runs 20`.

| Scenario | Runs | Clear winner named | Correct vendor | Wrong clear winner |
| --- | --- | --- | --- | --- |
| Vendor A −20%, vendor B −5% | 20 | 100% | 100% | 0% |
| Both −10% | 20 | 5% | n/a | 5% |

## How the effect estimator was corrected during validation

Validation changed the method before release:

1. **Pre-trend test.** A joint test of eight lead coefficients with a bootstrap covariance rejected far too often with about 30 teams (it passed only 83% of runs at zero effect). It now uses a team-clustered covariance with a Hotelling T² small-sample correction, and passes 97% to 100% of runs.
2. **Family-wise diagnostics.** Testing pre-trends, placebo dates and placebo outcomes each at 5% downgraded about one valid program in seven. They now share one 5% budget.
3. **Small-sample ranges.** Percentile intervals from resampling about 7 teams per wave were too narrow. Ranges now use a t critical value, the per-wave n/(n − 1) correction, and the larger of the bootstrap and delete-one-team jackknife spread.
4. **The simulator itself.** An early version moved returning tickets to random times of day. The placebo-outcome diagnostic flagged this correctly as a real change in when work arrived, and it was fixed in the simulator.

## Unit tests

`tests/test_engine.py` includes deterministic checks:

- recovery of a planted effect;
- a zero effect inside its range;
- no bias when effects grow every week, where ordinary two-way fixed effects would be biased;
- detection of anticipation by the pre-trend test;
- placebo and leave-one-team-out behavior;
- design selection and grading rules.

`tests/test_billing.py`, `tests/test_watch.py`, `tests/test_closures.py`, `tests/test_bakeoff.py` and `tests/test_reproduce.py` cover the bill reconciliation, the regression watch, the action audit, the bake-off and re-performance.

`tests/test_audit.py` checks the resolution audit:

- the failed share equals the excess return rate on hand-built data, with billing and held counts;
- recent, out-of-range and incomplete claims are counted but not checked;
- claims without a category fall back to matching on the person, and say so;
- claims from a system that identifies people differently get low confidence;
- planted shares of false resolutions (25% and 0%) are recovered from simulated exports, end to end;
- the measurement plan reports "no number" when everyone would get the AI at once;
- benchmark contributions contain no names, identifiers or money.
