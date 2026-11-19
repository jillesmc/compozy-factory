---
name: factory-review-github-app-review
description: Factory adapter for the review role through the GitHub App (review context, review submission, merge gate). Follow contracts/review.md.
role: review
provider: github-app-review
requires_extensions: ["github-app-review"]
requires_roles: {"code": "github"}
schema: {}
---

# review / github-app-review

The repository is `[roles.code.github] repo` (`{repo}`); branches, promotion pair and approvers come
only from the App's approved policy, never from `factory.toml`. The reviewer is always the user-level
`factory-reviewer`: run `python3 ~/.compozy/factory/tools/factory_guard.py reviewer <workspace>`
before every review or re-review dispatch and dispatch nothing on `refused:`. A tool error's `Cause:`
code is the operation error (`policy_unapproved`, `repo_not_allowed`, `pr_not_open`, …).

## review.context

`ext__github_app_review__pr_context` with `{"repo": "{repo}", "number": <pr>}`.

## review.submit

`ext__github_app_review__submit_review` with `repo`, `number`, `head_sha`, `summary`, `findings`
(empty list when none) and `"rereview": true` on pass 2. The tool derives the event from the
severities; never choose it.

## review.gate

`ext__github_app_review__merge_gate` with `{"repo": "{repo}", "number": <pr>, "kind": "<delivery|promotion>"}`;
ready only with `ready: true` and `head_sha` equal to the head you verified.
