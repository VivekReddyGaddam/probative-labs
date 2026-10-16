# Resolution audit: AI agent assist (simulated)

Claims 2025-11-10 to 2026-06-28 · 3,543 claimed resolutions · tickets 2025-01-06 to 2026-07-05 · **audit confidence: high**

> Of 3,543 resolutions the AI claimed, an estimated 12% (95% range 11% to 14%) did not hold: the person came back within 7 days, beyond the normal return rate after human-resolved tickets of the same types. At $1.00 per resolution, about $435 ($400 to $479) was paid for resolutions that did not hold. Audit confidence: high.

## 1. What was checked

For each resolution the AI claimed, the audit looked for a ticket from the same person in the following 7 days about the same type of issue. The same check on tickets resolved by people, weighted to the same issue mix, in the same weeks, gives the normal return rate. Only the excess counts against the AI:

did not hold = (return rate after the AI − return rate after people) ÷ (1 − return rate after people)

95% ranges come from resampling people 999 times.

## 2. Result

| | Estimate | 95% range |
| --- | ---: | ---: |
| Came back within 7 days after an AI-claimed resolution | 13.9% | 12.8% to 15.0% |
| Came back within 7 days after a human-resolved ticket | 1.8% | 1.5% to 2.0% |
| **Claimed resolutions that did not hold** | **12.3%** | 11.3% to 13.5% |
| Resolutions that held, of 3,543 claimed | 3,108 | 3,064 to 3,143 |
| Billed at $1.00 per claimed resolution | $3,543 | |
| **Paid for resolutions that did not hold** | **$435** | $400 to $479 |

## 3. By type of issue

| Type | Claimed | Came back | After AI | After people | Baseline | Did not hold |
| --- | ---: | ---: | ---: | ---: | --- | ---: |
| Account Access / Password Reset | 1,725 | 236 | 13.7% | 1.9% | same type | 12% |
| Inquiry / Help / General | 705 | 97 | 13.8% | 1.0% | same type | 13% |
| Account Access / Permissions | 675 | 94 | 13.9% | 2.0% | same type | 12% |
| Software / Email | 161 | 25 | 15.5% | 1.9% | same type | 14% |
| Software / Installation | 105 | 15 | 14.3% | 2.7% | same type | 12% |

## 4. Over time

| Month | Claims checked | After AI | After people | Did not hold |
| --- | ---: | ---: | ---: | ---: |
| 2025-11 | 46 | 17.4% | 2.7% | 15% |
| 2025-12 | 142 | 17.6% | 2.7% | 15% |
| 2026-01 | 292 | 13.0% | 2.8% | 11% |
| 2026-02 | 427 | 12.4% | 1.6% | 11% |
| 2026-03 | 507 | 12.6% | 2.0% | 11% |
| 2026-04 | 680 | 13.2% | 1.3% | 12% |
| 2026-05 | 637 | 15.2% | 1.6% | 14% |
| 2026-06 | 640 | 14.4% | 1.7% | 13% |

## 5. Robustness

| Check | Claims checked | Did not hold |
| --- | ---: | ---: |
| Return window 3 days | 3,509 | 4.6% |
| Return window 7 days (main result) | 3,371 | 12.3% |
| Return window 14 days | 3,217 | 12.2% |
| Any ticket counts as a return | 3,371 | 11.2% |
| Baseline from the same people only | 3,371 | 12.2% |

## 6. Audit confidence: high

- Enough claims and human-resolved tickets, a tight range, and the same people appear in both systems.

- Claimed resolutions: 3,543; checked: 3,371; too recent to check: 172; outside the ticket dates: 0
- People in the AI's records who also appear in the ticket data: 100%
- Human-resolved tickets in the baseline: 29,151
- 172 claims from the last 7 days could not be checked yet; the billing estimate assumes they held at the same rate.

## 7. Data lineage

| File | Rows | SHA-256 |
| --- | ---: | --- |
| incidents.csv | 74,477 | `a5dd9e825b4798c2…` |
| virtual_agent.csv | 4,741 | `45589c60796fe765…` |

Probative Labs 0.2.0 · settings `81a896999fd9` · seed 20261014 · run 2026-10-16T15:09:42+00:00

People who give up and call or email instead leave no ticket, so this audit can undercount failures: treat the result as a floor unless every channel creates a ticket.