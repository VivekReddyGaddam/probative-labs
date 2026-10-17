# Methodology

Probative Labs checks what AI delivered, independently of the vendor. It answers two questions. **What changed?** It estimates the *counterfactual*, what would have happened to the same work without the AI, using the strongest comparison the data allows, and states the uncertainty, the diagnostics and an evidence grade with every number. **Did the AI's claims hold?** The resolution audit checks the resolutions an AI reports against what happened next. This document describes exactly what the open engine does. The code is the reference implementation; where this text and the code disagree, the code is a bug or this document is: please open an issue.

## The five steps

1. **Lock the question before results exist.** Hypothesis, primary metric, guardrails with thresholds, and the analysis window are written to a registration file with a timestamp and a SHA-256 hash (`probative register`). Every analysis lists deviations from it.
2. **Compare like with like.** Every unit of work is typed, and effects are estimated within type (mix adjustment).
3. **Compare against the right group.** A comparison-group design estimates what would have happened anyway.
4. **Check quality.** Speed that comes with more reopens, escalations or unhappy users does not count as value.
5. **State the uncertainty.** Every effect comes with a range, diagnostics and an evidence grade.

## Unit of analysis

Results are computed on **team-by-week cells**: for example, the mean agent hours per ticket that a team's tickets opened that week took. Teams are the unit of exposure (AI is rolled out by assignment group), the unit of uncertainty, and the unit of reporting. Teams with fewer distinct people than the minimum group size (default 5) are left out, so no result describes an individual.

A team's **rollout week** is the first full week after its exposure start date. If the start falls mid-week, that week is dropped for the team (it is partly exposed). Teams that had the AI before the window starts are excluded, since they have no before period. Exposure is treated as absorbing. If an exposure ends (`end_date`), the team's later weeks are dropped rather than treated as untreated.

## Comparison designs

| Design | When Probative Labs uses it | Evidence grade |
| --- | --- | --- |
| Staggered rollout | Teams got the AI on different dates (with or without a never-exposed group) | A if the wave order was randomized and pre-registered; otherwise B |
| Two-group difference-in-differences | One group got the AI on one date; comparable teams did not | A if randomized and pre-registered; otherwise B |
| None | Everyone got the AI on the same date with no holdout, or too few teams | No number |

Synthetic control, interrupted time series, randomized holdout analysis and matched comparison are on the roadmap. Until they ship, Probative Labs reports no number rather than a weaker one dressed up.

## Estimator

Probative Labs uses the **imputation estimator** of Borusyak, Jaravel and Spiess (2024, *Review of Economic Studies*):

1. Fit `y(team, week) = α(team) + λ(week)` by weighted least squares, weights = units of work in the cell, using **only cells without the AI**: comparison teams, plus AI teams before their rollout.
2. For each cell with the AI, impute the untreated outcome `ŷ0 = α̂(team) + λ̂(week)`.
3. The cell's effect is `τ = y − ŷ0`.
4. The headline effect per unit of work is the volume-weighted mean of `τ` over all AI cells. The total effect is `Σ τ × units`, for example agent hours saved.
5. The event-study view averages `τ` by weeks since rollout.

AI cells are never used as controls for other AI cells. This avoids the "forbidden comparisons" that bias ordinary two-way fixed-effects regressions when effects change over time (Goodman-Bacon 2021; de Chaisemartin and D'Haultfœuille 2020; Sun and Abraham 2021). The test suite includes a case with effects that grow every week; the estimator stays on target.

The fixed effects are fitted by alternating projections, which handles unbalanced panels without building dummy matrices.

## Mix adjustment

Each unit of work gets a type, from rules first, then from category fields. Types under 1% of baseline volume are folded into "Other", so the list stays stable.

For each metric, Probative Labs computes each type's **baseline mean** on untreated cells only. It then analyzes the residual `y − mean(type)` plus the overall baseline mean. The causal engine therefore compares password resets with password resets, whatever share of the work they make up.

Both views are reported:

- **Headline:** the raw metric.
- **Mix-adjusted:** the residualized metric.

When AI deflects easy tickets, the headline can rise while the mix-adjusted effect falls. That is the deflection trap, and the evidence pack shows it explicitly. A descriptive before/after table also reweights the AI teams' after-period per-type means to their before-period mix. It has no comparison group, so it is not causal; it shows why headline averages mislead.

Value uses the mix-adjusted total over the tickets actually handled, `Σ_k Δ_k × N_k`.

## Uncertainty

Ranges come from a **bootstrap of whole teams** (default 499 draws), stratified by rollout wave so that every draw keeps the design. Tickets within a team share a manager, a queue and a culture, so they are not independent; resampling teams respects that.

With few teams per wave, plain percentile intervals are too narrow. Three corrections widen the half-widths, keeping their asymmetry around the point estimate:

- the per-wave variance correction `n/(n − 1)`;
- the delete-one-team jackknife: if it finds more variance than the bootstrap, the bootstrap spread is widened to match;
- the ratio of the t critical value with (teams − 1) degrees of freedom to the normal one.

Standard errors and p-values use the same corrections, and so do the effect draws that feed the ROI simulation. [Validation](validation.md) shows the ranges are conservative for small effects and somewhat narrow for large, team-dependent effects.

Every effect that feeds value (agent hours saved) carries its bootstrap draws into the ROI simulation, so value ranges inherit the effect's uncertainty, including its correlation with the per-ticket estimate.

## Diagnostics

| Diagnostic | What it does | Fails when |
| --- | --- | --- |
| Pre-trend | Estimates lead coefficients for the 8 weeks before rollout on untreated cells (team and week effects partialled out). Joint test with a team-clustered covariance and a Hotelling T² small-sample correction. | p below the per-test threshold |
| Placebo dates | Drops all data after each team's real rollout and moves the rollout 8 weeks earlier (or half the median history) | The placebo range excludes zero |
| Placebo outcomes | Estimates an outcome the AI cannot affect (service desk: share of tickets opened outside business hours) with the same design | The range excludes zero |
| Leave-one-team-out | Re-estimates dropping each AI team in turn | One team flips the sign of a significant effect, or moves the estimate by more than half the effect (or, for a null effect, by more than its uncertainty) |
| Category drift | Jensen-Shannon distance between before and after category mixes, AI teams versus comparison teams | Categories appear or vanish after rollout, which signals a taxonomy or categorizer change |
| Power | Minimum detectable effect = 2.80 × standard error | Never fails; shown so "no effect found" is not confused with "no effect" |

The three falsification tests (pre-trend, placebo dates, placebo outcomes) share a **5% family-wise error budget** (Bonferroni: 5% ÷ 3 each by default). Any failure moves the result to grade D. Testing each at 5% would wrongly downgrade roughly one valid program in seven.

## Quality guardrails

Guardrails are estimated with the same design and type adjustment as the primary effect. Each has a threshold for the worst acceptable change.

| Status | Rule |
| --- | --- |
| Pass | The upper end of the harm range is below the threshold |
| Breach | The estimated harm exceeds the threshold and its range excludes zero |
| Watch | Anything else (inconclusive) |

Any breach **withholds the program's value** (the faster-but-worse block).

Service desk defaults:

| Guardrail | Default threshold |
| --- | --- |
| Reopen rate | +1 percentage point |
| Reassignments per ticket | +0.10 |
| Escalation rate | +1 percentage point |
| Satisfaction score | −0.10 points |
| False deflection rate | At most 15%, judged on its level with a Wilson interval |

## Verified deflection

A self-service resolution is **verified** if the same (pseudonymized) user does not open a ticket of the same type within 7 days. Otherwise it is a **false deflection**: the work was done twice.

Deflection value counts toward measured value only if all of the following hold:

- the program's evidence grade counts;
- no guardrail breached;
- ticket volume per team-week fell measurably for AI teams (the volume estimate's upper bound is below zero);
- agent time per ticket type is recorded.

Otherwise it is shown as indicative.

## Resolution audit

The audit checks each resolution an AI claims. A claim **did not hold** if the same person opened a ticket about the same type of issue within 7 days. People come back after human-resolved tickets too, so the audit measures the excess over that baseline:

```
return rate after the AI      r = claims followed by a return ÷ claims checked
return rate after people      b = the same rate after human-resolved tickets, standardized to the claims'
                                  mix of month and issue type
did not hold                  f = (r − b) ÷ (1 − b)
```

- **Standardization.** Each (month, issue type) cell of claims is compared with human-resolved tickets of the same type resolved in the same month. Cells with fewer than 30 baseline tickets use the type over all months, then all tickets. Types under 1% of claims are grouped.
- **Matching.** Claims and tickets are typed on the fields both files have (category, subcategory). If fewer than half of the claims' types exist in the tickets, or the claims have no category, any ticket from the same person counts as a return, and the audit says so.
- **Eligibility.** A claim is checked only if the ticket export covers the full window after it. Later claims are reported as too recent; any billing estimate assumes they hold at the same rate.
- **Uncertainty.** A Poisson bootstrap over people (999 draws) moves each person's claims and tickets together. If a leave-one-month-out jackknife standard error is larger than the bootstrap's, the 95% range is widened to it.
- **Robustness.** 3- and 14-day windows, any-ticket matching and a baseline from the same people only are reported beside the main result.
- **Confidence.** High, medium or low, from the number of claims and baseline tickets, the share of people found in both systems, issue-type matching and the width of the range. See [resolution-audit.md](resolution-audit.md).

The audit does not need a comparison group of teams, so it works when everyone got the AI on the same day. It is a floor: failures that end in a phone call or a colleague's help leave no ticket.

## Bill reconciliation

`probative reconcile` matches each line of a per-resolution bill to the buyer's conversation log by the vendor's item ID, and to the ticket system by person and issue type. Lines billed twice, with no matching conversation, for conversations that were not resolved, or outside the billing period are billing errors. With a contract definition of a resolution (no repeat ticket within the window), each billed item followed by such a ticket is disputed too; without one, the resolution audit's excess over the human baseline is reported as a negotiation figure. Bills without item IDs are reconciled by day. See [reconciliation.md](reconciliation.md) and the model terms in [outcome-terms.md](outcome-terms.md).

## Regression watch

`probative watch` compares the latest 4 weeks of the AI's outcomes with the 12 weeks before: the share of claimed resolutions that did not hold (against the human baseline in the same weeks and types) and the share of conversations the AI resolved. The windows are bootstrapped over people independently, which is conservative. A regression is a worsening of at least 2 percentage points whose 95% range excludes zero. Known release dates are tested on four weeks either side, with a Bonferroni correction. See [regression-watch.md](regression-watch.md).

## Action audit

`probative closures` applies the resolution-audit estimator to tickets the AI closed by itself: a closure is reversed if the ticket was reopened or the same person opened a ticket about the same type of issue within the window, and the AI's reversal rate is compared with your agents' closures in the same months and types. See [action-audit.md](action-audit.md).

## Bake-off

`probative bakeoff` assigns teams to vendors and a control group by lottery, then measures each vendor against the control teams over the trial with the same estimator, guardrails and ranges as an evidence pack (the analysis is restricted to that vendor's teams and the control teams). A vendor leads only if eligible (effect excludes zero, grade counts, guardrails held), and is a clear winner only if its range does not overlap the runner-up's. See [bakeoff.md](bakeoff.md).

## Measurement plan

`probative plan pack` assembles, before go-live, everything the analysis will hold the program to: the wave schedule and holdout, the chance of detecting the expected effect at 8, 12 and 16 weeks after the last wave (simulated with the same estimator, using noise calibrated from the customer's own history when available), the guardrails and thresholds, the diagnostics, decision rules mapping each outcome to renew, expand, fix or stop, the value inputs, the registration hash and a sign-off table. The expected grade is A only if the wave order is randomized and registered before the first wave.

## Value and ROI

```
capacity value = (hours saved + verified deflections × baseline hours for the type
                  − false deflections × extra hours per false deflection) × loaded hourly cost
cash value     = capacity value × realization share + documented realized savings
ROI            = (value − total cost) ÷ total cost
payback        = one-time costs ÷ (monthly value − monthly recurring cost)
```

Each customer assumption (loaded hourly cost, realization share, extra hours per false deflection, uncertain cost lines) is a triangular distribution: low, most likely, high. 10,000 simulations combine them with the bootstrap draws of hours saved. Results are reported as P10, P50 and P90.

Costs are converted to the measured period, from the first rollout to the end of the window. Annual costs are prorated, monthly costs multiplied, and one-time and period costs counted once.

**Capacity ROI** counts all freed hours. **Cash ROI** counts only money that provably moved. With no documented realization, cash value is zero, and the pack says so.

## Evidence grades

See [evidence-grades.md](evidence-grades.md). In short: A and B count toward measured ROI. C counts, flagged as weaker. D is indicative only. E (claimed: vendor dashboards, surveys, self-reported time) is shown for comparison and never added.

## Reproducibility

Every result records:

- the engine version and git commit;
- the SHA-256 of each input file;
- the configuration and registration hashes;
- the bootstrap seed and draws;
- every exclusion, with a reason and a count.

Given the same files, configuration and pseudonymization key, `probative analyze` reproduces the result exactly. `probative reproduce results.json` does that check for you: it reruns the analysis (or an audit) with the recorded settings and seed, confirms the input files match their recorded fingerprints, and lists any value that differs.

## Validation

The engine is tested on simulated service desks with planted effects, including zero, using the same pipeline customers use. The simulated data includes realistic messiness: deflection, seasonality, learning curves, duplicates, impossible timestamps and right-censoring. Results, including coverage of the 95% ranges and false-positive rates: [validation.md](validation.md).

## Known limitations

- **Parallel trends is an assumption.** Diagnostics can reveal violations; they cannot prove its absence. Randomized wave order (grade A) removes most of the risk, which is why the rollout designer exists.
- **Spillovers are not modeled in v0.1.** If AI teams hand work to comparison teams, effects can be biased in either direction. A spillover check is planned.
- **Small programs.** With fewer than about 12 teams, or a single team in a wave, ranges are approximate. The grade is lowered, and the pack says so.
- **Large, heterogeneous effects.** When the effect scales with each team's own speed and the ticket mix also shifts, ranges for large effects are somewhat narrow (87% coverage of a −15% effect in validation). An analytic variance estimator is being evaluated.
- **Time worked as recorded.** If agents under-record time, so does the engine. The staffing-based fallback (staffed hours ÷ tickets) cannot be mix-adjusted.
- **Everyone at once.** The effect estimate needs teams that got the AI at different times. When every team got it on one day, the resolution audit still works, and agent-level staggered adoption is on the roadmap.
- **Value is only as good as the unit values.** Finance owns the loaded cost and the realization evidence; the pack records who agreed them and when.

## References

- Borusyak, K., Jaravel, X. and Spiess, J. (2024). Revisiting Event-Study Designs: Robust and Efficient Estimation. *Review of Economic Studies*.
- Brynjolfsson, E., Li, D. and Raymond, L. (2025). Generative AI at Work. *Quarterly Journal of Economics*.
- Callaway, B. and Sant'Anna, P. (2021). Difference-in-Differences with multiple time periods. *Journal of Econometrics*.
- de Chaisemartin, C. and D'Haultfœuille, X. (2020). Two-Way Fixed Effects Estimators with Heterogeneous Treatment Effects. *American Economic Review*.
- Goodman-Bacon, A. (2021). Difference-in-differences with variation in treatment timing. *Journal of Econometrics*.
- METR (2025). Measuring the Impact of Early-2025 AI on Experienced Open-Source Developer Productivity. arXiv:2507.09089.
- Sun, L. and Abraham, S. (2021). Estimating dynamic treatment effects in event studies with heterogeneous treatment effects. *Journal of Econometrics*.
