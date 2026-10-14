# Evidence grades

Every result carries a grade that says how much the design behind it can be trusted. Only grades A to C are added to measured ROI totals.

| Grade | Design behind the number | How it is used |
| --- | --- | --- |
| **A** | Randomized holdout or randomized wave order, registered before go-live, with all diagnostics passed | Counts toward measured ROI |
| **B** | Staggered rollout or comparison group, with pre-trend and placebo tests passed | Counts toward measured ROI |
| **C** | A valid design that is weaker: fewer than 12 teams in v0.1, and synthetic control or interrupted time series once they ship | Counts, flagged as weaker |
| **D** | Any falsification diagnostic failed (pre-trend, placebo dates, placebo outcomes, leave-one-team-out, category drift) | Shown as indicative, never added to totals |
| **E** | Vendor estimates, dashboards, surveys, self-reported time saved | Shown as claimed, never added to totals |
| **No number** | No valid comparison: everyone got the AI at once with no holdout, or too few teams | Probative Labs says so instead of producing a figure |

## How a grade is assigned

1. **Choose the design.** No comparison means no number.
2. **Run the diagnostics.** Any failure means grade D.
3. **Assign the base grade.** Randomized, registered before the first rollout, and analysed as registered means A. Anything else is B.
4. **Adjust for program size.** Fewer than 12 teams lowers the grade one step (A to B, B to C).

A randomized rollout that was registered after go-live, or analysed differently from its registration, is capped at B. The pack lists every deviation.

## Raising a grade

Most programs can move up a grade by design, not by more data:

- **B to A:** before the next expansion, use `probative plan waves` to assign teams to waves by lottery, then run `probative register` before the first wave starts.
- **No number to B:** keep a share of teams (`--holdout 0.2`) without the AI for a fixed period.
- **C to B:** plan enough teams to reach the target power with `probative plan power`.
