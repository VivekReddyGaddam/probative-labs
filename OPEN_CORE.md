# What is open source and what is Probative Labs Cloud

Probative Labs is an independent check on what AI vendors claim. A checker that asks for blind trust is no better than the claims it checks, so the method, and the engine that implements it, are open: any data scientist, auditor or skeptical CFO can read the code, rerun an analysis and get the same answer.

The commercial product, Probative Labs Cloud, sells what enterprises pay for once they trust the method:

- verification that keeps running, not once a year: live connectors and resolution audits on fresh data;
- peer benchmarks, which only exist because many companies contribute;
- running measurement across a whole AI portfolio, with the cost ledger and finance sign-off;
- meeting security and governance requirements without a data team.

## The split

| Capability | Open source (this repo) | Probative Labs Cloud |
| --- | --- | --- |
| **Plan** | | |
| Power calculator, lottery-based wave planner, pre-registration | ✓ (CLI) | ✓ (guided UI) |
| Measurement plan to sign before go-live | ✓ | ✓ |
| Registrations stored append-only with server timestamps | | ✓ |
| **Verify: what changed** | | |
| Measurement method, evidence-grade definitions, validation suite | ✓ | ✓ |
| File import (CSV, Parquet) with presets for ServiceNow, Jira Service Management, Zendesk, Freshservice, BMC Helix and Ivanti exports | ✓ | ✓ |
| Data health monitor, pseudonymization at ingest, minimum group size | ✓ | ✓ |
| Work typing, category drift check, mix adjustment | ✓ | ✓ |
| Causal engine: two-group and staggered difference-in-differences, event study, team bootstrap | ✓ | ✓ |
| Diagnostics: pre-trend, placebo dates, placebo outcomes, leave-one-team-out, power | ✓ | ✓ |
| Guardrails and the faster-but-worse block | ✓ | ✓ |
| Value and ROI ranges, capacity vs cash, payback | ✓ | ✓ |
| Evidence pack (HTML, Markdown, JSON) and method appendix | ✓ | ✓ |
| **Verify: what the AI claims** | | |
| Resolution audit: claimed resolutions that did not hold, against a human baseline, with ranges and confidence | ✓ | ✓ |
| Resolution audits on data synced from live connectors, with the amount paid for resolutions that did not hold | | ✓ |
| **Benchmark** | | |
| Anonymous contribution format and `probative benchmark` | ✓ | ✓ |
| Peer benchmarks: your result against other organizations running the same AI | | ✓ |
| **Run it at scale** | | |
| Live, read-only connectors: ServiceNow, Jira Service Management, Zendesk, Freshservice, BMC Helix, Ivanti | | ✓ |
| Hosted program registry and portfolio view | | ✓ |
| Cost ledger: contract, seat and usage import | | ✓ |
| Unit values with finance sign-off and change history | | ✓ |
| Renewal memos and board reports | | Planned |
| Single sign-on (OIDC), roles, audit log, retention controls | | ✓ |
| Single-tenant and customer-hosted deployment | | ✓ |
| Methodologist support and rollout design workshops | | ✓ |

## Principles

1. **Every number Probative Labs Cloud shows can be reproduced with the open engine.** Cloud results record the engine version, data hashes and configuration, and Cloud never runs a private fork of the method.
2. **Method improvements land in open source first.** That includes new designs, diagnostics, audits and grade definitions. The paid product competes on integration, continuity, benchmarks and service, not on hidden statistics.
3. **No feature removal.** Something released as open source stays open source.
4. **Independence.** Pricing is per measured program, never a share of the savings found, and AI vendors cannot pay for results. See [INDEPENDENCE.md](INDEPENDENCE.md).

## Licensing

- This repository: Apache License 2.0.
- Probative Labs Cloud: proprietary.
- "Probative Labs" name and logo: trademarks. Forks are welcome under a different name.
