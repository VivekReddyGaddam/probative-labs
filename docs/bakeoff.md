# Pre-purchase bake-off

Vendor proofs of concept are usually set up and graded by the vendors. A bake-off runs two or three vendors at the same time on your own teams, assigned by lottery, with control teams that keep working as before, and measures each vendor the same independent way.

## 1. Plan

```bash
probative bakeoff plan teams.csv --vendors "Vendor A,Vendor B" --start 2026-11-02 --weeks 8 --control 0.25
```

This writes `bakeoff.csv`: each team's arm (a vendor or `control`), the start date and the lottery seed. It also prints each vendor's chance of detecting the effect you care about (`--effect`, default −10%); pass `--config` to calibrate that from your own history. If the chance is below 80%, add teams or weeks before you start.

## 2. Configure

Add a `bakeoff` section to `probative.yaml`:

```yaml
bakeoff:
  assignment: bakeoff.csv
  start: 2026-11-02
  end: 2026-12-27
  vendors:
    - name: Vendor A
      annual_price: 120000          # optional: value for money
      claims: data/vendor_a.csv     # optional: audit this vendor's claimed resolutions
    - name: Vendor B
      annual_price: {low: 90000, mid: 100000, high: 110000}
```

The rest of the project (tickets, guardrails, value inputs) is used as for any evidence pack.

## 3. Analyze

```bash
probative bakeoff analyze --open
```

Each vendor is measured against the control teams over the trial weeks, with the same estimator, guardrails and team-level ranges as an evidence pack. The decision pack shows:

- each vendor's effect, range, grade and guardrails, and, where given, the share of its claimed resolutions that did not hold;
- value for money: hours and capacity value scaled to a year, against the quoted price;
- the decision.

## How the decision is made

1. A vendor is **eligible** only if its effect excludes zero, its evidence grade counts and no guardrail breached.
2. The eligible vendor with the largest improvement **leads**.
3. It is a **clear winner** only if its range does not overlap the runner-up's. This is conservative: a real but small difference can read as "no clear winner".
4. With no clear winner, decide on price and terms (the value-for-money leader is named), or extend the trial.

Because no vendor registered its own hypothesis, bake-off results are graded B at best, even though assignment was random.

## Validation

See [validation.md](validation.md#bake-off).
