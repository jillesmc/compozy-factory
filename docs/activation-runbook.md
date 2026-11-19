# Activation runbook: pre-roles project → role grammar (R0–R5)

One atomic switch of a running project from the pre-roles factory (github-app-review 0.2.x with
`GITHUB_APP_*` repo env, newrelic 0.3.x with `NEW_RELIC_APP`, old `factory.toml`) to the role grammar
(github-app-review 0.3.0 with an approved policy digest, newrelic 0.4.0 with `.compozy/newrelic.toml`,
agents on role operations). No component accepts both grammars, so the extensions, the policy, the agents
and `factory.toml` move together while the agents are paused, and any failure rolls back to the recorded
prior set. Rehearsed end to end by `tests/it/activation.sh` (IT-006) on an isolated daemon.

## Placeholders

| Placeholder | Meaning |
| --- | --- |
| `<ws>` / `<ws-root>` | CompozyOS workspace name / its checkout path |
| `<owner/repo>`, `<base>`, `<release>` | `[repo] github`, `base`, `release` of the old `factory.toml` |
| `<factory>` | the compozy-factory checkout `bin/install` runs from |
| `<r0-sha>` / `<new-sha>` | compozy-factory commit installed today / the commit with the role grammar |
| `<new>` | a second worktree of compozy-factory at `<new-sha>` (scripts R0–R2 and every verification run from it) |
| `<r0.json>` | the R0 record written by `bin/runbook-r0` |
| `<sha>` | the staged policy digest (sha256 of the policy version file) |
| `<pr>` / `<merge-sha>` | the migration PR and its merge commit on `<base>` |
| `<open-pr>` / `<merged-pr>` | the open delivery PR and the most recent merged one, from `<r0.json>` |

The six instances: `factory-sentinel factory-scribe factory-dispatcher factory-pr-watcher
factory-gatekeeper factory-auditor` (written `$SIX` below; `SIX="factory-sentinel factory-scribe
factory-dispatcher factory-pr-watcher factory-gatekeeper factory-auditor"`). `factory-reviewer` is
on-demand and has no instance.

## Who runs what

**Operator personally** (`[operator]`): every `compozy extension secrets set`/`unset`; the promotion
approvers prompt of `bin/migrate-config` (needs a TTY); merging the migration PR (and a revert PR) on
GitHub; moving the `<ws-root>` checkout; `compozy extension install` (trust prompt); the
`compozy agent heartbeat … approve` lines `bin/install` prints (operator-only commands). Everything
else is read-only or scripted and may be run by an agent session on the operator's request.

## What is paused

From R1 to R5 the six instances are paused: no check-in, no dispatch, no review, no merge, no promotion,
no deploy follow, no audit. R3 recreates them with `bin/reload <ws> --pause`, which pauses each new
instance right after `agent instance create`, before its boundary turn and before the next agent (a new
active instance is due for a check-in at once). The only agent turns in the window are those boundary turns
(one fixed "reply ok" prompt per new main session, on its real provider); R4's `check --dry-run` polls
sources without a turn. GitHub keeps running (CI, human merges, deploys); New Relic signals accumulate
and the sentinel's next check sees them. On-demand reviewer sessions and executor sessions are not
instances: start only when no delivery is in flight (no open entry in the dispatcher's work log, no running
executor session in `compozy session list --workspace <ws>`). Do not push to `<open-pr>` during the window:
R4 compares its gate verdict with R0's.

Schedule (America/Sao_Paulo, from the agents' `HEARTBEAT.md` active hours: scribe, dispatcher,
pr-watcher and gatekeeper 08:00–20:00, sentinel 10:00–12:00, auditor 12:00–14:00):

- R3 step 5 (`bin/reload --pause`) **outside every window**, before 08:00 or after 20:00, as a second
  guard: even an instance that slipped past the pause would find its heartbeat gate closed.
- R4 between 10:00 and 12:00: the newrelic smoke is the sentinel's `check --dry-run` on the paused
  instance, which polls its sources only inside the sentinel's active hours (outside it answers the gate
  `quiet_window` and checks nothing).

For example R0–R3 at 07:00, R4 at 10:00, R5 right after R4.

## Preparation (no effect)

```console
$ git -C <factory> fetch origin && git -C <factory> worktree add --detach <new> <new-sha>
$ git -C <factory> rev-parse HEAD
<r0-sha>
```

`<factory>` stays at `<r0-sha>` until R3; `<new>` only provides the scripts. Remove `<new>` after R5
(`git -C <factory> worktree remove <new>`).

## R0 — record the prior complete set (read-only)

```console
$ <new>/bin/runbook-r0 <ws> --factory <factory> --out ~/factory-activation/r0-<ws>.json
recorded   ~/factory-activation/r0-<ws>.json
workspace  <ws-root> HEAD <head> (<owner/repo>, base <base>)
factory    <r0-sha>
extension  github-app-review 0.2.2 (active; from <source>) env GITHUB_APP_BASE, GITHUB_APP_ID, GITHUB_APP_INSTALLATION_ID, GITHUB_APP_PRIVATE_KEY, GITHUB_APP_PROMOTION, GITHUB_APP_PROMOTION_APPROVERS, GITHUB_APP_REPOS
rollback   compozy extension install github:jillesmc/compozy-github-app@v0.2.2 --expected-digest <archive sha256> --allow-unverified
extension  newrelic 0.3.1 (active; from <source>) env NEW_RELIC_ACCOUNT_ID, NEW_RELIC_API_KEY, NEW_RELIC_APP
rollback   compozy extension install github:jillesmc/compozy-newrelic@v0.3.1 --expected-digest <archive sha256> --allow-unverified
agent      factory-auditor digest <12 hex> tree <12 hex>
…          (seven factory-* agents)
toml       at origin/<base> <12 hex> (copy ~/factory-activation/r0-<ws>.json.factory.toml); checkout <12 hex>
newrelic   .compozy/newrelic.toml absent
instances  factory-sentinel=active, …
smoke      last merged delivery PR #<merged-pr>
smoke      gate #<open-pr> {"ready": …, "head_sha": "…", "reasons": […]}
```

It reads extension versions and provenance, the agents' daemon digests and file trees, `factory.toml`
at `origin/<base>` (no fetch: run `git -C <ws-root> fetch origin` first so the ref is current), the
bound env names (never values), the open and last merged delivery PRs (`gh pr list`) and the open PR's
`merge_gate` verdict. Pass `--pr <n>` to pick the open PR. Check before going on:

- `toml at origin/<base>` equals `checkout`: the agents read `factory.toml` from `<ws-root>`; if the
  checkout is on another branch or differs, move it to `<base>` first (operator) and record again.
- `factory` is not `(dirty checkout)`.
- Each extension has a `rollback` line. For a published install (provenance `installed_from: github`,
  as both live extensions are) it reinstalls the tag `github:<slug>@v<version>` pinned to the provenance
  `archive_digest_sha256`, which `bin/runbook-r0` checks equals the release's published
  `.tar.gz.sha256` asset (a read-only `gh release download`); for a published install `--expected-digest`
  is compared with the downloaded archive, not with `checksum_sha256` (the extracted package). A local-path
  install reinstalls from its path with `--replace`, pinned to `checksum_sha256`. When the provenance has no
  slug, or the derived tag publishes another archive, the capture is refused naming the extension: pass
  `--source <extension>=github:<owner>/<repo>@<tag>` with the release the build came from.
- The smoke gate line shows a verdict, not an error. `bin/runbook-r0` refuses an incomplete capture
  (`refused: incomplete R0 capture, nothing recorded: …`, exit 1, no file): a missing extension, version,
  source or checksum, an agent digest or tree, the factory commit, `factory.toml` at `origin/<base>`, or a
  failed smoke read. A `tool_denied` gate read: rerun with `--agent factory-gatekeeper`.
- Keep `<r0.json>` and its `.factory.toml` copy until R5 or the rollback ends.

Rollback: nothing to undo.

## R1 — pause the six instances

```console
$ for a in $SIX; do compozy agent instance pause $a --workspace <ws>; done
Factory Sentinel paused in <ws>
…                                                   (six lines)
$ compozy agent instance list --workspace <ws> -o json | jq -r '.instances[] | select(.state != "archived") | "\(.agent_name) \(.state)"'
factory-sentinel paused
…                                                   (six, all paused)
$ <new>/bin/runbook-r0 <ws> --factory <factory> --compare <r0.json>
same     extensions
same     factory commit
same     agents
same     factory.toml at base
same     factory.toml in checkout
same     newrelic.toml absent
```

Rollback: `for a in $SIX; do compozy agent instance resume $a --workspace <ws>; done`
(`Factory Sentinel resumed in <ws>` …).

## R2 — stage (nothing active changes)

1. `[operator]` Convert, open the migration PR and stage the policy (TTY: it asks the approvers that
   the old write-only `GITHUB_APP_PROMOTION_APPROVERS` held):

   ```console
   $ <new>/bin/migrate-config <ws>
   converted  .compozy/factory/factory.toml (6 sections → [roles.*])
   newrelic   .compozy/newrelic.toml (app, account_id from the old [newrelic])
   approvers  promotion approvers for <owner/repo> (comma-separated GitHub logins): <login>[, <login>]
   branch     factory/migrate-roles from origin/<base>, pushed
   pr         #<pr> opened into <base> (the reviewer flags the factory.toml change for you)
   policy     working copy ~/.config/compozy-github-app/policy.toml updated with:
              [repos."<owner/repo>"]
              delivery_base = "<base>"
              promotion = { head = "<base>", base = "<release>" }
              approvers = ["<login>"]
   policy     staged     policies/<sha>.toml (byte copy of the working copy; schema checked)
   policy     activate   printf <sha> | compozy extension secrets set github-app-review --env GITHUB_APP_POLICY_DIGEST --value-stdin
   policy     not active: the activation runbook switches GITHUB_APP_POLICY_DIGEST (R3)
   ```

   The workspace checkout is not touched (a temporary worktree carries the commit). Do **not** run the
   printed `activate` line yet, and do not call `compozy extension exec github-app-review --cmd
   policy-stage`: 0.2.2's command catalog is `doctor` only, and the extension commands are used from R3 on.
   Review the PR diff: `factory.toml` (role grammar) and `.compozy/newrelic.toml` (`app`, `account_id`).
2. `[operator]` Merge `#<pr>` into `<base>` on GitHub with a merge commit, outside the factory (the
   agents are paused; both files are protected paths, so the App never approves it: another approver or
   an explicit bypass, as for any convention change). Note `<merge-sha>` and the PR head `<pr-head-sha>`.
3. `[operator]` Move the workspace checkout to the merge and verify the bytes:

   ```console
   $ git -C <ws-root> switch <base> && git -C <ws-root> pull --ff-only
   $ git -C <ws-root> rev-parse HEAD
   <merge-sha>
   $ for f in .compozy/factory/factory.toml .compozy/newrelic.toml; do git -C <ws-root> show <pr-head-sha>:$f | cmp - <ws-root>/$f && echo "same $f"; done
   same .compozy/factory/factory.toml
   same .compozy/newrelic.toml
   ```

4. Confirm the staged version (factory-side, no extension call):

   ```console
   $ <new>/bin/stage-policy
   unchanged  policies/<sha>.toml
   activate   printf <sha> | compozy extension secrets set github-app-review --env GITHUB_APP_POLICY_DIGEST --value-stdin
   ```

   After a hand edit of the working copy it prints `staged` with a new `<sha>`; use that one.
5. Probe: the six still paused; the old extensions ignore both new files;
   `<new>/bin/runbook-r0 <ws> --factory <factory> --compare <r0.json>` prints `differs` for exactly
   `factory.toml at base`, `factory.toml in checkout` and `newrelic.toml absent`.

Rollback (before step 2): close `#<pr>` and delete `factory/migrate-roles` on GitHub (operator); resume.
After step 2: revert as in [Rollback](#rollback-from-r2-r3-or-r4) steps 3–4, then resume. The staged
`policies/<sha>.toml` and the working-copy block stay; nothing reads them while the digest is unset.

## R3 — activate together

1. `[operator]` Install the new extension versions (a published source replaces the installed one):

   Trust, per `compozy extension install --help`: `--allow-unverified` "Allow install when the extension
   checksum is not registry-verified" and `--yes` "Skip confirmation when using --allow-unverified". These
   GitHub releases are not in the curated registry, so expect the install to need `--allow-unverified`
   and to ask for confirmation: run it without `--yes` and confirm yourself after reading what it lists
   (the tools it adds). `--replace` is for local paths only; a published source replaces implicitly.

   ```console
   $ compozy extension install github:jillesmc/compozy-newrelic@v0.4.0 --allow-unverified
   $ compozy extension install github:jillesmc/compozy-github-app@v0.3.0 --allow-unverified
   github-app-review will:
     • add tool policy to every profile
     • add tool policy_stage to every profile
     …
   ✓ install github-app-review
   ```

   `compozy extension status github-app-review` shows `Version: 0.3.0`, `State: active`;
   `compozy extension secrets list github-app-review` shows the four removed keys as `Stale Bindings`
   until R5.
2. Check the working copy is the staged version and nothing is active yet (the text output redacts the
   digest; use JSON):

   ```console
   $ compozy extension exec github-app-review --cmd policy --workspace <ws> -o json | jq -c '.result.structured | {active: .active.state, working: .working.digest}'
   {"active":"unset","working":"<sha>"}
   ```

3. `[operator]` Set the approved digest, restart the extension (the running process does not see a new
   secret), verify:

   ```console
   $ printf <sha> | compozy extension secrets set github-app-review --env GITHUB_APP_POLICY_DIGEST --value-stdin
   $ compozy extension disable github-app-review && compozy extension enable github-app-review
   Disabled in profile default.
   Enabled in profile default.
   $ compozy extension exec github-app-review --cmd policy --workspace <ws> -o json | jq -c '.result.structured.active | {state, digest}'
   {"state":"approved","digest":"<sha>"}
   ```

   `disable`/`enable` of newrelic is not needed (its install restarted it and it has no new secret).
4. Move the factory checkout and install the agents, tools and adapters:

   ```console
   $ git -C <factory> checkout --detach <new-sha>
   $ <factory>/bin/install
   updated factory-sentinel
   …                                                   (seven agents)
   updated tools (factory_envelope.py, factory_external.py, factory_guard.py, factory_intake.py)
   installed adapter code-github tree <12 hex>
   installed adapter deploy-github-deployments tree <12 hex>
   installed adapter review-github-app-review tree <12 hex>
   installed adapter signals-newrelic tree <12 hex>
   installed adapter work-github tree <12 hex>
   installed adapter work-jira tree <12 hex>
   reviewer factory-reviewer digest <12 hex> (origin global, layer user) recorded
   approve before attaching:
     compozy agent heartbeat sources approve factory-dispatcher deliver --sha256 sha256:<64 hex> --workspace @user --yes
     compozy agent heartbeat sources approve factory-auditor due --sha256 sha256:<64 hex> --workspace @user --yes
     compozy agent heartbeat authority approve factory-auditor --sha256 sha256:<64 hex> --workspace @user --yes
   ```

   `[operator]` Run each printed `approve` line exactly as printed, including the
   `heartbeat authority approve factory-auditor` line (the auditor's HEARTBEAT.md changed).
5. Outside every active window (see Schedule): recreate the instances paused (reviewer guard first;
   each new instance is paused before its boundary turn and before the next agent):

   ```console
   $ <factory>/bin/reload <ws> --pause
   reviewer factory-reviewer origin global layer user file <12 hex> digest <12 hex> (installed)
   == factory-sentinel
      paused factory-sentinel
      main: sess-… provider: claude-mm4
   boundary factory-sentinel ok
   …                                                   (six agents)
   $ for a in $SIX; do echo "$a $(compozy agent instance check-ins $a --workspace <ws> -o json | jq '.check_ins | length')"; done
   factory-sentinel 0
   …                                                   (six, all 0; check again at the end of R4)
   ```

   Without `--pause`, `bin/reload` leaves each new instance active and due for a check-in at once. **On any
   `bin/reload --pause` error, run the R1 pause loop before anything else**
   (`for a in $SIX; do compozy agent instance pause $a --workspace <ws>; done`): a failed `pause` call
   leaves that new instance active.
6. Probe: `<new>/bin/runbook-r0 <ws> --factory <factory> --compare <r0.json>` prints `differs` for every
   line (new extensions, commit, agents, both files).

Rollback: [Rollback](#rollback-from-r2-r3-or-r4).

## R4 — smoke (each compared with R0)

```console
$ compozy extension exec github-app-review --cmd policy --workspace <ws> -o json | jq -c '.result.structured.active | {state, digest}'
{"state":"approved","digest":"<sha>"}
$ compozy tool invoke ext__github_app_review__pr_context --workspace <ws> -o json \
    --input '{"repo":"<owner/repo>","number":<merged-pr>}' | jq -c '{status, state: .result.structured.state}'
{"status":"completed","state":"merged"}
$ compozy tool invoke ext__github_app_review__merge_gate --workspace <ws> -o json \
    --input '{"repo":"<owner/repo>","number":<open-pr>,"kind":"delivery"}' | jq -cS '.result.structured'
$ jq -cS '.smoke.gate.verdict' <r0.json>             # must print the same line
$ compozy agent instance check factory-sentinel --workspace <ws> --dry-run -o json | jq -c '{gate, sources: [.sources[]? | {id, result, code: .error.code}]}'
{"gate":"…","sources":[{"id":"nr-errors","result":"…","code":null},{"id":"nr-logs",…},{"id":"nr-incidents",…}]}
$ python3 ~/.compozy/factory/tools/factory_guard.py reviewer <ws>
ok: factory-reviewer origin global layer user file <12 hex> digest <12 hex> (installed)
$ <factory>/bin/attach <ws> --dry-run --allow-unsupported-adapter
warning: adapter signals/newrelic is not supported (conformance record missing); attaching anyway
…                                                   (one per configured adapter)
reviewer factory-reviewer origin global layer user file <12 hex> digest <12 hex> (installed)
exists factory-sentinel
…                                                   (six `exists`, exit 0)
```

- `pr_context` runs on a merged PR on purpose: `merge_gate` on a closed PR always answers `pr_not_open`.
- Sentinel dry-run passes when `sources` lists the three sources and none has `"result":"failing"` (so
  no `"code":"source_params_invalid"`, which is `with.where uses {{app}}; set app in .compozy/newrelic.toml
  or with.app`: `{{app}}` did not resolve). `"gate":"quiet_window"` with no `sources` means outside
  10:00–12:00: rerun inside the window. The instance stays paused; a dry-run starts no turn.
- After the smokes, the six `check-ins` counts of R3 step 5 are still 0.
- `--allow-unsupported-adapter` is needed by `bin/attach` (and later by `bin/setup`) until the
  conformance walks write `conformance/<role>-<provider>.json` for each adapter; without it attach refuses
  `adapter <role>/<provider> is not supported (conformance record missing …); pass
  --allow-unsupported-adapter`. `bin/reload` does not check conformance.
- If a tool call answers `tool_denied`, the daemon's `tools.policy` does not trust the extension for
  direct calls; rerun the call with `--agent factory-reviewer` (pr_context) or `--agent
  factory-gatekeeper` (merge_gate), whose allowlists grant them.

Any mismatch: [Rollback](#rollback-from-r2-r3-or-r4).

## R5 — success: drop the removed keys, resume

```console
$ for k in GITHUB_APP_REPOS GITHUB_APP_BASE GITHUB_APP_PROMOTION GITHUB_APP_PROMOTION_APPROVERS; do compozy extension secrets unset github-app-review --env $k; done
✓ unbound GITHUB_APP_REPOS
…                                                   (four lines)
$ compozy extension secrets unset newrelic --env NEW_RELIC_APP
✓ unbound NEW_RELIC_APP
$ compozy extension secrets list github-app-review -o json | jq -c '.bound_env_keys'
["GITHUB_APP_ID","GITHUB_APP_INSTALLATION_ID","GITHUB_APP_POLICY_DIGEST","GITHUB_APP_PRIVATE_KEY"]
$ compozy extension secrets list newrelic -o json | jq -c '.bound_env_keys'
["NEW_RELIC_ACCOUNT_ID","NEW_RELIC_API_KEY"]
$ for a in $SIX; do compozy agent instance resume $a --workspace <ws>; done
Factory Sentinel resumed in <ws>
…                                                   (six lines)
```

`[operator]` both `unset` commands. Removed keys are unset only here, so every earlier rollback needs no
secret re-entry. After R5 a rollback needs the operator to set the five values again (`secrets set`), so
watch the first delivery (E2E-001) before calling the activation done. Remove `<new>`.

## Rollback (from R2, R3 or R4)

Order matters: everything below is verified **before** `bin/reload`.

1. `[operator]` Reinstall the prior extensions with the pinned commands recorded in R0, one by one, without
   `--yes` (confirm the trust prompt yourself, as in R3):

   ```console
   $ jq -r '.extensions[].rollback.command' <r0.json>
   compozy extension install github:jillesmc/compozy-github-app@v0.2.2 --expected-digest <archive sha256> --allow-unverified
   compozy extension install github:jillesmc/compozy-newrelic@v0.3.1 --expected-digest <archive sha256> --allow-unverified
   $ compozy extension install github:jillesmc/compozy-github-app@v0.2.2 --expected-digest <archive sha256> --allow-unverified
   …
   ✓ install github-app-review
   $ compozy extension install github:jillesmc/compozy-newrelic@v0.3.1 --expected-digest <archive sha256> --allow-unverified
   …
   ✓ install newrelic
   ```

   A published source replaces the installed version implicitly and is checked against the archive
   digest; a recorded local-path command carries `--replace` and the package checksum instead. A refusal
   `source changed since it was listed` means the tag no longer serves the R0 archive: rerun the same
   command without `--expected-digest` only if you accept that, and let step 4's `--compare` line
   `extensions` (version, source, package checksum, archive digest) decide. The install restarts the
   extension (no `disable`/`enable` needed). The removed keys are still bound (never unset
   before R5), so 0.2.2 and 0.3.1 work as before. `GITHUB_APP_POLICY_DIGEST` may stay bound: 0.2.2
   neither declares nor reads it, and a retry reuses it.
2. Reinstall the prior factory commit (it rewrites the agents and removes the new tools):

   ```console
   $ git -C <factory> checkout --detach <r0-sha>
   $ <factory>/bin/install
   updated factory-sentinel
   …
   updated tools (factory_intake.py)
   approve before attaching:
     compozy agent heartbeat …                         (run each line, operator)
   ```

   The adapters and `factory-<role>-<provider>` user skills the new install added stay on disk, unused by
   the old agents (`~/.compozy/factory/adapters/`, `~/.compozy/skills/factory-*-*`,
   `~/.compozy/factory/installed.json`); remove them only after deciding not to retry.
3. `[operator]` Revert the migration PR outside the factory: on GitHub, "Revert" on `#<pr>`, merge the
   revert PR into `<base>` with a merge commit. Note `<revert-merge-sha>`.
4. `[operator]` Move the workspace checkout and verify, before anything reloads:

   ```console
   $ git -C <ws-root> pull --ff-only && git -C <ws-root> rev-parse HEAD
   <revert-merge-sha>
   $ cmp <r0.json>.factory.toml <ws-root>/.compozy/factory/factory.toml && echo "factory.toml = R0"
   factory.toml = R0
   $ test ! -e <ws-root>/.compozy/newrelic.toml && echo "newrelic.toml absent"
   newrelic.toml absent
   $ <new>/bin/runbook-r0 <ws> --factory <factory> --compare <r0.json>
   same     extensions
   same     factory commit
   same     agents
   same     factory.toml at base
   same     factory.toml in checkout
   same     newrelic.toml absent
   ```

   The daemon reloads definitions asynchronously: if only `agents` differs, rerun after a few
   seconds. Any other `differs` → stop and fix that part first.
5. `<factory>/bin/reload <ws>` (the R0 version: recreates the six instances, active), then confirm with
   `compozy agent instance list --workspace <ws>` and one gate call on `<open-pr>` equal to its R0 verdict.
   The approved `policies/<sha>.toml` stays on disk for the next attempt.

R1-only rollback: resume the six. R2 before the merge: close the PR, resume.
