# Run a pilot on your own data

This guide is for a service-desk analyst, data analyst or IT operations lead who wants to measure one AI program with Probative Labs. You run everything on your own computer. Your ticket data never leaves your machine, and you decide what, if anything, to share.

Expect about two hours of hands-on work spread over a week: most of it exporting data and agreeing two numbers with finance.

## What you get

An evidence pack for one AI program. It shows:

- the effect on agent hours or resolution time per ticket, with a range;
- an evidence grade from A to E;
- quality guardrails: reopens, escalations, satisfaction, false deflection;
- capacity and cash ROI ranges;
- a renew, expand, fix or stop recommendation.

[See a sample](https://vivekreddygaddam.github.io/probative-labs/sample/evidence-pack.html) (simulated data).

## Before you start

- **Permission.** Check with whoever owns the ticket data, and your privacy team if you have one, that you may analyse an export on your work computer. Probative pseudonymizes people at ingest, but that does not replace permission.
- **A program worth measuring.** An AI tool used by your service desk, such as agent assist, summaries, a virtual agent or an AI agent, that:
  - reached different teams on different dates, **or** reached some teams and not others;
  - covers at least 8 teams (12 or more is better);
  - has at least three months of tickets before the first team got it, and some weeks after.

  If every team got the AI on the same day and none were held back, Probative will honestly report "no number". Planning the next rollout in waves fixes that (step 6).

## 1. Install

You need Python 3.12 or newer.

```bash
pip install probative-labs          # or: uv tool install probative-labs
probative --version
probative demo --open               # optional: a 1-minute run on simulated data
```

## 2. Export your tickets

Export incidents or requests from about **six months before the first team got the AI until today**. Use CSV.

| Platform | How to export | Preset |
| --- | --- | --- |
| ServiceNow | Incident list, filter on Opened, then right-click a column header › Export › CSV. Include: number, assignment_group, category, subcategory, priority, contact_type, opened_at, resolved_at, closed_at, time_worked, reopen_count, reassignment_count, escalation, caller_id, assigned_to, state. | `servicenow` |
| Jira Service Management | Issue search with a JQL filter for the service desk project, then Export › CSV (all fields). | `jira_service_management` |
| Zendesk | Admin Center › Account › Tools › Reports › Export, CSV. | `zendesk` |
| Freshservice | Tickets list, filter on created time, then Export, CSV. | `freshservice` |
| BMC Helix ITSM | Incident report export, including Incident ID, Assigned Group, Reported Date, Last Resolved Date and Status. | `bmc_helix` |
| Ivanti Neurons for ITSM | Incident grid export, including IncidentNumber, OwnerTeam, CreatedDateTime and ResolvedDateTime. | `ivanti` |

Your survey score and any "AI was used on this ticket" flag are often custom fields. Include them if they exist.

## 3. List who got the AI, and when

Create `rollout.csv` with one row per team that got the AI. Team names must match the team column in your export **exactly**, for example the assignment group in ServiceNow or the group in Zendesk.

```csv
team_id,start_date,kind,randomized
Service Desk L1,2026-03-02,wave,false
Service Desk L2,2026-04-06,wave,false
Desktop Support,2026-05-04,wave,false
```

Teams that never got the AI can be left out. They become the comparison group. Set `randomized` to `true` only if the order really was decided by lottery.

## 4. Create the project

```bash
mkdir my-pilot && cd my-pilot
mkdir data          # put incidents.csv and rollout.csv here
probative init --preset servicenow     # or jira_service_management, zendesk, ...
```

Open `probative.yaml` and set:

- **`data.work_units`** and **`data.exposure`**: your two file paths.
- **`hypothesis.statement`** and **`primary_metric`**: `effort_hours` if your export has time worked (ServiceNow, Jira, Freshservice); otherwise `resolution_hours`.
- **`value.loaded_hourly_cost`**: ask finance for the fully loaded cost of an agent hour. A range is fine, e.g. `{low: 45, mid: 55, high: 65}`.
- **`value.realization_rate`**: the share of freed hours you can show were actually used (backlog cleared, contractors released, hires avoided). Use `0` if you don't know. The pack will then show capacity value, and cash value only where it's evidenced.
- **`costs`**: what the AI costs. Licences, implementation, training and the time people spend reviewing AI output all count.

## 5. Check the data

```bash
probative health
```

This reports how many rows are usable and what was excluded and why: duplicates, impossible timestamps, rows with no team. If it says a required column is missing, it names the column it looked for. Map yours in `probative.yaml`:

```yaml
data:
  mapping:
    team_id: "Custom field (Support team)"
```

## 6. If the rollout hasn't happened yet

This is the best time to measure, because you can make the evidence stronger by design.

```bash
probative plan power -c probative.yaml --effect -0.10   # is it big enough to detect a 10% change?
probative plan waves teams.csv --waves 3 --start 2026-11-02 --holdout 0.2
probative register                                      # lock the question before results exist
```

A lottery-based wave order, registered before go-live, can earn grade A.

## 7. Run it

```bash
probative analyze --open
```

The evidence pack opens in your browser. It is also saved as `out/evidence-pack.html`, `out/evidence-pack.md` and `out/results.json`.

## 8. Review it together

On a 45-minute call, walk through:

1. **The grade, and why.** Section 3 explains the design and any downgrades.
2. **Diagnostics** (section 7). A failed pre-trend or placebo test means the comparison is not trustworthy, and the pack says so.
3. **Guardrails** (section 6). Faster but worse earns nothing.
4. **Headline versus mix-adjusted** (section 4). This is where the deflection trap shows up.
5. **Value** (section 8). Check that the unit values and costs are right.

**What the pack contains:** team names, counts, effects, ranges, costs, file fingerprints and row counts. It contains no names, user IDs or ticket text. Read it before sharing it. If team names are sensitive, rename them in `rollout.csv` and the export first.

## If something looks wrong

| You see | It usually means |
| --- | --- |
| "No number" | Everyone got the AI at once, there was no comparison group, or there are fewer than 8 teams. Plan the next rollout in waves. |
| Grade D | A diagnostic failed. Something else changed at the same time as the rollout, such as a reorganization, new categories or a big seasonal shift. The pack names which check failed. |
| Very wide ranges | Few teams, few weeks after rollout, or noisy data. `probative plan power` shows how much more data would help. |
| Effect looks too good | Check that `rollout.csv` dates are right and that team names match exactly. |

## What we ask in return

Thirty minutes of honest feedback on what was useful, what was confusing and what you'd need to act on the result. If you're willing, we'd also like an anonymized case study. That part is optional, and we never name your company without written permission.

Questions or problems: [open an issue](https://github.com/VivekReddyGaddam/probative-labs/issues), or reply to whoever invited you to the pilot.
