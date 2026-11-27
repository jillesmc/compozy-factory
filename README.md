# compozy-factory

The factory's CompozyOS agents (always-on, plus the on-demand reviewer), defined once and installed at user level. A project joins
by adding `.compozy/factory/factory.toml` and attaching. The file names one provider per role
(`signals`, `code`, `work`, `review`, `deploy`) and carries each provider's values in
`[roles.<role>.<provider>]`; New Relic's app and account live in the extension-owned
`.compozy/newrelic.toml`. Each role has a contract (`contracts/<role>.md`) and each provider an adapter
skill (`skills/factory-<role>-<provider>/`) that performs the contract's operations. Agents speak only
in operations (`code.pr_merge`, `work.transition`, `signals.verify_gone`, …): at the start of a turn each
one resolves its roles' adapter skills with `tools/factory_guard.py adapter`, loads them with
`compozy__skill_view` and uses them only when they come from the user skill root.

| Agent | Role |
| --- | --- |
| `factory-sentinel` | Classifies observability signals (`signals`) and proposes issues |
| `factory-scribe` | Deduplicated issues (`code`) and work items (`work`) |
| `factory-dispatcher` | Accepts `factory:deliver` issues and dispatches executors |
| `factory-pr-watcher` | Follows executor PRs, starts a `factory-reviewer` (after `factory_guard.py reviewer`) per green head and one re-review per contested head, sends `ready:` only when `review.gate` is ready; blocks PRs touching a protected path |
| `factory-gatekeeper` | Merges only on `review.gate` ready and after the `premerge` re-read (no bypass, never a protected PR); the app opens the promotion PR, the operator approves it on GitHub, then deploy and work Done; `continue:` resumes an operator-merged protected PR |
| `factory-auditor` | Post-deploy verdict (`signals.verify_gone` by the finding's verifier provider) |
| `factory-reviewer` | On demand, one session per PR head: reviews the code and posts through the `github-app-review` extension. Never attached |

The reviewer needs the `github-app-review` extension, sandboxed reviewer provider overlays
(`config.toml`, see the extension's `docs/reviewer-sandbox.md`), `[roles.review] provider =
"github-app-review"` and a `REVIEW.md` at the project root. It is never repository-configurable:
`tools/factory_guard.py reviewer` checks the user-level definition at its installed digest.

Provider routing is per machine, not in this repo: `$XDG_CONFIG_HOME/compozy-factory/routing.toml` (default
`~/.config/compozy-factory/routing.toml`, mode 0600, no secrets; each class is `primary = { provider, model[,
reasoning_effort] }` plus `fallback = [{ provider, model }, …]`). The first `bin/install` on a terminal asks, per
class (`work` = sentinel, scribe, dispatcher, pr-watcher, gatekeeper; `auditor`; `reviewer`), a primary
`<provider> <model>`, its reasoning effort (the current one by default) and an ordered fallback among the authenticated providers of `compozy provider list` (the
reviewer: only sandboxed reviewer providers, whose command launches the extension's `reviewer-sandbox`). A class
with one candidate uses it with no fallback. `[agents.<name>]` in the file overrides one agent. Install renders
`provider`, `model` and `fallback_chain` into the installed `AGENT.md`; `bin/install --reconfigure-routing` asks
again, then `bin/reload <ws>` moves live instances to it. The executor route stays in each project's
`factory.toml` `[runtime] executor`.

## Golden path

```console
$ bin/install                       # asks the provider routing once, copies agents/ and tools/ into $COMPOZY_HOME
$ compozy agent heartbeat sources approve ...   # run the approval commands install printed
$ cp factory.example.toml /tmp/acme.toml   # then edit
$ bin/setup <ws> --from /tmp/acme.toml     # on your terminal: preflight, confirm, then policy switch, files, labels, attach
```

`bin/setup` opens a PR (branch `factory/setup`) with `factory.toml`, `.compozy/newrelic.toml` and `REVIEW.md` and
stages the same files in the checkout so the agents can start; review the PR and merge it outside the factory
(protected paths). By hand instead: write both files, then `bin/attach <ws>`.

Update: `git pull && bin/install && bin/reload <ws>`.

### Scope signals (a library or base image)

A project with no New Relic application of its own (a library or a Docker base image used by many
services) is watched through the Log events its consumers emit. The sentinel's optional `nr-scope`
source reads three keys of `.compozy/newrelic.toml`; a project without them gets no `nr-scope` items.
Example, a schema-migration base image whose errors are logged by every service that runs it, in two
accounts (production and staging):

```toml
app = "lib-schema-migration-apply"            # the project: the finding's service, so N consumers dedupe to one
account_id = 1234567
accounts = [1234567, 7654321]                 # 1 to 20; sources and NRQL run once cross-account
logs_where = "global_event_name = 'MIGRATION' AND level = 'ERROR'"
logs_facet = "service_name, step"             # service_name = the consumer where it surfaced, not the owner
logs_traffic_where = "global_event_name = 'MIGRATION'"   # every run: the verify_gone traffic baseline
```

`signals.normalize` turns each item's error text into a signature with
`tools/factory_envelope.py signature` (volatile parts such as quoted SQL, hosts, numbers and SQLSTATE
values replaced), so `apply failed: atlas command failed: signal: killed` in many consumers and both
accounts is one finding; the evidence lists the consumers and accounts. `bin/attach` refuses
malformed keys (`accounts` not 1–20 positive integers; a clause with a newline, `;`, `{{` or `}}`;
only some of the three `logs_*` keys). Needs newrelic ≥ 0.6.0.

Protected paths (`factorylib.PROTECTED_PATHS`): `REVIEW.md`, `.compozy/factory/factory.toml`,
`.compozy/newrelic.toml`, `.compozy/agents/**`, `.compozy/profiles/*/agents/**`, `.compozy/skills/**`,
`.compozy/profiles/*/skills/**`. A PR touching one gets the reviewer's `major` operator finding and is never
merged by the factory: the operator merges it outside the factory, then types
`merged_external: #<pr> <merge-sha>` in the dispatcher's session; `tools/factory_external.py` keeps its phases
(`external_merge_verified` → `continue_dispatched` → `post_merge_done`) in the work-log entry and the
gatekeeper runs the post-merge steps once (`continue:` / `continuing:` / `post_merge_done:`).

Migrating a running project from the pre-roles grammar is one atomic activation (agents paused, extensions,
policy, agents and `factory.toml` switched together, rollback to the recorded prior set):
[docs/activation-runbook.md](docs/activation-runbook.md). Its project steps (one shot, on the operator's terminal):

```console
$ bin/migrate-config <ws>          # asks the promotion approvers, opens the migration PR, stages the policy
$ bin/stage-policy                 # stage a hand-edited policy working copy; prints the secrets set line
```

## Scripts

| Command | Does |
| --- | --- |
| `bin/install [--dry-run] [--prune] [--reconfigure-routing]` | Resolves the provider routing (the file, else asks on a terminal; refused without either, or when a routed provider is no longer authenticated, or a reviewer route is not sandboxed) and renders it into each installed `AGENT.md`. Copies `agents/factory-*` to `$COMPOZY_HOME/agents/`, `tools/` (minus tests) to `$COMPOZY_HOME/factory/tools/`, each adapter to `$COMPOZY_HOME/factory/adapters/<role>-<provider>/` and its `SKILL.md` to `$COMPOZY_HOME/skills/`. Records the adapter tree digests and the `factory-reviewer` digest read back from the daemon in `$COMPOZY_HOME/factory/installed.json` (fails and keeps the old record when the daemon does not report it within 30 s). Prints `installed`/`updated`/`unchanged`/`stale`/`pruned` and the approval commands, with the daemon's hashes, for changed source scripts and authority blocks. Refuses symlinked targets before writing. |
| `bin/attach <ws> [--agents a,b] [--allow-unsupported-adapter] [--dry-run]` | Validates `factory.toml` (role grammar, adapter schemas, cross-role requirements), checks every adapter is installed (custom ones bound) and has a conformance record matching the installed tree, runs the reviewer guard, then creates one instance per `[team] agents` and gives its main session one boundary prompt (`boundary <agent> ok`, workaround for compozy/compozy#1815); `exists` when already attached; `skipped <agent> (on-demand)` for the reviewer; refuses an agent whose source script is unapproved. `--dry-run` runs every check and prints `would attach <agent>` and each agent's effective routing (`routing <agent> <provider>/<model> → <fallback>`, from the installed `AGENT.md`). |
| `bin/setup <ws> [--from <factory.toml>] [--allow-unsupported-adapter]` | Onboards a project, preflight then apply (BR-19). The preflight is read-only and prints every change: missing `factory.toml` (copied from `--from`, never overwritten), `.compozy/newrelic.toml` (app and account asked on stdin), `REVIEW.md` (template), each marked `(protected path)`; extensions (newrelic ≥ 0.6.0, github-app-review ≥ 0.3.0, active); adapters verified like `bin/attach` (conformance records, BR-11) and the reviewer guard; the policy block for the repo (approvers asked) and the active digest change; the PR preconditions (a git checkout on the delivery base with an empty index and an `origin`; none of the files on `origin/<base>`; `factory/setup` absent on origin or identical); missing labels; agents to attach and each agent's effective routing (`routing <agent> ...`, from the installed `AGENT.md`). A policy change needs an interactive terminal: otherwise `refused: policy confirmation needs an interactive terminal` with no write, GitHub call or CompozyOS mutation. After `y`: stage the version, set `GITHUB_APP_POLICY_DIGEST`, verify it active (restart the extension if needed) and smoke `pr_context` on an already-served repo; on any failure or error the digest is set back, the extension enabled and verified (else the manual commands are printed) and nothing else is written. Then the files go out as a PR from `factory/setup` into the delivery base (like `bin/migrate-config`) and are staged in the checkout, then `gh label create`, `bin/attach`, and the manual checklist (`todo`). A re-run on a configured project prints `ok`/`exists` and changes nothing. |
| `bin/install-adapter <role> <path> --workspace <ws>` | Operator only (interactive terminal). Installs the custom adapter `factory.toml` points to at `$COMPOZY_HOME/factory/adapters/<role>-<provider>@<tree digest>/` with the user skill `factory-<role>-<provider>-<digest12>`, and binds it to (workspace root, role, provider) in `bindings.json`. Re-run after every change. |
| `tools/factory_guard.py reviewer\|premerge\|adapter …` | Trusted reviewer check, pre-merge re-read of base and head, adapter skill resolution (installed tree, binding, user skill root). Exit 0 `ok:` / 1 `refused:`. |
| `tools/factory_external.py …` | The dispatcher's only writer of the `merged_external` phases; prints the next action (`cleanup:`, `send: continue:`, `wait:`, `done:`). |
| `bin/reload <ws> [agent…] [--pause]` | Runs the reviewer guard (nothing is archived on refusal), then archives and recreates instances so they load the current definition, with one boundary prompt each; skips on-demand agents. `--pause` pauses each new instance right after creating it, before its boundary prompt (a new active instance is due for a check-in at once; activation runbook R3). |
| `bin/doctor [--fix] [--json]` | On the operator's terminal; read-only without `--fix`. For every workspace with a `factory.toml`, reports each `[team] agents` always-on agent that is not running unattended: `missing` (no instance → `bin/attach`), `paused` (`pause_reason` `unattended`/`fatigue`/…, with the next step), `attention` (main session needs the operator), and `warn` (unattended turns ≥ 80 % of the limit, failure streak, failing source, check-ins disabled, check-in overdue > 30 min, no check-in for 24 h). `--fix` wakes only an `attention` main session left stopped (by a daemon shutdown) with nothing pending, via the boundary prompt (`fixed`). Exit 1 on any finding other than `warn`/`fixed`. Never resumes a pause or recreates. To be alerted, run it from a user timer: [docs/doctor-timer.md](docs/doctor-timer.md). |
| `bin/migrate-config <ws> [--dry-run]` | One-shot conversion of a pre-roles `factory.toml` (lossless or refused) plus `.compozy/newrelic.toml`, committed on `factory/migrate-roles` from `origin/<base>` in a temporary worktree (the checkout is untouched), pushed, PR opened; asks the promotion approvers on a TTY (refuses otherwise, before any write), adds the repo's block to the github-app-review policy working copy and stages it through `bin/stage-policy`. Never reads a secret; activates nothing. `unchanged` on the role grammar. |
| `bin/stage-policy` | Checks the github-app-review policy working copy against the extension's strict TOML subset and schema (a port of its parser) and writes the byte copy `policies/<sha256>.toml` (0444, never overwritten); prints the `secrets set` line. No extension or network call. |
| `bin/runbook-r0 <ws> [--factory <checkout>] [--pr <n>] [--agent <name>] [--out <file>] [--source <ext>=github:<owner>/<repo>@<tag>] [--compare <r0.json>]` | Read-only R0 record of [docs/activation-runbook.md](docs/activation-runbook.md): extension versions, factory commit, installed agent digests, `factory.toml` bytes at `origin/<base>` (copy saved next to the record), `.compozy/newrelic.toml` presence, instances, bound env names (never values), the smoke PRs, the open PR's gate verdict, and each extension's pinned rollback command (published install: the `github:<slug>@v<version>` tag pinned to the archive digest, checked against the release's `.tar.gz.sha256`; local path: `--replace` pinned to the package checksum). Writes only the record, outside the workspace and the factory checkout. Refuses an incomplete capture (no record). `--agent` invokes the gate as that agent. `--compare` reports `same`/`differs` per part of the R0 set (extension version, source, checksum and archive digest; factory commit; agent digest and tree; both `factory.toml` hashes; `newrelic.toml` absent), exit 1 on a difference. |
| `bin/conformance-trace <role> <provider> <operation> --row <n> --outcome pass\|fail --effect '<text>' [--input k=v]… [--call '<tool or argv>' '<target>']…` | Appends one redacted operation call (matrix row, provider calls, observed effect) to `conformance/<role>-<provider>.trace.jsonl`; refuses an operation outside `contracts/<role>.md` or a row of another operation. See [docs/conformance-walk.md](docs/conformance-walk.md). |
| `bin/conformance-record <role> <provider> --workspace-root <path>` | Writes `conformance/<role>-<provider>.json` from the trace (every contract operation's latest line `pass`, else refused and nothing written), with the installed tree digest, `provider_site`, required extension versions and factory commit; then verifies it like `bin/attach` and prints `supported` or `unsupported (<reason>)`. |
| `bin/migrate-memory --workspace <ws> --map old=new[,…] [--append]` | Copies old agent memory to the new name with a `Migrated from …` first line; never deletes the old memory. |

Python 3.11+ stdlib only. Agents call shared tools at `~/.compozy/factory/tools/<tool>`.

Adapter format (`skills/factory-<role>-<provider>/`): `SKILL.md` with frontmatter `role`, `provider`,
`schema`, `requires_extensions`, `requires_roles` (and `key_format` for `work`) and one `## <operation>`
section per operation of `contracts/<role>.md`. An adapter that serves heartbeat polls ships
`sources/<name>.py`; each one is self-contained (stdlib only, no sibling-module import) because the
provider-neutral wrapper runs it as `python3 -I -B`. Agents run any adapter script the same way
(`python3 -I -B <installed adapter>/<script>`). The tree digest covers every file except the
conformance record and its trace; compiled Python in a tree is refused.

## Tests

```console
$ python3 -m pytest          # unit tests; CompozyOS is faked at the subprocess boundary
$ tests/it/run.sh            # integration tests against a throwaway daemon (own COMPOZY_HOME, port, socket)
$ tests/it/activation.sh     # IT-006 only: the activation runbook from the released extension builds (needs gh, bun)
```

`tests/it/last-run.log` holds the latest IT evidence. Specs: `.compozy/tasks/factory-user-agents/`,
`.compozy/tasks/factory-roles/` (role grammar, contracts, adapters).
