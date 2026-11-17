# compozy-factory

The factory's always-on CompozyOS agents, defined once and installed at user level. A project joins
by adding `.compozy/factory/factory.toml` and attaching; every project value (repo, branches, Jira,
CI runner rule, New Relic app, executor runtime, authors, post language) comes from that file.

| Agent | Role |
| --- | --- |
| `factory-sentinel` | Classifies New Relic signals and proposes issues |
| `factory-scribe` | Deduplicated GitHub issues and Jira work items |
| `factory-dispatcher` | Accepts `factory:deliver` issues and dispatches executors |
| `factory-pr-watcher` | Follows executor PRs until ready |
| `factory-gatekeeper` | Merges, promotion PR, deploy, Jira Done |
| `factory-auditor` | Post-deploy verdict in New Relic |

## Golden path

```console
$ bin/install                       # copies agents/ and tools/ into $COMPOZY_HOME (default ~/.compozy)
$ compozy agent heartbeat sources approve ...   # run the approval commands install printed
$ cp factory.example.toml <project>/.compozy/factory/factory.toml   # then edit
$ printf '1234567' | compozy extension secrets set newrelic --workspace <ws> --value-stdin --env NEW_RELIC_ACCOUNT_ID
$ printf 'acme-api-prod' | compozy extension secrets set newrelic --workspace <ws> --value-stdin --env NEW_RELIC_APP
$ bin/attach <ws>
```

Update: `git pull && bin/install && bin/reload <ws>`.

## Scripts

| Command | Does |
| --- | --- |
| `bin/install [--dry-run] [--prune]` | Copies `agents/factory-*` to `$COMPOZY_HOME/agents/` and `tools/` (minus tests) to `$COMPOZY_HOME/factory/tools/`. Prints `installed`/`updated`/`unchanged`/`stale`/`pruned` per agent and the approval commands, with the daemon's hashes, for changed source scripts and authority blocks. Refuses symlinked targets before writing. |
| `bin/attach <ws> [--agents a,b]` | Validates `factory.toml`, then creates one instance per `[team] agents`; `exists` when already attached; refuses an agent whose source script is unapproved. |
| `bin/reload <ws> [agent…]` | Archives and recreates instances so they load the current definition. |
| `bin/migrate-memory --workspace <ws> --map old=new[,…] [--append]` | Copies old agent memory to the new name with a `Migrated from …` first line; never deletes the old memory. |

Python 3.11+ stdlib only. Agents call shared tools at `~/.compozy/factory/tools/<tool>`.

## Tests

```console
$ python3 -m pytest          # unit tests; CompozyOS is faked at the subprocess boundary
$ tests/it/run.sh            # IT-001..IT-003 against a throwaway daemon (own COMPOZY_HOME, port, socket)
```

`tests/it/last-run.log` holds the latest IT evidence. Spec: `.compozy/tasks/factory-user-agents/`.
