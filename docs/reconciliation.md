# Bill reconciliation

When an AI agent is billed per resolution, `probative reconcile` checks every billed item against your own records and writes a dispute pack you can send to the vendor.

```bash
probative reconcile vendor-bill.csv --period 2026-09 --contractual --open
```

It uses the project's tickets and the AI's conversations (`data.self_service`), or `--claims` and `--tickets`. Output in `out/reconcile/`: `dispute-pack.html`, `dispute-pack.md`, `disputed-items.csv` (one line per disputed item, by the vendor's own IDs, with no personal data) and `reconciliation.json`.

## What the bill needs

One line per billed resolution, with the vendor's item ID (usually the conversation ID). A date and an amount help. Common column names (`Conversation ID`, `Resolution ID`, `Resolved at`, `Date`, `Amount`, `Price`) are recognized; map others with `--bill-column item_id="Your column"`.

If the bill has no item IDs, only a date and a count per line, it is reconciled by day: billed counts against the resolutions in your records that day.

## What is checked, line by line

| Reason | Meaning |
| --- | --- |
| Billed more than once | The same item ID appears again |
| No matching conversation in your records | The item ID is not in your conversation log |
| The conversation was not resolved | Your records show it escalated or abandoned |
| Conversation outside the billing period | It started before or after the period |
| Did not hold under the contract's definition | With `--contractual`: the same person opened a ticket about the same type of issue within the window |

Items billed for conversations your records show as resolved, but that are not on the bill, are reported too, in the vendor's favour.

## With and without a contract definition

- **`--contractual`** (or `audit.contractual: true`): the contract defines a resolution as no repeat ticket within the window (Option A in [outcome-terms.md](outcome-terms.md)), so every item that came back is disputed.
- **Without it:** only billing errors are disputed line by line. The resolution audit's comparison with your agents' resolutions gives a negotiation figure: the amount paid for resolutions that did not hold beyond the normal return rate (Option B).

The two differ: the per-item definition also counts people who came back about something unrelated, which the comparison nets out.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--period` | from the bill's dates | `YYYY-MM`, or `YYYY-MM-DD:YYYY-MM-DD` (end exclusive). Bill lines dated outside it are left out |
| `--price` | from the bill's amounts | Price per resolution when the bill has no amounts |
| `--window`, `--match` | 7, `same_type` | As in the resolution audit |
| `--contractual` | from `audit.contractual` | Dispute every item that did not hold |

## Validation

On simulated bills with known errors, every planted duplicate, unknown ID and billed escalation was found (`tests/test_billing.py`).
