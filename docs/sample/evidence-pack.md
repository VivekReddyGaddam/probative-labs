# Evidence pack: AI agent assist (simulated)

IT service desk · 2025-01-06 to 2026-07-05 · evidence grade **B** · Probative Labs 0.2.0

## 1. Headline

AI agent assist (simulated) changed agent hours per ticket by -0.11 h per ticket (-12%; 95% range -0.13 to -0.09 h, -14% to -11%). Evidence grade B. Guardrails: all pass. Cash ROI 7% to 79% (P10 to P90).

**Recommendation: Renew and expand.** Cash ROI is positive even at the low end of the range.

## 2. The question as registered

- **Hypothesis:** AI summaries and resolution suggestions cut agent hours per ticket by at least 10% without lowering quality; the virtual agent deflects password resets and simple requests.
- **Primary metric:** effort_hours (expected −10%)
- **Registration:** Registered 2026-10-16T15:09:26+00:00 after go-live.

## 3. Design

**Staggered rollout (imputation difference-in-differences).**
- 30 teams adopted in 3 waves, with 10 teams never exposed in the window. Each wave is compared with teams that did not have the AI yet.
- Rollout order was assigned by lottery, so waves are comparable by design.

Evidence grade **B**: Comparison-group design with diagnostics passed: counts toward measured ROI.
- Randomized rollout, but the question was not registered before go-live, so capped at B.

## 4. Effects

Headline and mix-adjusted results side by side. Effects are per ticket; negative means less time.

| Metric | View | Effect | 95% range | Relative | Total over AI tickets |
| --- | --- | --- | --- | --- | --- |
| Agent hours per ticket | headline | −0.03 h | −0.05 to −0.01 | −3.3% | −429 h |
| Agent hours per ticket | mix-adjusted | −0.11 h | −0.13 to −0.09 | −12.2% | −1,725 h |
| Resolution time | headline | +0.23 h | −0.25 to +0.75 | +1.9% | +3,651 h |
| Resolution time | mix-adjusted | −1.02 h | −1.42 to −0.61 | −7.4% | −16,001 h |

Descriptive before/after for AI teams (no comparison group, not causal):

| Metric | Before AI | After AI | Change |
| --- | --- | --- | --- |
| Headline mean resolution time (h) | 12.6 | 12.7 | +1% |
| Mix-adjusted mean resolution time (h) | 12.6 | 11.6 | −8% |
| Agent hours for the same work | 14,281.9 | 12,460.5 | −13% |

By work type (agent hours per ticket, within type):

| Work type | AI tickets | Effect | Range | Relative |
| --- | --- | --- | --- | --- |
| Software / Application Error | 2,680 | −0.230 | −0.294 to −0.163 | −15% |
| Software / Installation | 2,394 | −0.075 | −0.105 to −0.039 | −10% |
| Hardware / Laptop | 2,190 | −0.126 | −0.219 to −0.043 | −9% |
| Account Access / Permissions | 2,027 | −0.056 | −0.085 to −0.032 | −11% |
| Software / Email | 1,840 | −0.051 | −0.082 to −0.021 | −8% |
| Account Access / Password Reset | 1,708 | −0.045 | −0.059 to −0.031 | −18% |
| Network / Connectivity | 1,465 | −0.117 | −0.201 to −0.040 | −10% |
| Inquiry / Help / General | 1,391 | −0.070 | −0.090 to −0.050 | −17% |

AI was recorded on 77% of AI teams' tickets after rollout (usage is context, never value).

## 5. Effect over time

| Weeks since rollout | Effect | Range |
| --- | --- | --- |
| -8 | +0.033 | −0.014 to +0.079 |
| -7 | +0.006 | −0.043 to +0.059 |
| -6 | −0.039 | −0.082 to +0.001 |
| -5 | −0.012 | −0.065 to +0.052 |
| -4 | +0.001 | −0.042 to +0.046 |
| -3 | −0.039 | −0.099 to +0.013 |
| -2 | +0.012 | −0.041 to +0.062 |
| -1 | −0.037 | −0.088 to +0.015 |
| 0 | −0.038 | −0.092 to +0.013 |
| 1 | −0.049 | −0.110 to +0.013 |
| 2 | −0.064 | −0.121 to −0.002 |
| 3 | −0.083 | −0.126 to −0.033 |
| 4 | −0.053 | −0.104 to −0.005 |
| 5 | −0.108 | −0.169 to −0.050 |
| 6 | −0.127 | −0.174 to −0.080 |
| 7 | −0.150 | −0.199 to −0.103 |
| 8 | −0.122 | −0.187 to −0.055 |
| 9 | −0.169 | −0.220 to −0.123 |
| 10 | −0.166 | −0.211 to −0.120 |
| 11 | −0.096 | −0.141 to −0.044 |
| 12 | −0.131 | −0.192 to −0.069 |
| 13 | −0.079 | −0.140 to −0.017 |
| 14 | −0.170 | −0.226 to −0.111 |
| 15 | −0.091 | −0.152 to −0.041 |
| 16 | −0.059 | −0.139 to +0.022 |
| 17 | −0.113 | −0.170 to −0.051 |
| 18 | −0.145 | −0.209 to −0.079 |
| 19 | −0.139 | −0.201 to −0.070 |
| 20 | −0.030 | −0.102 to +0.054 |
| 21 | −0.105 | −0.171 to −0.035 |
| 22 | −0.176 | −0.246 to −0.096 |
| 23 | −0.126 | −0.213 to −0.053 |
| 24 | −0.115 | −0.172 to −0.051 |
| 25 | −0.123 | −0.189 to −0.052 |
| 26 | −0.146 | −0.266 to −0.003 |
| 27 | −0.056 | −0.180 to +0.048 |
| 28 | −0.089 | −0.171 to +0.009 |
| 29 | −0.103 | −0.196 to −0.018 |
| 30 | −0.227 | −0.308 to −0.137 |
| 31 | −0.136 | −0.248 to −0.019 |
| 32 | −0.221 | −0.285 to −0.133 |
| 33 | −0.164 | −0.250 to −0.043 |

## 6. Quality guardrails

| Guardrail | Threshold | Status | Detail |
| --- | --- | --- | --- |
| Reopen rate | rise of at most 0.01 (share) | Pass | Change +0.00188 (share) (range -0.00261 to +0.00664); within the limit. |
| Reassignments per ticket | rise of at most 0.1 | Pass | Change -0.00464 (range -0.0246 to +0.0162); within the limit. |
| Escalation rate | rise of at most 0.01 (share) | Pass | Change -0.00104 (share) (range -0.00887 to +0.00589); within the limit. |
| Satisfaction score | fall of at most 0.1 pts | Pass | Change +0.000737 pts (range -0.0451 to +0.0528); within the limit. |
| False deflection rate | at most 15% | Pass | 13.6% of resolved sessions came back as a ticket within 7 days (range 12.5% to 14.8%). |

## 7. Diagnostics

| Check | Result | Detail |
| --- | --- | --- |
| Pre-trend test | Pass | AI and comparison teams moved together before rollout (joint p = 0.38 over 8 weeks). |
| Placebo dates | Pass | A fake rollout 8 weeks early shows no effect (-0.0109, range -0.0386 to +0.0147). |
| Placebo outcome: Share of tickets opened outside business hours | Pass | A metric the AI cannot affect did not move (+0.0057). |
| Leave-one-team-out | Pass | Dropping any one AI team keeps the estimate between -0.112 and -0.106. |
| Category drift | Pass | No sign that the category taxonomy changed with the rollout. |
| Power | Pass | With this data the smallest reliably detectable effect is ±0.0218 per unit of work (±2%). |

## 8. Value and ROI

Status: **counted**. Period: 7.8 months from first rollout. All values P10 / P50 / P90 from 10,000 simulations.

| | P10 | P50 | P90 |
| --- | --- | --- | --- |
| Agent hours released | 2,343 | 2,590 | 2,849 |
| Capacity value | $138,059 | $156,639 | $177,415 |
| Cash value | $59,413 | $78,103 | $98,716 |
| Total cost | $52,955 | $55,588 | $58,306 |
| Capacity ROI | 146% | 182% | 222% |
| Cash ROI | 7% | 40% | 79% |
| Cost per agent hour released | $19 | $21 | $24 |

Payback (cash basis, middle case): 3.3 months. Payback (capacity basis, middle case): 1.1 months.

Deflection: 3,543 self-service resolutions, 3,061 verified, 482 false (13.6%); 1,150 agent hours avoided. Counted: verified resolutions and a measurable fall in ticket volume for AI teams.

Costs for the period:

| Component | Description | Basis | Low | Most likely | High |
| --- | --- | --- | --- | --- | --- |
| license | AI add-on licence uplift (illustrative) | annual | $31,146 | $31,146 | $31,146 |
| build_integration | Implementation partner (illustrative) | one_time | $8,000 | $10,000 | $14,000 |
| change_training | Agent training: 300 agents × 20 min × $60 | one_time | $6,000 | $6,000 | $6,000 |
| human_oversight | Knowledge-base upkeep and AI review time | monthly | $3,893 | $7,786 | $11,680 |

## 9. Claimed versus proven

| Source | Claimed hours | Claimed value | Measured hours (P10 to P90) | Note |
| --- | --- | --- | --- | --- |
| Vendor value dashboard (illustrative) | 9,000 | n/a | 2,343 to 2,849 | Usage × assumed minutes saved per summary |

Claimed figures are grade E: shown for comparison, never added to measured value.

## 10. Assumptions

| Assumption | Low | Most likely | High | Unit | Owner | Agreed |
| --- | --- | --- | --- | --- | --- | --- |
| Loaded cost per agent hour | 52.00 | 60.00 | 70.00 | USD | Finance partner, IT (illustrative) | 2025-11-10 |
| Share of freed hours provably redeployed (realization) | 0.30 | 0.50 | 0.70 | share | Finance partner, IT (illustrative) | 2025-11-10 |
| Extra agent hours per false deflection | 0.25 | 0.50 | 1.00 | h | Finance partner, IT (illustrative) | 2025-11-10 |

## 11. Data lineage

| File | Rows | Modified | SHA-256 |
| --- | --- | --- | --- |
| incidents.csv | 74,477 | 2026-10-16T15:09:26+00:00 | `a5dd9e825b4798c2…` |
| rollout.csv | 40 | 2026-10-16T15:09:26+00:00 | `dca626de90094161…` |
| virtual_agent.csv | 4,741 | 2026-10-16T15:09:26+00:00 | `45589c60796fe765…` |

Exclusions:
- missing team_id: 74
- duplicate ID: 37
- resolved before opened: 106


Code: Probative Labs 0.2.0 (git e6ca2e331e782c111dee33122de4fda1bd6b9f43-dirty). Configuration SHA-256 `3e246c2f719bccc6…`. Run 2026-10-16T15:09:26+00:00.

## 12. Recommendation

**Renew and expand.** Cash ROI is positive even at the low end of the range.

This is a rule-based suggestion from the evidence above; the decision belongs to the program owner and finance.