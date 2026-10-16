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

### Platform presets

Set `data.mapping_preset` to read a platform's export without writing a mapping. Run `probative presets` to list them.

| Preset | Platform and export | Column names checked against |
| --- | --- | --- |
| `servicenow` | ServiceNow incident or request list export, or the Table API | ServiceNow field names |
| `jira_service_management` | Jira Service Management, issue search › Export › CSV (all fields) | Typical exports: check yours |
| `zendesk` | Zendesk ticket CSV export (Admin Center › Account › Tools › Reports) | Zendesk's documented export columns |
| `freshservice` | Freshservice ticket export (CSV) | Typical exports: check yours |
| `bmc_helix` | BMC Helix ITSM incidents (HPD:Help Desk field names, or report exports) | Typical field names: check yours |
| `ivanti` | Ivanti Neurons for ITSM incidents (business object field names, or grid exports) | Ivanti's Incident business object reference |
| `canonical` | Files already using the column names above | — |

Each field can list several candidate column names, and the first one present wins. Matching ignores case and extra spaces, so `Created At` matches `Created at`. If a required column is missing, the error names every column it looked for.

Notes by platform:

- **Jira Service Management** has no assignment group. The team comes from `Custom field (Team)`, else `Team`, else the first `Component/s` value. Map your own team field if you route work differently. `Time Spent` is read as seconds.
- **Zendesk** exports carry no agent time, so measure resolution time (`primary_metric: resolution_hours`) or add a staffing file. `Satisfaction Score` becomes 1 for good, 0 for bad and missing otherwise. `Group stations` minus one gives reassignments.
- **Freshservice** `Time Tracked` is read as hours and minutes (`HH:MM`).
- **BMC Helix and Ivanti** exports have no agent time by default. The requester is read from `Person ID` (Helix) or `ProfileLink_RecID` (Ivanti) where present.

The ServiceNow preset in detail:

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

Override any entry in `data.mapping`, with a single column name or a list of candidates:

```yaml
data:
  mapping_preset: jira_service_management
  mapping:
    team_id: "Custom field (Support team)"
    csat: ["Custom field (Satisfaction)", "Satisfaction"]
    effort_hours: {source: work_minutes, transform: minutes_to_hours}
```

Available transforms are `datetime`, `number`, `bool`, `positive_bool`, `duration_hours` (seconds, `HH:MM:SS`, `HH:MM` or `1 Day 2 Hours`), `seconds_to_hours`, `minutes_to_hours`, `minus_one`, `satisfaction_good_bad` and `string`.

To try a preset without real data, `probative simulate data --format zendesk` writes a simulated export in that platform's format, and `probative demo --format zendesk` runs the whole analysis on it.

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

## Self-service sessions and AI claims (optional)

Virtual-agent sessions, or any export of the resolutions an AI claims. They enable verified deflection, the false-deflection guardrail and the resolution audit (`probative audit`).

| Column | Required | Notes |
| --- | --- | --- |
| `session_id` | | |
| `requester_id` | yes | The same identifier the ticket system uses for the person (email or employee ID). Pseudonymized with the same key as tickets |
| `started_at` | yes | |
| `outcome` | for the analysis; optional for the audit | `resolved`, `escalated` or `abandoned`. For the audit, rows whose outcome is not `resolved` are skipped; with no outcome column, every row is a claimed resolution |
| `category`, `subcategory` | recommended | Use the same values as tickets, so a return can be matched to the same type of issue |

For the audit, common export names are recognized automatically: `Requester`, `Requester ID`, `User`, `User ID`, `Requester Email`, `User Email` or `Email` for the person; `Started`, `Start time`, `Created`, `Created at` or `Timestamp` for the time; `Topic` for the category. Map anything else with `--claims-column field="Your column"`.

## Vendor bills (for `probative reconcile`)

One line per billed resolution: `item_id` (the vendor's conversation or resolution ID, matching `session_id` in your conversation log), and ideally `billed_at` and `amount`. Without item IDs, give `billed_at` and `quantity` per line and the bill is reconciled by day. Common names are recognized: `Conversation ID`, `Resolution ID`, `Resolved at`, `Date`, `Amount`, `Price`, `Count`, `Quantity`.

## Who resolved a ticket (for `probative closures`)

A "resolved by" column in the ticket export (`resolved_by`, `Resolved by`, `Closed by`), or a yes/no column marking tickets the AI closed. It is read only to mark AI closures, before pseudonymization, and never stored.

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
