---
name: factory-work-github
description: Factory adapter for the work role on GitHub — the code issue is the work item. Follow contracts/work.md.
role: work
provider: github
requires_extensions: []
requires_roles: {"code": "github"}
schema:
  project: {"type": "integer", "required": false}
key_format: {"regex": "^#\\d+$", "title_prefix": "{key}: ", "commit_scope": "{key}", "branch_prefix": "{id}-"}
---

# work / github

The work item is the code issue itself (BR-5): never create a second record. The work key is `#<n>`
of the issue in `[roles.code.github] repo`. Use the code adapter's operations (`factory-code-github`)
for every GitHub call. States are labels: `factory:in-progress`, `factory:blocked`; done = closed as
completed; canceled = closed as not planned. Facts live in the issue's `factory.facts/v1` block and
need no provider field.

## work.create

Return `#<n>` of the issue ref; create nothing.

## work.state

`code.issue_get`: closed `completed` → `done`, closed `not_planned` → `canceled`, label
`factory:blocked` → `blocked`, label `factory:in-progress` → `in_progress`, else `todo`. Missing
required fields: none, unless the issue has no `factory.facts/v1` block (`facts` missing).

## work.fields_repair

Write the given facts into the issue body's `factory.facts/v1` block
(`python3 ~/.compozy/factory/tools/factory_envelope.py facts` then `code.issue_edit`). No other field.

## work.transition

`in_progress`: `code.issue_labels` add `factory:in-progress`, remove `factory:deliver`, `factory:blocked`.
`blocked`: add `factory:blocked`, remove `factory:in-progress`. `done`: `code.issue_close` reason
`completed`. `canceled`: `code.issue_close` reason `not_planned`. With `project` set, also set the
item's Status field in that GitHub Project (`gh project item-edit`).

## work.comment

`code.issue_comment` on the issue.

## work.key_format

The frontmatter `key_format`: title `#194: x`, branch `194-x`, commit scope `#194`. The promotion
`key_pattern` is the `regex`; with it the App lists the issues each included PR closes.

## work.train_close

Idempotent per item (a train or a resumed continuation may close the same item twice, R2-1): read `work.state` first and skip the Done transition (and the repair) when it is already `done`; read the item's comments and skip the comment when one already carries this deploy's link. Comments come from `code.issue_get` with comments.
Per item: a factory item (its issue is labeled `factory`) is closed by the deploy step of the code
role, so `work.transition` `done` here is the same closure; any other item is `left_to_operator`.
