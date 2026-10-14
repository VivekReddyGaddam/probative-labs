# Validation

A causal claim that turns out wrong destroys trust in the method, so the engine is checked the way it is used. Each check runs the full pipeline, from CSV export to evidence pack, on simulated service desks where the true effect is known. One of the planted effects is zero.

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

## How the method was corrected during validation

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
