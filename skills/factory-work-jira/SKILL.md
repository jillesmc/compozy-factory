---
name: factory-work-jira
description: Factory adapter for the work role on Jira (work item with required fields, transitions, comments). Follow contracts/work.md.
role: work
provider: jira
requires_extensions: []
requires_roles: {}
schema:
  site: {"type": "string", "required": true}
  project: {"type": "string", "required": true}
  assets_workspace: {"type": "string", "required": true}
  issuetype: {"type": "table", "required": true}
  fields: {"type": "table", "required": true}
  defaults: {"type": "table", "required": true}
  tipo_atividade: {"type": "table", "required": true}
  tipo_de_valor: {"type": "table", "required": true}
  plataformas: {"type": "table", "required": true}
key_format: {"regex": "^[A-Z][A-Z0-9]+-\\d+$", "title_prefix": "{key}: ", "commit_scope": "{key}", "branch_prefix": "{key}-"}
---

# work / jira

Values come from `[roles.work.jira]` of `.compozy/factory/factory.toml`: `{site}`, `{project}`,
`[roles.work.jira.issuetype]` (`bugfix`, `feature`) and the field tables. Jira goes through the
`twg jira workitem ...` CLI (`get --fields`, `update --fields-json`, `transitions query`,
`transition --transition-id`, `comment`); the Atlassian MCP tools on site `{site}` are an
alternative. A tool or CLI failure is `work_unavailable`; a 4xx on a write is `work_rejected`.

Field values never come from the work key or the PR: they come from the issue's facts block
(`factory.facts/v1`: `kind`, `attached_doc`, `origin`, `touches_ci`). Build the payload with

```console
$ python3 -I -B ~/.compozy/factory/adapters/work-jira/fields.py --facts '<facts JSON>' [--doc-yes <option id>] [--value-type instrumentacao]
```

run from the workspace root (always `python3 -I -B`: only the digested script runs, nothing from its
directory or the user site, and no bytecode is written into the adapter tree). `--doc-yes` is the id of the "yes" option (`Sim`/`Yes`) in the
`documentacao_anexa` field's allowed values (read them with `twg jira workitem get <KEY> --fields <id>`
or the field metadata); it is required when `attached_doc` is true. `--value-type instrumentacao`
when the item is an instrumentation fix. Assets fields are always sent as full objects
`{"workspaceId", "id": "<workspace>:<objectId>", "objectId"}`; the `{"key": "KEY-…"}` form is accepted
by Jira without error and silently dropped.

## work.create

Create in project `{project}` with `issuetype` from `[roles.work.jira.issuetype]` by `kind`, summary =
the issue title without prefix, description = issue body + issue link, and **every** field of
`fields.py` at creation (the transitions require them). Read the card back with the field ids and
repair any empty one (`work.fields_repair`). Return the key `{project}-<n>`; the caller retitles the
issue with `work.key_format` and adds the body line `Jira: <key>`.

## work.state

`twg jira workitem get <KEY> --fields status,<every id of [roles.work.jira.fields]>`. Map the status
category to `todo`/`in_progress`/`done`, the Blocked status to `blocked`, Canceled to `canceled`;
list every `[roles.work.jira.fields]` key whose value is empty.

## work.fields_repair

Read the item first: `twg jira workitem get <KEY> --fields <every id of [roles.work.jira.fields]>`.
Build the payload with `fields.py --facts '<facts>' --current '<the fields object read>'` (plus
`--doc-yes`/`--value-type` only if those fields are empty): it holds **only the required fields that
are empty now**; a populated field is never overwritten. It fails closed: when the read is not an
object holding every requested field id (an error body, another envelope, a partial read) it exits
non-zero with `work_unavailable: …` and prints no payload — report `work_unavailable` and update nothing (the value chosen at creation, e.g. an
`instrumentacao` value type, stays). An empty payload `{}` means nothing to repair. Otherwise
`twg jira workitem update --id <KEY> --fields-json '<payload>'`, then `work.state` again; any field
still empty is `work_rejected`.

## work.transition

Run `work.fields_repair` first (the transitions require the fields). `twg jira workitem transitions
query --id <KEY>`, pick the transition whose target status matches the target by name
(in_progress/done/blocked/canceled), then `twg jira workitem transition --id <KEY> --transition-id <id>`.
No such transition from the current status is `transition_unavailable`; a field it still asks for is
`fields_missing`.

## work.comment

`twg jira workitem comment --id <KEY> --body '<markdown>'`.

## work.key_format

The frontmatter `key_format`: title `WID-1: x`, branch `WID-1-x`, commit scope `WID-1`. The
promotion `key_pattern` is the `regex` (the App's Jira default).

## work.train_close

Per item `{key, facts}` after a successful deploy: a factory item (its PR closes an issue labeled
`factory`) gets `work.fields_repair`, `work.transition` `done` and a `work.comment` with the PR and
deploy links → `done`. Any other item gets only the comment and is reported `left_to_operator`
(change neither its fields nor its status). A failure on one item is `failed` with the reason; go on.
