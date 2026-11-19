# compozy-factory

The factory's CompozyOS agents (always-on, plus the on-demand reviewer), defined once and installed at user level. A project joins
by adding `.compozy/factory/factory.toml` and attaching. The file names one provider per role
(`signals`, `code`, `work`, `review`, `deploy`) and carries each provider's values in
`[roles.<role>.<provider>]`; New Relic's app and account live in the extension-owned
`.compozy/newrelic.toml`. Each role has a contract (`contracts/<role>.md`) and each provider an adapter
skill (`skills/factory-<role>-<provider>/`) that performs the contract's operations.

| Agent | Role |
| --- | --- |
| `factory-sentinel` | Classifies New Relic signals and proposes issues |
| `factory-scribe` | Deduplicated GitHub issues and Jira work items |
| `factory-dispatcher` | Accepts `factory:deliver` issues and dispatches executors |
| `factory-pr-watcher` | Follows executor PRs, starts a `factory-reviewer` per green head and one re-review per contested head, sends `ready:` only when `merge_gate` is ready |
| `factory-gatekeeper` | Merges only on `merge_gate` ready (no bypass); the app opens the promotion PR, the operator approves it on GitHub, then deploy and Jira Done |
| `factory-auditor` | Post-deploy verdict in New Relic |
| `factory-reviewer` | On demand, one session per PR head: reviews the code and posts through the `github-app-review` extension. Never attached |

The reviewer needs the `github-app-review` extension, the `claude-reviewer` provider overlays
(`config.toml`, see the extension's `docs/reviewer-sandbox.md`), `[roles.review] provider =
"github-app-review"` and a `REVIEW.md` at the project root. It is never repository-configurable:
`tools/factory_guard.py reviewer` checks the user-level definition at its installed digest.

## Golden path

```console
$ bin/install                       # copies agents/ and tools/ into $COMPOZY_HOME (default ~/.compozy)
$ compozy agent heartbeat sources approve ...   # run the approval commands install printed
$ cp factory.example.toml <project>/.compozy/factory/factory.toml   # then edit
$ printf 'app = "acme-api-prod"\naccount_id = 1234567\n' > <project>/.compozy/newrelic.toml
$ bin/attach <ws>                   # --allow-unsupported-adapter until the adapters have conformance records
```

Update: `git pull && bin/install && bin/reload <ws>`.

## Scripts

| Command | Does |
| --- | --- |
| `bin/install [--dry-run] [--prune]` | Copies `agents/factory-*` to `$COMPOZY_HOME/agents/`, `tools/` (minus tests) to `$COMPOZY_HOME/factory/tools/`, each adapter to `$COMPOZY_HOME/factory/adapters/<role>-<provider>/` and its `SKILL.md` to `$COMPOZY_HOME/skills/`. Records the adapter tree digests and the `factory-reviewer` digest read back from the daemon in `$COMPOZY_HOME/factory/installed.json` (fails and keeps the old record when the daemon does not report it within 30 s). Prints `installed`/`updated`/`unchanged`/`stale`/`pruned` and the approval commands, with the daemon's hashes, for changed source scripts and authority blocks. Refuses symlinked targets before writing. |
| `bin/attach <ws> [--agents a,b] [--allow-unsupported-adapter]` | Validates `factory.toml` (role grammar, adapter schemas, cross-role requirements), checks every adapter is installed (custom ones bound) and has a conformance record matching the installed tree, runs the reviewer guard, then creates one instance per `[team] agents`; `exists` when already attached; `skipped <agent> (on-demand)` for the reviewer; refuses an agent whose source script is unapproved. |
| `bin/install-adapter <role> <path> --workspace <ws>` | Operator only (interactive terminal). Installs the custom adapter `factory.toml` points to at `$COMPOZY_HOME/factory/adapters/<role>-<provider>@<tree digest>/` with the user skill `factory-<role>-<provider>-<digest12>`, and binds it to (workspace root, role, provider) in `bindings.json`. Re-run after every change. |
| `tools/factory_guard.py reviewer\|premerge\|adapter …` | Trusted reviewer check, pre-merge re-read of base and head, adapter skill resolution (installed tree, binding, user skill root). Exit 0 `ok:` / 1 `refused:`. |
| `bin/reload <ws> [agent…]` | Archives and recreates instances so they load the current definition; skips on-demand agents. |
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
```

`tests/it/last-run.log` holds the latest IT evidence. Specs: `.compozy/tasks/factory-user-agents/`,
`.compozy/tasks/factory-roles/` (role grammar, contracts, adapters).
