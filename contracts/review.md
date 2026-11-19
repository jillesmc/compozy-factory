# Contract: `review` (review and approval evidence)

Review and approval evidence only; the reviewer is always the user-level `factory-reviewer`, verified
by `factory_guard.py reviewer` before every dispatch (BR-16). Promotion PR creation is in `code`.

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| review.context | pr | {head_sha, files[], commentable, conventions (base REVIEW.md and factory.toml), app_reviews[], app_threads[]} | pr_not_open, repo_not_allowed, policy_unapproved, review_unavailable |
| review.submit | pr, head, findings[{path, line, severity, title, body}], summary, rereview? | {event (derived from severities), pass} | head_moved, already_reviewed_head, rereview_not_eligible, too_many_findings, field_too_long, repo_not_allowed, policy_unapproved |
| review.gate | pr, kind (delivery/promotion) | {ready, head_sha, reasons[]} | repo_not_allowed, policy_unapproved, review_unavailable |
