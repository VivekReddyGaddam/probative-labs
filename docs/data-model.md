# Data model

One data model covers every function: a **work unit** done by a **team**, possibly touched by an **AI program**, with outcomes, quality events and costs attached. Connectors and file mappings translate source systems into these columns; everything downstream reads only these names. Columns not mapped are dropped at ingest.

## Work units (required)

| Column | Type | Required | Notes |
| --- | --- | --- | --- |
| `unit_id` | string | yes | Pseudonymized at ingest |
| `team_id` | string | yes | Assignment group; the unit of analysis |
| `opened_at` | datetime | yes | Assigns the unit to a week |
| `resolved_at` | datetime | recommended | Resolution time = resolved − opened |
| `closed_at` | datetime | | |
| `effort_hours` | number | recommended | Agent time worked; drives capacity value |
| `category`, `subcategory` | string | recommended | Work typing |
| `priority`, `channel`, `state` | string | | |
| `reopen_count` | number | recommended | Reopen-rate guardrail |
| `reassignment_count` | number | | Reassignment guardrail |
| `escalated` | bool | | Escalation guardrail |
| `csat` | number | | Satisfaction guardrail (any numeric scale) |
| `ai_touched` | bool | | Context only (adoption); never counted as value |
| `requester_id`, `assignee_id` | string | recommended | Pseudonymized at ingest; used for deflection matching and minimum group size |
| `short_description` | string | | Used only for rule-based typing, then dropped |

### ServiceNow preset (`mapping_preset: servicenow`)

| Canonical | ServiceNow field | Transform |
| --- | --- | --- |
| `unit_id` | `number` | |
| `team_id` | `assignment_group` | |
| `channel` | `contact_type` | |
| `effort_hours` | `time_worked` | duration: seconds, `HH:MM:SS` or `1 Day 2 Hours` |
| `escalated` | `escalation` | > 0 |
| `requester_id` | `caller_id` | |
| `assignee_id` | `assigned_to` | |
| `ai_touched` | `u_ai_assisted` | bool (custom field: map your own AI-usage field) |
| `csat` | `u_csat` | (custom field: map your survey score) |
| others | same name | |

Override any entry in `data.mapping`, for example:

```yaml
data:
  mapping:
    csat: survey_score
    effort_hours: {source: work_minutes, transform: minutes_to_hours}
```

Available transforms are `datetime`, `number`, `bool`, `positive_bool`, `duration_hours`, `seconds_to_hours`, `minutes_to_hours` and `string`.

## Exposure (required)

One row per team that received the program. Teams that are not listed, or that have a blank `start_date`, are comparison teams.

| Column | Required | Notes |
| --- | --- | --- |
| `team_id` | yes | Must match work units |
| `start_date` | yes for exposed teams | First day with the AI |
| `end_date` | | If exposure stopped; later weeks are dropped |
| `kind` | | `wave`, `licence`, `feature_switch` or `holdout` |
| `randomized` | | `true` if assignment was by lottery (needed for grade A) |

`probative plan waves` writes this file.

## Self-service sessions (optional)

Enables verified deflection and the false-deflection guardrail.

| Column | Required | Notes |
| --- | --- | --- |
| `session_id` | | |
| `requester_id` | yes | Pseudonymized with the same key as tickets |
| `started_at` | yes | |
| `outcome` | yes | `resolved`, `escalated` or `abandoned` |
| `category`, `subcategory` | recommended | Typed like tickets, for same-topic matching |

## Staffing (optional)

If time worked is not recorded, provide `team_id`, `week_start` and `hours`. Agent hours per ticket are then staffed hours divided by tickets. This cannot be mix-adjusted, and the pack says so.

## Derived columns

The pipeline adds these columns. You never map them yourself.

| Column | Meaning |
| --- | --- |
| `resolution_hours` | Resolved minus opened, in hours |
| `reopened` | Reopen count is above zero |
| `escalated_rate` | `escalated` as a 0/1 number |
| `after_hours` | Opened before 08:00 or after 18:00, or on a weekend (placebo outcome) |
| `period` | Week index from the Monday on or before the window start |
| `work_type` | Assigned type |
