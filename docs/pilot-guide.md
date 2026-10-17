# Run a pilot on your own data

This guide is for a service-desk analyst, data analyst or IT operations lead who wants an independent check of one AI program with Probative Labs. You run everything on your own computer. Your ticket data never leaves your machine, and you decide what, if anything, to share.

## Pick your path

| Your situation | Start with | Effort |
| --- | --- | --- |
| A virtual agent or AI agent reports resolutions (and you may pay per resolution) | **Resolution audit**: did the claimed resolutions hold? | About an hour, plus getting two exports |
| You pay per resolution and have the vendor's bill | **Bill reconciliation**: what to dispute, line by line (`probative reconcile`) | An hour, plus the bill |
| The AI is billed per conversation, per session, in assists or credits, or per seat | **Spend check**: what each resolution that held cost, and what to raise (`probative reconcile --model`, `probative usage`, `probative seats`; see [billing models](billing-models.md)) | An hour, plus the bill or usage export |
| The AI agent closes tickets by itself | **Action audit**: how often its closures were reversed (`probative closures`) | An hour |
| You are choosing between vendors | **Bake-off**: vendors side by side on your own teams (`probative bakeoff`) | A trial of 6 to 10 weeks |
| Teams got the AI on different dates, or some were held back | **Evidence pack**: what changed, and was it worth it? | About two hours over a week, mostly exports and agreeing two numbers with finance |
| The rollout hasn't happened yet | **Measurement plan**: design the rollout so the result can be proven | An afternoon |

The audit works even when every team got the AI on the same day. If you can, do the audit and the evidence pack together.

## What you get

- **Resolution audit:** the share of the AI's claimed resolutions that did not hold, compared with your agents' own resolutions, by issue type and month, with a range and a confidence level. If you give the price per resolution, the amount paid for ones that did not hold. [Sample](https://probativelabs.com/sample/resolution-audit.html).
- **Evidence pack:** the effect on agent hours or resolution time per ticket, with a range; an evidence grade from A to E; quality guardrails; capacity and cash ROI ranges; a renew, expand, fix or stop recommendation. [Sample](https://probativelabs.com/sample/evidence-pack.html).
- **Measurement plan:** waves, holdout, the chance of detecting the expected effect, guardrails, decision rules and a sign-off table.

All samples use simulated data.

## Before you start

- **Permission.** Check with whoever owns the ticket data, and your privacy team if you have one, that you may analyse an export on your work computer. Probative pseudonymizes people at ingest, but that does not replace permission.
- **For the resolution audit:** a virtual agent or AI agent with a few hundred resolved conversations, and a way to export them with who the user was. The user identifier must match the one in your tickets (an email or employee ID usually does).
- **For the evidence pack:** an AI tool used by your service desk, such as agent assist, summaries, a virtual agent or an AI agent, that:
  - reached different teams on different dates, **or** reached some teams and not others;
  - covers at least 8 teams (12 or more is better);
  - has at least three months of tickets before the first team got it, and some weeks after.

  If every team got the AI on the same day and none were held back, the evidence pack honestly reports "no number". The audit still works, and planning the next rollout in waves fixes the rest (step 6).

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

### The AI's claimed resolutions (for the audit)

From your virtual agent or AI agent's reporting, export the conversations it marked as resolved, or all conversations with their outcome. Include who the user was, when the conversation started and, if available, the topic or category. Save it as `data/virtual_agent.csv`.

You can run the audit straight away, without a project file:

```bash
probative audit --tickets data/incidents.csv --preset servicenow --claims data/virtual_agent.csv --price 0.99 --open
```

Leave out `--price` if you don't pay per resolution. If the audit can't find a column, it names the one it looked for; map yours with `--claims-column requester_id="User Email"`. Read the "Audit confidence" section of the report before sharing the result: if few people appear in both files, the two systems probably identify people differently.

### ServiceNow Virtual Agent, in one command

In ServiceNow, open `interaction.list`, filter to **Virtual agent = true** for the same months as your incidents, add the columns Number, Opened for, Opened, State, State reason, Agent chat and Type, and export as CSV to `data/interactions.csv`. Then:

```bash
probative audit --tickets data/incidents.csv --preset servicenow --claims data/interactions.csv --claims-preset servicenow_va --open
```

The preset counts a conversation the virtual agent closed without a live agent as resolved, the way ServiceNow's own reporting does, and the report shows each state reason separately: "Topic Complete" next to "No User Response", with how often each came back. If you pay for Now Assist in assists, export the Gen AI usage log (`sys_gen_ai_usage_log`, with Created, Assists, Skill config and User) and run `probative usage` as in [billing models](billing-models.md). The presets follow ServiceNow's documented field names and have been tested on simulated exports: if a column is missing, the error names it.

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
probative plan pack --open                              # the measurement plan to sign off
```

A lottery-based wave order, registered before go-live, can earn grade A. The measurement plan puts the waves, the chance of detecting the effect, the guardrails and the decision rules on a few pages for the program owner and finance to sign.

## 7. Run it

```bash
probative analyze --open
```

The evidence pack opens in your browser. It is also saved as `out/evidence-pack.html`, `out/evidence-pack.md` and `out/results.json`.

If `data.self_service` points at the AI's conversations, run the audit from the same project:

```bash
probative audit --open
```

To contribute anonymously to the benchmark, run `probative benchmark` and read the file it writes. It holds rates, ranges and size bands only. Nothing is sent anywhere unless you share it.

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
