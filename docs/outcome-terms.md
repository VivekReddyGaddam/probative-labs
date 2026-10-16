# Outcome terms for AI billed per resolution

> **Draft model terms, not legal advice.** Probative Labs is not a law firm. Use these as a starting point for a conversation between your legal counsel, procurement and the vendor. Your counsel should adapt them to your contract, jurisdiction and data protection obligations.

When an AI agent is billed per resolution, the vendor counts the thing you pay for. These terms move three things into the contract: what counts as a resolution, what data you get to check it, and who measures it. Once the definition is written down, every bill can be reconciled against it (`probative reconcile`).

## 1. What counts as a resolution

Pick one of two definitions.

**Option A: per item (strict).**

> A *Verified Resolution* is a conversation that (a) the AI completed without a human agent, (b) started within the billing period, and (c) was not followed, within seven (7) calendar days, by a ticket or other contact from the same end user about the same issue category in the Customer's service management system.

Simple to apply and to dispute item by item. It also counts returns that are about something unrelated, so vendors may ask for a lower price per resolution, or for Option B.

**Option B: adjusted for the normal return rate (statistical).**

> *Verified Resolutions* for a billing period equal the Billed Resolutions multiplied by (1 − the *Excess Return Rate*). The Excess Return Rate is the share of the AI's resolutions followed within seven (7) days by a ticket from the same end user about the same issue category, in excess of the same rate after human-resolved tickets of the same categories in the same month, measured as described in Schedule M.

Fairer to the vendor, because unrelated returns cancel out, but it relies on the published method (Schedule M points to [docs/resolution-audit.md](resolution-audit.md) at a fixed engine version).

**Counting rules for both options:**

- One resolution per conversation. A conversation billed twice counts once.
- A billed item must match a conversation in the Customer's own records by its ID.
- Conversations escalated to a human, abandoned, or marked as tests are not resolutions.
- A conversation counts in the billing period in which it started.

## 2. Data access

> Within five (5) business days after each month end, the Vendor provides, at no extra charge: (a) a conversation-level export for the month with the conversation ID, the end user's identifier as used in the Customer's directory, start time, outcome and topic or category; and (b) billing detail with one line per billed resolution, using the same conversation IDs.

Without the end user's identifier, returns cannot be matched; without item-level billing, the bill can only be reconciled by day.

## 3. Independent measurement

> Verified Resolutions are measured each month by the Customer or by an independent measurer agreed by both parties, which has no financial relationship with the Vendor, using a published method at a stated version. Either party may rerun the measurement on the same data and must obtain the same result.

The open engine records file fingerprints, settings and seeds, so a result can be re-performed with `probative reproduce`.

## 4. Credits and disputes

> Billing errors (items billed more than once, items with no matching conversation, conversations that were not resolved, conversations outside the billing period) are credited in full. Under Option A, each billed item that is not a Verified Resolution is credited at the price per resolution. Under Option B, the credit is the billed amount multiplied by the Excess Return Rate, where the lower end of its 95% range is above zero. The Customer may raise a dispute within thirty (30) days of receiving the bill, with a dispute pack listing the disputed items by the Vendor's conversation IDs.

`probative reconcile --contractual` produces the Option A dispute pack; without `--contractual` it disputes billing errors and reports the Option B figure.

## 5. Changes to the AI

> The Vendor notifies the Customer at least fourteen (14) days before a material change to the models, prompts or knowledge sources behind the service. If the share of Verified Resolutions falls by more than five (5) percentage points for two consecutive months, the parties will review the price per resolution in good faith.

`probative watch --events releases.csv` tests each announced release, and flags unannounced changes too.

## 6. Privacy

> Measurement uses pseudonymized identifiers. Dispute packs and reports shared with the Vendor contain the Vendor's conversation IDs and aggregate results only, and no personal data.

## How Probative Labs fits

- Monthly: `probative reconcile` (the dispute pack) and `probative watch` (silent regressions).
- At renewal: `probative audit` and the evidence pack.
- Before purchase: `probative bakeoff`, so the definition and the data access can be agreed while you still have leverage.

Probative Labs takes no money from AI vendors; see [INDEPENDENCE.md](../INDEPENDENCE.md).
