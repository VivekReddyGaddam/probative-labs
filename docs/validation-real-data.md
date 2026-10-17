# Validation on real ServiceNow data

Simulated service desks show that the method recovers what was planted in them ([validation.md](validation.md)). They cannot show that the engine copes with a real export, or what real return rates look like. This page uses a public log from a real ServiceNow instance: the **Incident management process enriched event log** in the UCI Machine Learning Repository, the audit trail of an IT company's service desk (CC BY 4.0).

The log predates AI agents, so it cannot show an AI's effect. It shows three things.

## 1. The engine reads the real export

The log has 141,712 events: every update to every incident, from 2016-02-29 to 2017-02-16. The engine keeps each incident's last state (24,918 incidents from 5,244 callers), maps it with the ServiceNow preset and reads its day-first dates (29/2/2016), deciding the date order once for the whole file. The health check flagged: 3 week(s) with no records at all, e.g. 2016-06-13; check the extract is complete.

The log has no time worked or survey scores, so the effort and satisfaction measures cannot run on it; the resolution audit needs neither.

## 2. Real return rates after people's resolutions

How often the same caller opened another incident within 7 days after one was resolved: 41.3% for any issue, 10.1% for the same type of issue (23,336 resolved incidents). Many callers in the log open incidents often, so the rate for any issue is high; the same-type rate is the one the audit compares with. It varies a lot by category:

| Category | Resolved | Came back, any issue | Came back, same type |
| --- | ---: | ---: | ---: |
| Category 42 | 3,124 | 43.2% | 16.5% |
| Category 26 | 3,045 | 37.7% | 7.4% |
| Category 53 | 2,587 | 36.4% | 7.0% |
| Category 46 | 2,356 | 47.3% | 8.8% |
| Category 32 | 1,268 | 43.0% | 9.7% |
| Category 9 | 1,121 | 38.7% | 3.3% |
| Category 37 | 1,098 | 36.1% | 5.7% |
| Category 23 | 1,055 | 41.0% | 6.9% |

## 3. The audit finds failures planted in real data

Each run relabels 2,000 real resolved incidents, chosen at random, as an AI's claimed resolutions made when the incident was resolved; they leave the ticket data, as a deflected contact would. They carry real callers, categories and timing, and before planting they are the same kind of resolution as the rest, so the true share that did not hold is zero. For a planted share f, a return incident about the same type of issue is added within 7 days for a random f of the claims that did not come back, which makes the true excess exactly f. 50 runs per level, 199 bootstrap draws each.

| Planted | Mean estimate | Mean error | 95% range held the truth | Flagged a problem | Mean range width |
| --- | ---: | ---: | ---: | ---: | ---: |
| 0% | −0.2% | 0.7 pts | 94% | 0% | 3.4 pts |
| 10% | 9.8% | 0.6 pts | 100% | 100% | 4.4 pts |
| 20% | 19.7% | 0.4 pts | 100% | 100% | 5.3 pts |

With nothing planted, the audit flagged a problem in none of the runs, and its range held zero in most; with failures planted, it flagged every run and its range held the truth.

## What this does not show

- An AI's effect: the log predates AI agents. Results on real AI come from design partners and are published only with their permission.
- How real failures cluster: the planted returns are spread at random; real ones may bunch by topic or week, which the audit's month-by-month check is for.

## Reproduce

Download `incident_event_log.csv` from the [dataset page](https://archive.ics.uci.edu/dataset/498/incident+management+process+enriched+event+log), then:

```bash
probative validate --real incident_event_log.csv --runs 50 --reps 199 --seed 7 --report real-validation.json
```

Data: Amaral, C., Fantinato, M., & Peres, S. (2018). Incident management process enriched event log [Dataset]. UCI Machine Learning Repository. https://doi.org/10.24432/C57S4H Results above from Probative Labs 0.4.0.
