# Contributing to Probative Labs

Thanks for helping make AI measurement honest.

## Development setup

```bash
git clone https://github.com/VivekReddyGaddam/probative-labs
cd probative-labs
uv sync --group dev
uv run pytest
uv run ruff check src tests && uv run ruff format --check src tests
```

## What we look for

- **Method changes need evidence.** Any change to an estimator, diagnostic, threshold or grade rule must come with tests on simulated data with planted effects, including a zero effect. Run `uv run probative validate --runs 30` before and after, and include both summaries in the pull request.
- **No silent numbers.** If a result cannot be supported, the code must say so (grade D or "no number") rather than produce a figure.
- **Privacy first.** Never log, store or render a person-level identifier. New data fields that identify people go in `schema.PERSON_COLUMNS`.
- **Plain language in outputs.** Evidence packs are read by finance leaders. Write labels and messages a non-statistician can act on.
- **Small, focused pull requests**, with tests.

## New function playbooks

A playbook defines one function's unit of work, metrics, guardrails, placebo metrics and known traps (see `src/probative/playbooks/service_desk.py`). Open an issue first to agree on the metric definitions and guardrails.

## Code of conduct

Be kind, be specific, assume good faith. Harassment of any kind is not tolerated.

## Licensing of contributions

By contributing you agree that your contribution is licensed under the Apache License 2.0.
