# Contract: `code` (issues, pull requests, labels, merge, CI rule, promotion)

The code host of the project. One provider (`[roles.code] provider`); its subsection names the
repository and branches. Repository = `[roles.code.<provider>] repo`; PR = the code PR number;
deployment = the code merge SHA (BR-17). Promotion PR creation and identity belong here, not to `review`.

| Operation | Input | Output | Errors |
| --- | --- | --- | --- |
| code.issue_list | state (open/closed), labels[], since? (ISO UTC), fields? (body) | issues[{number, title, url, state, state_reason, closed_at, labels[], body?}], pull requests excluded, all pages | code_unavailable |
| code.issue_label_events | issue | events ordered oldest first [{id, event (labeled/unlabeled), label, actor, created_at}] | not_found, code_unavailable |
| code.issue_get | issue, with_comments (bool) | {number, title, url, state, state_reason, labels[], body, comments?[{author, body, created_at}]} | not_found |
| code.issue_search | query (free text) | open and closed issues and PRs [{number, title, url, state, is_pr}] | code_unavailable |
| code.pr_search | query, state (open/closed/merged/all) | prs[{number, title, url, state, merged}] | code_unavailable |
| code.issue_create | title, body, labels[] | issue number, url | code_rejected |
| code.label_ensure | name, color, description | — (created or updated, idempotent) | code_rejected |
| code.issue_comment | issue or PR number, markdown | comment url | not_found, code_rejected |
| code.issue_edit | issue, title?, body? | — | not_found, code_rejected |
| code.issue_reopen | issue, comment | — | not_found |
| code.issue_close | issue, reason (completed/not_planned), comment | — | not_found |
| code.issue_labels | issue, add[], remove[] | labels[] after the change | not_found |
| code.issue_link_child | parent issue, child issue | — (native sub-issue) | not_found, link_unsupported |
| code.pr_get | pr | {state (open/closed/merged), draft, base, head_sha, head_branch, title, body, commits[{sha, message}], files[], changed_files (the PR's total count; `files` may be shorter), mergeable (mergeable/conflicting/unknown), merge_state (clean/behind/blocked/…), checks[{name, state, url}], merge_commit?} | not_found |
| code.pr_threads | pr | unresolved review threads [{id, path, line, author, url}], all pages | not_found |
| code.pr_update_branch | pr | new head sha | conflict, not_found |
| code.pr_merge | pr, head (full sha), expected_base | merge commit sha; merge commit only, pinned to head, after `factory_guard.py premerge` (BR-20) | head_moved, base_changed, not_mergeable |
| code.promotion_upsert | key_pattern? (regex from `work.key_format`) | {created, number or none, head_sha, included_prs[]} | promotion_conflict, config_invalid, repo_not_allowed, policy_unapproved, rate_limited, code_unavailable |
| code.check_hygiene | pr | violations[] (base, title/commit key, closing keyword in a commit, CI rule `ci_runner` on changed workflows) | not_found |

Errors not listed for an operation are reported as `code_unavailable` (the provider did not answer)
or `code_rejected` (the provider refused the write), with the provider's message.
