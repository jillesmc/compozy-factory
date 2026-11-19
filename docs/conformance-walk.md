# Conformance walks (BR-11, US-010)

An adapter is `supported` only with `conformance/<role>-<provider>.json`: every operation of
`contracts/<role>.md` exercised against the real provider with outcome `pass`, and an `adapter_digest`
equal to the installed tree digest. The evidence is one trace per adapter,
`conformance/<role>-<provider>.trace.jsonl`; the record is produced from it.

Most operations are exercised by a normal delivery (E2E-001): the conductor traces them while observing
it. The rest are covered by a **supplemental walk** below. Then `bin/conformance-record` writes and
verifies the record, and the record and trace are committed (a changed adapter file invalidates it).

## Tools

```console
$ bin/conformance-trace <role> <provider> <operation> --row <matrix row> --outcome pass|fail \
      --effect '<provider-side effect observed>' [--today '<today call>'] \
      [--input key=value]... [--call '<tool or argv>' '<target>']...
$ bin/conformance-record <role> <provider> --workspace-root <ws-root>
```

- One trace line per operation call: `{ts, role, provider, operation, inputs, provider_calls
  [{tool|argv, target}], outcome, effect, equivalent_to {row, call}}`. The operation must be in the
  contract and `--row` a row of the spec's behavior → operation matrix for that operation;
  `equivalent_to.call` defaults to that row's today call (`--today` overrides it, e.g. for work/github,
  whose today call was Jira). `effect` is what the provider shows afterwards (`issue #412 labeled
  conformance-walk`, `PR #88 merge commit 1a2b3c4`), the basis of trace equivalence: the same effect as
  today's call.
- `outcome` is whether the adapter behaved as its contract says. A contract error (`not_found` on a
  missing issue, `head_moved`) returned as specified is a `pass`; a wrong result or an undeclared error
  is `fail`. Re-running an operation appends a line; the latest line of an operation decides.
- Never trace secrets: input names that look like credentials become `[redacted]`, token-shaped values
  are masked, and every value is cut at 200 characters. Do not pass issue bodies; pass the issue number.
- `bin/conformance-record` refuses, writing nothing, when an operation has no line or its latest line is
  `fail`, when the installed tree differs from `installed.json` (run `bin/install` first), or when an
  extension in the adapter's `requires_extensions` is not installed. It reads `compozy extension list`
  of the running daemon (read only), takes `provider_site` from the workspace's `factory.toml`, and
  checks the record the way `bin/attach` does: `<role>/<provider> supported`.
- Walk with the adapter version you will record: trace lines are not tied to a tree digest, so after
  changing an adapter, `bin/install` and walk again from an empty trace.

## Rules for every supplemental step

- Values: `{repo}` = `[roles.code.github] repo`; `{project}`/`{site}` = `[roles.work.jira]`;
  `{environment}` = `[roles.deploy.github-deployments] environment` (else `release`); `gh` always as
  `env -u GH_TOKEN gh …` (keyring login), from the workspace root.
- Throwaway targets carry the label `conformance-walk` and the title prefix `[conformance-walk]`, and
  **never** `factory`, `factory:deliver` or another `factory:*` label (the dispatcher would deliver
  them, the auditor would audit them). The only exception is work/github `work.transition`, which by
  contract sets `factory:in-progress`/`factory:blocked` on the throwaway issue; remove them right after.
- **OUTWARD** marks a step that writes to the provider (visible to the team, notifies watchers). Run
  those only with the operator's go-ahead and undo them as written. Unmarked steps are read only.
- Trace each step right after it with `bin/conformance-trace` (row given in the step).

## Coverage

E2E-001 = activation runbook, then one small issue labeled `factory:deliver` delivered through dispatch,
executor, app review, merge, promotion, operator approval, deploy, work Done and auditor marker. It is
assumed to run with `work = jira` and to start from an issue without a work key (so the scribe creates
the Jira item and retitles the issue). An operation marked "E2E-001 if" counts only when that branch
actually ran; otherwise walk it.

| Adapter | Operation | Row | E2E-001 | Supplemental |
| --- | --- | --- | --- | --- |
| signals/newrelic | signals.list_new | 24 | if a finding arrives | S1 |
| | signals.normalize | 25a | if a finding arrives | S2 |
| | signals.query_window | 23 | if a finding arrives | S3 |
| | signals.verify_gone | 25 | if the audit runs in the window | S4 |
| code/github | code.issue_list | 1, 3 | yes (dispatcher `deliver` source, row 1) | — |
| | code.issue_label_events | 2 | yes (`deliver` source: last `factory:deliver` actor) | — |
| | code.issue_get | 4 | yes (dispatcher size check) | — |
| | code.issue_search | 5 | no (scribe intake) | C1 |
| | code.pr_search | 7 | no (scribe intake) | C2 |
| | code.issue_create | 8 | no (issue made by hand) | C3 **OUTWARD** |
| | code.label_ensure | 9 | no | C4 **OUTWARD** |
| | code.issue_comment | 10 | yes (auditor verdict comment) | C5 **OUTWARD** |
| | code.issue_edit | 11 | yes (scribe retitles with the work key) | C6 **OUTWARD** |
| | code.issue_reopen | 12 | no (regression only) | C7 **OUTWARD** |
| | code.issue_close | 13 | yes (train closes the issue) | — |
| | code.issue_labels | 14 | yes (`factory:in-progress` at dispatch) | — |
| | code.issue_link_child | 15 | no (split only) | C8 **OUTWARD** |
| | code.pr_get | 16, 20 | yes (pr-watcher, gatekeeper) | — |
| | code.pr_threads | 17 | yes (pr-watcher, gatekeeper) | — |
| | code.pr_update_branch | 18 | if the PR falls behind `base` | C9 **OUTWARD** |
| | code.pr_merge | 19 | yes (gatekeeper, after `premerge`) | — |
| | code.promotion_upsert | 21 | yes (gatekeeper) | — |
| | code.check_hygiene | 22 | yes (pr-watcher) | — |
| work/jira | work.create | 26 | yes (scribe, on `work: #<n>`) | J1 **OUTWARD** |
| | work.state | 27 | yes (inside transition/train_close) | J2 |
| | work.fields_repair | 28 | yes (before every transition) | J3 **OUTWARD** |
| | work.transition | 29 | yes (`in_progress` at dispatch) | J4 **OUTWARD** |
| | work.comment | 30 | yes (train_close comment) | J5 **OUTWARD** |
| | work.key_format | 31 | yes (title, branch, commit) | J6 |
| | work.train_close | 32 | yes (after deploy success) | J7 **OUTWARD** |
| work/github | all 7 operations | 26–32 | no (E2E-001 runs `work = jira`) | G1–G7 |
| review/github-app-review | review.context | 33 | yes (pr-watcher, reviewer) | R1 |
| | review.submit | 34 | yes (factory-reviewer) | R2 **OUTWARD** |
| | review.gate | 35 | yes (pr-watcher, gatekeeper) | R3 |
| deploy/github-deployments | deploy.wait | 36 | yes (gatekeeper after the promotion merge) | D1 |

## Supplemental walks

### signals/newrelic (read only)

`.compozy/newrelic.toml` gives `<app>` and `<account_id>`. Nothing is written to New Relic.

- **S1 `signals.list_new`** (row 24). Observe one check-in of `factory-sentinel` carrying items from the
  `newrelic/errors`, `newrelic/logs` or `newrelic/issues` sources (the daemon polls them; never poll
  yourself). Effect: the item keys delivered. Undo: none. If no item arrives in the walk window, record
  the source poll result (zero new items is the contract's answer) as the effect.
- **S2 `signals.normalize`** (row 25a). On an S1 item (or a recent error group), build the envelope
  fields per the SKILL.md section with at most 3 `signals.query_window` queries, then
  `python3 ~/.compozy/factory/tools/factory_envelope.py wrap < fields.json`. Effect: the fingerprint
  equals `factory_intake.fingerprint(app, f)` for the same app/class/transaction (today's dedupe key).
  Undo: none (local).
- **S3 `signals.query_window`** (row 23). `ext__newrelic__nrql_query` with
  `{"account_id": <account_id>, "nrql": "SELECT count(*) FROM TransactionError WHERE appName = '<app>' SINCE <ms> UNTIL <ms>"}`
  over the last hour. Effect: the rows, same as the same NRQL through the old tool. Undo: none.
- **S4 `signals.verify_gone`** (row 25). On a closed factory issue with a `factory.finding/v1`
  envelope and a known deploy time: `factory_envelope.py read < body`, then run the verifier query in
  the before/after windows plus the traffic query, as the SKILL.md section says. Effect: the verdict and
  the counts, compared with the auditor's earlier verdict comment on that issue. Undo: none; post
  nothing.

### code/github

Create one throwaway issue in C3 and reuse it (`<t>`); C8 creates a second one (`<t2>`).

- **C1 `code.issue_search`** (row 5). `gh api --method GET search/issues -f q='repo:{repo} conformance-walk'`.
  Effect: issues and PRs with `is_pr`. Undo: none.
- **C2 `code.pr_search`** (row 7). `gh pr list --repo {repo} --state all --search '<a merged PR title word>' --limit 10 --json number,title,url,state`.
  Effect: includes a merged PR. Undo: none.
- **C3 `code.issue_create`** (row 8) **OUTWARD**. `gh api -X POST repos/{repo}/issues --input -` with
  `{"title": "[conformance-walk] code.issue_create", "body": "Throwaway issue of a conformance walk; closed by the walk.", "labels": ["conformance-walk"]}`
  (after C4, so the label exists). Effect: issue `#<t>` with label `conformance-walk`. Undo: close it
  as not planned at the end (C7 leaves it closed); deletion needs a repository admin
  (`gh issue delete <t> --repo {repo} --yes`).
- **C4 `code.label_ensure`** (row 9) **OUTWARD**. `gh label create conformance-walk --repo {repo} --color ededed --description 'conformance walk throwaway' --force`,
  twice (idempotent). Effect: one label, same color. Undo: after the walk,
  `gh label delete conformance-walk --repo {repo} --yes`.
- **C5 `code.issue_comment`** (row 10) **OUTWARD**. `gh api -X POST repos/{repo}/issues/<t>/comments -f body=@-`
  with `conformance walk comment` on stdin. Effect: comment URL on `#<t>`. Undo:
  `gh api -X DELETE repos/{repo}/issues/comments/<comment id>`.
- **C6 `code.issue_edit`** (row 11) **OUTWARD**. `gh issue edit <t> --repo {repo} --title '[conformance-walk] edited'`.
  Effect: new title. Undo: none needed (throwaway).
- **C7 `code.issue_reopen`** (row 12) **OUTWARD**. `gh issue close <t> --repo {repo} --reason "not planned" --comment 'conformance walk'`,
  then `gh issue reopen <t> --repo {repo} --comment 'conformance walk reopen'`. Effect: `#<t>` open with
  the comment. Undo: `gh issue close <t> --repo {repo} --reason "not planned"` at the end. (Also a
  `code.issue_close` line if E2E-001 did not cover it, row 13.)
- **C8 `code.issue_link_child`** (row 15) **OUTWARD**. Create `#<t2>` as in C3, read its id with
  `gh api repos/{repo}/issues/<t2> --jq .id`, then `gh api -X POST repos/{repo}/issues/<t>/sub_issues -F sub_issue_id=<id>`.
  Effect: `#<t2>` is a sub-issue of `#<t>` (a 404/422 is `link_unsupported`, also a pass). Undo:
  `gh api -X DELETE repos/{repo}/issues/<t>/sub_issue -F sub_issue_id=<id>`; close `#<t2>` as not planned.
- **C9 `code.pr_update_branch`** (row 18) **OUTWARD**, only if E2E-001 did not need it. Branch
  `conformance-walk/update-branch` from `{base}~1`, one commit adding `.conformance-walk` (no workflow,
  no protected path), push, `gh pr create --draft --repo {repo} --base {base} --title '[conformance-walk] update branch' --body 'throwaway'`
  (no factory label; the dispatcher only delivers labeled issues). Then
  `gh pr update-branch <p> --repo {repo}` and re-read with `code.pr_get`. Effect: the PR head moved to a
  commit merging `{base}`. Undo: `gh pr close <p> --repo {repo} --delete-branch`.
- Any of rows 1–4, 13, 14, 16, 17, 19, 21, 22 that E2E-001 did not reach: rows 1–4, 16, 17 and 22 are
  read only (run them on `#<t>` or a merged PR); 14 is `gh issue edit <t> --repo {repo} --add-label conformance-walk --remove-label conformance-walk`
  **OUTWARD** on the throwaway; 19 and 21 are covered only by a delivery (no throwaway merge into `{base}`).

### work/jira

All covered by E2E-001 when it starts without a work key. If not:

- **J1 `work.create`** (row 26) **OUTWARD**. Create in `{project}` per the SKILL.md section, summary
  `[conformance-walk] work.create`, facts `{"kind": "bugfix", "attached_doc": false, "origin": "manual", "touches_ci": false}`,
  every field from `python3 -I -B ~/.compozy/factory/adapters/work-jira/fields.py --facts '<facts>'`.
  Effect: key `{project}-<n>` with every required field set (same as `createjiraissue` today). Undo:
  J4 moves it to Canceled; deletion needs a Jira admin.
- **J2 `work.state`** (row 27). `twg jira workitem get <KEY> --fields status,<field ids>`. Effect: state
  and missing fields. Undo: none.
- **J3 `work.fields_repair`** (row 28) **OUTWARD**. Run it on J1's item: the payload is `{}` (nothing
  to repair); then on an item with one empty required field, if one exists. Effect: fields
  written. Undo: none (it only fills empty fields).
- **J4 `work.transition`** (row 29) **OUTWARD**. `twg jira workitem transitions query --id <KEY>`, then
  `twg jira workitem transition --id <KEY> --transition-id <canceled id>`. Effect: status Canceled.
  Undo: none (canceled is the end state of a throwaway).
- **J5 `work.comment`** (row 30) **OUTWARD**. `twg jira workitem comment --id <KEY> --body 'conformance walk'`.
  Undo: delete the comment in Jira.
- **J6 `work.key_format`** (row 31). Read the frontmatter: title `WID-1: x`, branch `WID-1-x`. Undo: none.
- **J7 `work.train_close`** (row 32) **OUTWARD**. Only with a real deploy; a non-factory item gets the
  comment and `left_to_operator`. Undo: delete the comment.

### work/github (IT-003 walk on a throwaway issue)

Run with a scratch workspace whose `factory.toml` sets `[roles.work] provider = "github"` (same
`{repo}`), and record with `--workspace-root` of that workspace. Use C3's `#<t>`; trace with
`--today` naming today's Jira call of the row.

- **G1 `work.create`** (row 26). Returns `#<t>`; creates nothing. Effect: no second record.
- **G2 `work.state`** (row 27). `code.issue_get` → `todo` (or `facts` missing without a facts block).
- **G3 `work.fields_repair`** (row 28) **OUTWARD**. `python3 ~/.compozy/factory/tools/factory_envelope.py facts`
  with the facts of J1, then `gh issue edit <t> --repo {repo} --body-file -` with the body plus the block.
  Effect: the issue body holds `factory.facts/v1`. Undo: none (throwaway).
- **G4 `work.transition`** (row 29) **OUTWARD**. `in_progress`:
  `gh issue edit <t> --repo {repo} --add-label factory:in-progress --remove-label factory:deliver --remove-label factory:blocked`;
  then `canceled`: `gh issue close <t> --repo {repo} --reason "not planned" --comment 'conformance walk'`.
  Effect: labels, then closed not planned. Undo: `gh issue edit <t> --repo {repo} --remove-label factory:in-progress`.
- **G5 `work.comment`** (row 30) **OUTWARD**. As C5. Undo: delete the comment.
- **G6 `work.key_format`** (row 31). Title `#194: x`, branch `194-x`, commit scope `#194`.
- **G7 `work.train_close`** (row 32) **OUTWARD**. Item `{key: "#<t>", facts}`: the issue has no
  `factory` label → comment only, `left_to_operator`. Undo: delete the comment.

### review/github-app-review

- **R1 `review.context`** (row 33). `ext__github_app_review__pr_context` with
  `{"repo": "{repo}", "number": <a merged PR>}`. Effect: head, files, conventions. Undo: none.
- **R2 `review.submit`** (row 34) **OUTWARD**. Only on a throwaway draft PR (C9's): first
  `python3 ~/.compozy/factory/tools/factory_guard.py reviewer <ws>`, then
  `ext__github_app_review__submit_review` with `repo`, `number`, `head_sha`, `summary` and `"findings": []`.
  Effect: an App review on the PR with the derived event. Undo: close the PR (reviews cannot be deleted).
- **R3 `review.gate`** (row 35). `ext__github_app_review__merge_gate` with
  `{"repo": "{repo}", "number": <an open PR>, "kind": "delivery"}`. Effect: `ready` and reasons, same as
  the R0 gate verdict. Undo: none.

### deploy/github-deployments (read only)

- **D1 `deploy.wait`** (row 36). On a merge SHA already deployed to `{environment}`: with
  `compozy__terminal_exec`, `gh api 'repos/{repo}/deployments?sha=<merge-sha>&environment={environment}'`
  and `gh api repos/{repo}/deployments/<id>/statuses`; a bound of 1 minute. Effect: `success`,
  `finished_at` and `link` equal to the Deployment's newest status. Undo: none.

## Finish

```console
$ bin/install                                        # the tree being recorded
$ bin/conformance-record code github --workspace-root <ws-root>
wrote …/conformance/code-github.json
code/github supported
```

Undo every OUTWARD step listed above, delete the `conformance-walk` label, then commit
`conformance/<role>-<provider>.json` and its trace.
