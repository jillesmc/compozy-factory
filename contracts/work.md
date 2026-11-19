# Contract: `work` (work item)

The record the team tracks. When `[roles.work] provider` equals `[roles.code] provider`, the work
item is the code issue itself and no second record is created (BR-5). Facts are the
`factory.facts/v1` block of the issue (`kind`, `attached_doc`, `origin`, `touches_ci`); the adapter
maps them to provider fields and never derives a field from the work key.

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| work.create | issue ref, title, body, facts | work key | work_unavailable, work_rejected |
| work.state | work key | {state (todo/in_progress/done/blocked/canceled), missing required fields[]} | not_found |
| work.fields_repair | work key, facts | fields written[] | work_unavailable, work_rejected |
| work.transition | work key, target (in_progress/done/blocked/canceled) | new state | transition_unavailable, fields_missing |
| work.comment | work key, markdown | — | work_unavailable |
| work.key_format | — | {regex, title_prefix, commit_scope, branch_prefix} (frontmatter `key_format`) | — |
| work.train_close | items[{key, facts}] | per item {key, outcome (done/left_to_operator/failed), reason} | work_unavailable |

`key_format` values use `{key}` (the work key) and `{id}` (the key without a leading `#`).
