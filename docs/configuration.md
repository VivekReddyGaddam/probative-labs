# Configuration reference (`probative.yaml`)

One file per AI program. Relative paths resolve against the file's folder. `probative init` writes a commented starter file.

## `program`

| Key | Meaning |
| --- | --- |
| `id`, `name` | Identifier and display name |
| `vendor`, `tool`, `models` | What is being measured |
| `function` | e.g. `it_service_management` |
| `owner` | Accountable owner (a role is fine) |
| `contract_ref` | Contract or order-form reference |

## `hypothesis` (registered)

| Key | Meaning |
| --- | --- |
| `statement` | What the program should change, by how much, by when |
| `primary_metric` | `effort_hours` (agent hours per ticket) or `resolution_hours` |
| `expected_effect` | Relative change hoped for, e.g. `-0.10` |
| `by` | Date by which the effect should show |

## `data`

| Key | Meaning |
| --- | --- |
| `work_units` | A file, or a list of files (CSV or Parquet) |
| `mapping_preset` | `servicenow`, `jira_service_management`, `zendesk`, `freshservice`, `bmc_helix`, `ivanti` or `canonical` (see [data-model.md](data-model.md#platform-presets)) |
| `mapping` | Per-field overrides: `canonical: source` or `{source, transform}` |
| `exposure` | Rollout file |
| `self_service`, `self_service_mapping` | Optional virtual-agent sessions |
| `teams` | Optional team list (used by `plan waves` for stratification) |
| `staffing` | Optional staffed hours per team-week |

## `typing`

| Key | Default | Meaning |
| --- | --- | --- |
| `fields` | `[category, subcategory]` | Fields joined into a type label |
| `rules` | `[]` | Ordered rules: `{name, when: {field: value \| [values] \| "re:regex"}}` |
| `min_share` | `0.01` | Types below this share of baseline volume fold into `other_label` |
| `other_label` | `Other` | |

## `analysis`

| Key | Default | Meaning |
| --- | --- | --- |
| `start`, `end` | data range | Analysis window (registered) |
| `design` | `auto` | `auto`, `staggered` or `two_group` (registered) |
| `randomized` | from exposure | Override the randomization flag |
| `bootstrap_reps` | 499 | Team bootstrap draws |
| `seed` | 20261014 | Makes results reproducible |
| `confidence` | 0.95 | Interval level (registered) |
| `min_group_size` | 5 | Teams with fewer people are left out |
| `min_teams` | 8 | Below this, no number |
| `pretrend_weeks` | 8 | Leads in the pre-trend test |
| `placebo_shift_weeks` | auto | How far the placebo rollout is moved |
| `placebo_outcomes` | `[after_hours]` | Outcomes the AI cannot affect (registered) |
| `censoring_threshold` | 0.05 | Trailing weeks with more unresolved records than this are dropped |
| `min_type_units` | 200 | Minimum AI units for a per-type breakdown row |

## `guardrails` (registered)

Override any playbook default, or disable one:

```yaml
guardrails:
  reopen_rate: {threshold: 0.02}
  csat: {enabled: false}
  reassignments: {threshold: 0.05, kind: relative}
```

## `value`

| Key | Meaning |
| --- | --- |
| `currency` | Display currency |
| `loaded_hourly_cost` | Range: `{low, mid, high}`, `[low, mid, high]` or a number |
| `realization_rate` | Share of freed hours provably redeployed, 0 to 1 (default 0) |
| `false_deflection_cost_hours` | Extra hours per false deflection |
| `realized_savings` | `[{description, amount, evidence}]`: money that provably moved |
| `owner`, `agreed_on` | Who agreed these numbers, and when |
| `simulations` | ROI simulations (default 10,000) |

## `costs`

A list of `{component, description, amount, basis, source}`.

- `component` is one of `license`, `model_usage`, `infrastructure`, `build_integration`, `change_training`, `run_govern`, `human_oversight`, `quality` or `other`.
- `basis` is one of `period`, `annual` (prorated), `monthly` (multiplied) or `one_time`.
- `amount` can be a range.

## `claimed`

A list of `{source, hours_saved, value, note}`: vendor or dashboard estimates shown beside the measured result (grade E).

## `registration`

Path of the registration file (default `registration.yaml`).
