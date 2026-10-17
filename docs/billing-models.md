# Every billing model, checked the same way

AI for the service desk is billed in different units. Each model hides waste in a different place, but every one can be brought to the same measure: **what each resolution that held cost**. That is the number to compare across vendors, contracts and renewals.

| Billing model | Examples (public pricing, October 2026; check your contract) | Command | What is checked |
| --- | --- | --- | --- |
| Per resolution | Intercom Fin, Zendesk AI agents | `probative reconcile --model resolution` | Billed twice, no matching conversation, not resolved, outside the period and, under a contract definition, did not hold |
| Per conversation | Salesforce Agentforce, Jira Service Management virtual service agent | `probative reconcile --model conversation` | Billed twice, no matching conversation, outcomes the contract does not bill, the included allowance, and what the conversations bought |
| Per session | Freshservice Freddy AI Agent | `probative reconcile --model session` | More sessions billed than your records hold, and a second item billed inside one session |
| Usage units | ServiceNow Now Assist (assists), Microsoft Copilot Studio and Agentforce (credits) | `probative usage` | Units by feature and instance, units used outside production, units spent on conversations that did not resolve, and how fast the pool is going |
| Per seat or per employee | Moveworks, Microsoft 365 Copilot, assist seats | `probative seats` | The period's share of the fee, licensed against active users, and the cost of seats nobody used |

## The one number: cost per resolution that held

```
cost per resolution that held = what the period cost ÷ resolutions that held
resolutions that held          = claimed resolutions × (1 − share that did not hold)
```

The share that did not hold comes from the [resolution audit](resolution-audit.md): the return rate after the AI's claimed resolutions, less the normal return rate after your agents' own resolutions of the same types. The range comes from the audit's range. A per-resolution price of $1 can mean $1.20 per resolution that held; a per-conversation price of $2 can mean more than $3, because every escalation and abandoned chat is billed too.

## Per resolution, per conversation and per session

```bash
probative reconcile bill.csv --model resolution --claims conversations.csv --tickets incidents.csv --preset servicenow
probative reconcile bill.csv --model conversation --billable resolved,escalated --included 1000 --price 0.30 ...
probative reconcile bill.csv --model session --price 0.90 --session-hours 24 ...
```

The bill can list one item per line (with the vendor's conversation or session ID) or a date and a count per line. Item-level bills are matched line by line; count-only bills are reconciled by day against your own records.

- `--billable` lists the outcomes the contract charges for. Per-resolution bills default to resolutions; the others default to every outcome, as most per-conversation contracts do.
- `--included` is the number of items the plan includes each month. An error then costs money only when it pushes the total past the allowance.
- Per session, one person's conversations within `--session-hours` of their first count as one session. A vendor that counts calendar days instead of 24 hours bills more sessions than your records hold, and the reconciliation shows it day by day.

Every dispute pack also shows **what the money bought**: the billed items that held, came back as a ticket, were handed to a person, or were abandoned. Per-conversation and per-session pricing charges for all of them. They are not billing errors, but they are why the cost per resolution that held is higher than the price of one conversation.

## Usage units: assists, credits, actions

```bash
probative usage now_assist_usage.csv --nonprod dev_usage.csv \
  --claims interactions.csv --claims-preset servicenow_va --tickets incidents.csv --preset servicenow \
  --unit assists --pool 500000 --pool-price 250000 --term-start 2026-01-01
```

The usage export needs a date and a number of units on each row; a feature (skill or agent) and an instance make it more useful. ServiceNow's Gen AI usage log (`sys_gen_ai_usage_log`, exported as CSV with Created, Assists, Skill config and User) maps automatically, as do credit reports with columns such as Billed Copilot Credits.

You get units and their cost by feature; units used in development and test instances (names containing dev, test, uat, qa, sandbox, staging or demo, or files passed with `--nonprod`); the units the AI's conversations used, by what those conversations delivered; the cost per resolution that held; and, given the pool and the contract start, when the pool runs out at the last 30 days' pace, or how much of it will go unused.

When the usage export does not link units to conversations, the cost per resolution that held divides all production units by the resolutions that held: an upper bound, because some units go to features for your agents (summaries, drafting), whose value the evidence pack measures as time saved per ticket.

## Seats

```bash
probative seats --fee 150000 --seats 1000 --seat-label employees --term-start 2026-01-01 \
  --claims conversations.csv --tickets incidents.csv --preset servicenow --period 2026-09
```

The fee is spread over the term by day. Active users are the people with a conversation in the period, or the users in a usage export (`--usage`). For seats used by your agents rather than by employees, leave out the conversations and compare the fee with the hours saved in the evidence pack.

## ServiceNow: Virtual Agent and Now Assist

ServiceNow bills Now Assist in assists drawn from a contracted pool, not per resolution. What its reporting counts as resolved still matters at renewal, because it is the basis of the value story. The `servicenow_va` conversation preset reads the Interactions table (`interaction.list`, filtered to Virtual agent = true; CSV columns number, opened_for, opened_at, state, state_reason, agent_chat and type) and derives each conversation's outcome:

| ServiceNow state | State reason | Outcome |
| --- | --- | --- |
| Closed Complete, no live agent | Topic Complete, Auto Closed, No User Response, User Ended, Left for search | Resolved (as ServiceNow's reporting counts it) |
| Any, with Agent chat = true; or Agent Ended, Left Before Engagement | | Escalated |
| Closed Abandoned, or Faulted | No Activity, Error | Abandoned |

The resolution audit then shows, for each state reason, how often the person came back. A conversation closed because the person stopped responding is not the same as one where the topic finished, and the report keeps them apart.

The presets follow ServiceNow's documented field names. They are tested on simulated exports; check the column names on your instance, and map any that differ with `--claims-column` or `--usage-column`.

## Limits

- Platforms define a resolution in their own words; the presets and `--resolved-value` (which takes several values, separated by commas) decide which outcomes count as claimed resolutions. The report lists the values it used.
- The usage export may not link units to conversations; then the cost per resolution that held is an upper bound.
- Non-production instances are recognised by name; check the instance list in the report.
- A session's length differs between vendors; set `--session-hours` to the contract's definition.
