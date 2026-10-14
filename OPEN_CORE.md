# What is open source and what is Probative Labs Cloud

Probative Labs' credibility rests on its method. A measurement vendor that grades other vendors' AI cannot ask customers to trust a black box. So the method, and the engine that implements it, are open: any data scientist, auditor or skeptical CFO can read the code, rerun an analysis and get the same answer.

The commercial product, Probative Labs Cloud, sells what enterprises pay for once they trust the method:

- not having to build and maintain connectors;
- running measurement across dozens of AI programs at once;
- meeting their security and governance requirements;
- getting finance-ready outputs without a data team.

## The split

| Capability | Open source (this repo) | Probative Labs Cloud |
| --- | --- | --- |
| Measurement method, evidence-grade definitions, validation suite | ✓ | ✓ |
| File import (CSV, Parquet) with field mapping and presets for ServiceNow, Jira Service Management, Zendesk, Freshservice, BMC Helix and Ivanti exports | ✓ | ✓ |
| Data health monitor | ✓ | ✓ |
| Pseudonymization at ingest, minimum group size | ✓ | ✓ |
| Rule-based work typing, category drift check, mix adjustment | ✓ | ✓ |
| Causal engine: two-group and staggered difference-in-differences, event study, team bootstrap | ✓ | ✓ |
| Diagnostics: pre-trend, placebo dates, placebo outcomes, leave-one-team-out, power | ✓ | ✓ |
| Guardrail library and faster-but-worse block | ✓ | ✓ |
| Value and ROI ranges, capacity vs cash, payback | ✓ | ✓ |
| Rollout designer: power calculator, wave planner, pre-registration | ✓ (CLI) | ✓ (guided UI) |
| Evidence pack (HTML, Markdown, JSON) and method appendix | ✓ | ✓ (branded PDF) |
| Live, read-only connectors: ServiceNow, Jira Service Management, Zendesk, Freshservice, BMC Helix, Ivanti | | ✓ |
| Hosted, multi-tenant app with program registry and portfolio view | | ✓ |
| Cost ledger: contract, seat and model-usage import, allocation rules | | ✓ |
| Unit values with finance sign-off and change history | | ✓ |
| Pre-registrations stored append-only with server timestamps | | ✓ |
| LLM work typing with audit against a hand-labeled sample | | ✓ (v2) |
| Renewal memos, quarterly board report, CFO exports | | ✓ |
| Single sign-on (OIDC), roles, audit log, retention controls | | ✓ |
| Single-tenant and customer-hosted deployment | | ✓ |
| Methodologist support and rollout design workshops | | ✓ |

## Principles

1. **Every number Probative Labs Cloud shows can be reproduced with the open engine.** Cloud results record the engine version, data hashes and configuration, and Cloud never runs a private fork of the method.
2. **Method improvements land in open source first.** That includes new designs, diagnostics and grade definitions. The paid product competes on integration, scale and service, not on hidden statistics.
3. **No feature removal.** Something released as open source stays open source.
4. **Pricing is per measured program, never a share of the savings found.** A share of the savings would reward finding value, which is the opposite of neutral measurement.

## Licensing

- This repository: Apache License 2.0.
- Probative Labs Cloud: proprietary.
- "Probative Labs" name and logo: trademarks. Forks are welcome under a different name.
