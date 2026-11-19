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
`twg jira workitem ...` CLI (`create --fields-json`, `get --fields … -o json --output-summary none`, `update --fields-json`, `transitions query`,
`transition --transition-id`, `comment`); the Atlassian MCP tools on site `{site}` are an
alternative. A tool or CLI failure is `work_unavailable`; a 4xx on a write is `work_rejected`.

Field values never come from the work key or the PR: they come from the issue's facts block
(`factory.facts/v1`: `kind`, `attached_doc`, `origin`, `touches_ci`). Build the payload with

```console
$ python3 -I -B ~/.compozy/factory/adapters/work-jira/fields.py --facts '<facts JSON>' [--doc-yes <option id>] [--value-type instrumentacao]
```

run from the workspace root (always `python3 -I -B`: only the digested script runs, nothing from its
directory or the user site, and no bytecode is written into the adapter tree). `--doc-yes` is the id of the "yes" option (`Sim`/`Yes`) in the
`documentacao_anexa` field's allowed values (read them from the field metadata); it is required when `attached_doc` is true. `--value-type instrumentacao`
when the item is an instrumentation fix. Assets fields are always sent as full objects
`{"workspaceId", "id": "<workspace>:<objectId>", "objectId"}`; the `{"key": "KEY-…"}` form is accepted
by Jira without error and silently dropped.

## work.create

Create in project `{project}` with `issuetype` from `[roles.work.jira.issuetype]` by `kind`, summary =
the issue title without prefix, description = issue body + issue link (written to a temp file and
passed as `--description "$(cat <file>)" --description-format markdown`), and `--fields-json` = the
output of `fields.py --facts '<facts>' --create` (no `--doc-yes`). `--create` leaves out the
**post-create fields** `documentacao_anexa` and `utilizacao_ia` (`[roles.work.jira.fields]` ids, values
from `[roles.work.jira.defaults]` / `--doc-yes`): Jira's create screen rejects them with a 400 "not
available in the create context". In the same turn, right after the create, run `work.fields_repair`
on the new key: it reads the item back, sets those two (and repairs any other empty field) and
reads it back again. The transitions require every field, so a create is complete only when that
repair leaves nothing empty. Return the key `{project}-<n>`; the caller retitles the issue with
`work.key_format` and adds the body line `Jira: <key>`.

## work.state

`twg jira workitem get <KEY> --fields status,<every id of [roles.work.jira.fields]> -o json --output-summary none`. Map the status
category to `todo`/`in_progress`/`done`, the Blocked status to `blocked`, Canceled to `canceled`;
list every `[roles.work.jira.fields]` key whose value is empty.

## work.fields_repair

Read the item first as JSON, into a temp file:
`twg jira workitem get <KEY> --fields <every id of [roles.work.jira.fields], comma-separated> -o json --output-summary none > <file>`.
Build the payload with `fields.py --facts '<facts>' --current "$(cat <file>)"` (plus
`--doc-yes`/`--value-type` only if those fields are empty): the read goes to `--current` unchanged, never
retyped or summarized by hand (`fields.py` takes the twg envelope itself: `data` is a list
holding the one item read, its fields directly on it). The payload holds **only the required fields that
are empty now**; a populated field is never overwritten. It fails closed: when the read is not an
object holding every requested field id (an `ok: false` error, another envelope, a partial read) it exits
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

Write the markdown to a temp file, then
`twg jira workitem comment create --issue-id <KEY> --body-format markdown --body "$(cat <file>)"`
(`twg` has no body-file flag; the quoted `$(cat …)` passes the file unchanged). Never inline the
markdown in `--body`.

## work.key_format

The frontmatter `key_format`: title `WID-1: x`, branch `WID-1-x`, commit scope `WID-1`. The
promotion `key_pattern` is the `regex` (the App's Jira default).

## work.train_close

Idempotent per item (a train or a resumed continuation may close the same item twice, R2-1): read `work.state` first and skip the Done transition (and the repair) when it is already `done`; read the item's comments and skip the comment when one already carries this deploy's link. Per item `{key, facts}` after a successful deploy: a factory item (its PR closes an issue labeled
`factory`) gets `work.fields_repair`, `work.transition` `done` and a `work.comment` with the PR and
deploy links → `done` (comments read with `twg jira workitem get <KEY> --fields comment`). Any other item gets only the comment and is reported `left_to_operator`
(change neither its fields nor its status). A failure on one item is `failed` with the reason; go on.
