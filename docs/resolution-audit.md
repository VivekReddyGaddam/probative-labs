# Resolution audit

Virtual agents and AI agents report how many issues they resolved. More and more, that number is also what you pay for. The resolution audit checks it against what happened next: **did the person need help again within a few days?**

It needs no comparison group of teams, so it works even when every team got the AI on the same day.

## Run it

```bash
probative audit --tickets incidents.csv --preset servicenow --claims virtual_agent.csv --price 0.99 --open
```

For ServiceNow Virtual Agent, export the Interactions table and add `--claims-preset servicenow_va`. The report then lists each way ServiceNow closed the conversations it counts as resolved (Topic Complete, No User Response and the rest) with how often each came back, so a chat the person walked away from is not counted the same as a finished topic. `--resolved-value` also takes several values separated by commas, for exports with more than one kind of resolution (for example `"Assumed resolution,Confirmed resolution"`).

Or, in a project that already has `data.self_service` set:

```bash
probative audit -c probative.yaml --open
```

The audit writes `out/audit/resolution-audit.html`, `resolution-audit.md` and `audit.json`.

| Option | Default | Meaning |
| --- | --- | --- |
| `--window` | 7 | Days within which a ticket from the same person counts as a return |
| `--match` | `same_type` | `same_type`: only a ticket about the same type of issue counts. `any`: any ticket counts |
| `--price` | none | Price paid per claimed resolution, to estimate what was paid for ones that did not hold |
| `--resolved-value` | `resolved` | The outcome value that marks a claimed resolution |
| `--claims-column` | | Map a claims field, e.g. `--claims-column requester_id="User Email"` |
| `--tickets-column` | | Map a ticket field, e.g. `--tickets-column requester_id="Caller"` |

The same settings can live in `probative.yaml` under `audit:` (see [configuration.md](configuration.md)).

## What it needs

**The AI's claims:** one row per conversation or automated resolution, with:

- the person (`requester_id`): the same identifier your ticket system uses, such as an email or employee ID;
- when it happened (`started_at`);
- the outcome (`outcome`), if the file also contains escalated or abandoned conversations. If there is no outcome column, every row counts as a claimed resolution;
- ideally the issue's category and subcategory, using the same values as your tickets.

Common column names (`Requester`, `User Email`, `Created`, `Topic` and so on) are recognized automatically. See [data-model.md](data-model.md).

**Your ticket export:** covering the same dates plus at least a week, with the person who raised each ticket, opened and resolved times, and category. Any built-in preset works (`probative presets`).

## How it works

1. **Find returns.** For every claimed resolution, look for a ticket from the same person within the window, about the same type of issue.
2. **Find the normal return rate.** People come back after human-resolved tickets too, for reasons that have nothing to do with the AI. The same check runs on tickets your agents resolved, in the same months and weighted to the same mix of issue types.
3. **Count only the excess against the AI:**

   did not hold = (return rate after the AI − return rate after people) ÷ (1 − return rate after people)

   Dividing by (1 − return rate after people) scales the excess to the claims that could have come back.
4. **State the uncertainty.** Ranges come from resampling people 999 times, because one person's contacts are not independent. If a leave-one-month-out jackknife shows more spread, the range is widened to it.
5. **Check the result.** The audit reruns with 3- and 14-day windows, with any ticket counting as a return, and with a baseline from the same people only. Large disagreement is a reason for care.

## Audit confidence

| Level | When |
| --- | --- |
| High | At least 500 checked claims, at least 1,000 baseline tickets, at least 60% of people found in both systems, returns matched on issue type, and a range no wider than ±8 points |
| Medium | Any of: 200 to 499 claims, 300 to 999 baseline tickets, 30% to 59% of people found in both systems, issue types that could not be matched, or a range of ±8 to ±15 points |
| Low | Any of: fewer than 200 claims, fewer than 300 baseline tickets, under 30% of people found in both systems, or a range wider than ±15 points |

The report lists every reason that lowered the level.

## What it cannot tell you

- **It can undercount failures.** Someone who gives up on the bot and phones, emails or asks a colleague leaves no ticket, and looks like a resolution that held. Treat the result as a floor unless every channel creates a ticket.
- **It needs the same people in both systems.** If the AI and the ticket system identify people differently, returns are missed. The coverage table shows the share of people found in both; map the same identifier in both files.
- **Human resolutions are the yardstick, not perfection.** The question is whether the AI's resolutions hold as well as your agents' do. Reopened tickets are tracked separately as a quality guardrail in the evidence pack.
- **Claims from the last few days cannot be checked yet.** They are counted as "too recent". Any billing estimate assumes they hold at the same rate.

## Validation

On 180 simulated service desks with a known share of false resolutions (0%, 12% and 30%), the audit was unbiased to within 0.2 percentage points, its 95% ranges contained the truth in 93% to 100% of runs, and with no false resolutions planted it never reported an excess. See [validation.md](validation.md#resolution-audit).
