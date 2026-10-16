# Dispute pack: Example vendor

Billing period 2026-05-01 to 2026-05-31 · 650 billed resolutions · $650 billed
> Of 650 billed resolutions ($650) for 2026-05-01 to 2026-05-31, 110 are disputable ($110): 8 billed twice; 2 with no matching conversation; 3 not resolved; 97 did not hold under the contract's definition.

## 1. The definition used

**Verified resolution:** a conversation the AI resolved, inside the billing period, after which the same person did not open a ticket about the same type of issue within 7 days.

The contract uses this definition, so every billed item that did not meet it is disputed.

## 2. Reconciliation

| Reason | Items | Amount |
| --- | ---: | ---: |
| Billed more than once | 8 | $8 |
| No matching conversation in your records | 2 | $2 |
| The conversation was not resolved | 3 | $3 |
| Did not hold under the contract's definition | 97 | $97 |
| **Disputed** | **110** | **$110** |

Held: 540 of 637 checked; 97 came back; 0 too recent to check.

## 3. Compared with your agents' resolutions

15.2% came back after the AI against 1.5% after your agents. Did not hold beyond that normal rate: **13.9%** (range 11.0% to 17.3%).
## 4. In the vendor's favour

0 conversations resolved in this period are not on the bill.

- 2,972 bill lines dated outside the period were left out.

The line-by-line list is in `disputed-items.csv`. Probative Labs 0.3.0 · settings `d9fcd198dba9` · run 2026-10-16T17:05:05+00:00