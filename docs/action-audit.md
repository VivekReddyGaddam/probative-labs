# Action audit: tickets the AI closed

AI agents increasingly act instead of suggesting: they resolve and close tickets with no person involved. An action that has to be undone is not saved work. `probative closures` checks how often the AI's own closures were reversed, compared with your agents' closures.

```bash
probative closures --ai-resolver "AI Agent" --open
```

## What it needs

A ticket export that says who resolved each ticket. The column is found automatically (`resolved_by`, `Resolved by`, `Closed by`; or `--resolver-column`). Name the value that means the AI with `--ai-resolver` (repeat it for several values); if you leave it out, the error lists the most common values. A yes/no column works too: `--ai-flag-column`.

The AI is identified before names are pseudonymized, and agents' names never appear in the result.

## How it works

A closure counts as **reversed** if the ticket was reopened, or the same person opened a ticket about the same type of issue within 7 days. The same check runs on tickets your agents closed, weighted to the AI's mix of issue types and months, and only the excess counts against the AI:

```
did not stick = (reversed after the AI − reversed after people) ÷ (1 − reversed after people)
```

Ranges come from resampling people, widened to a leave-one-month-out jackknife when that is larger, exactly as in the resolution audit.

## Scope

This first version covers the most common agent action on a service desk: closing tickets. Other actions, such as approving access, issuing refunds or changing records, need the system's change history (for example ServiceNow's audit table) and are planned with a design partner that has that data.

## Validation

See [validation.md](validation.md#action-audit).
