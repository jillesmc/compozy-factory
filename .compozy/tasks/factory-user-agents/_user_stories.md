# User Stories: Factory agents at user level

Companion to `_spec.md`; consumed by `_tests.md`. No UI changes.

## Personas

- **Operator**: runs CompozyOS across several companies' projects and owns the factory.
- **Factory agent**: any `factory-*` always-on agent running in a project.

## Story Index

| ID | Feature Area | Persona | Story |
| --- | --- | --- | --- |
| US-001 | Install | Operator | Install or update the factory agents at user level |
| US-002 | Attach | Operator | Attach the factory to a project |
| US-003 | Run | Factory agent | Read every project value from factory.toml |
| US-004 | Cutover | Operator | Move acme-platform to the user-level agents |
| US-005 | Cutover | Operator | Keep agent memory across the rename |
| US-006 | New Relic | Operator | Set New Relic account/app per workspace |

### US-001: Install or update at user level

**As** operator, **I want** `bin/install` to copy the agents into `~/.compozy`, **so that** every project
uses the same versioned definitions.

- AC-1: Given a clean `COMPOZY_HOME`, when I run `bin/install`, then `agents/factory-*` and
  `factory/tools/` exist and `compozy agent list` shows the six `factory-*` agents with origin global.
- AC-2: Given no repo change, when I run it again, then every line reads `unchanged` and no file mtime
  changes.
- AC-3: After install, the output lists the exact approval commands with hashes for changed source
  scripts and authority blocks.

Edge cases:
- EC-1: a non-factory directory `~/.compozy/agents/general` exists → untouched.
- EC-2: `factory-old` installed earlier but removed from the repo → kept, reported `stale`; removed only
  with `--prune`.
- EC-3: `~/.compozy/agents/factory-scribe` is a symlink → refused with `symlink_not_supported`.

### US-002: Attach a project

**As** operator, **I want** `bin/attach <workspace>`, **so that** a project gets factory instances.

- AC-1: Given a valid `factory.toml`, when I attach, then one active instance per `[team].agents` entry
  exists in that workspace.
- AC-2: Given `factory.toml` missing or invalid, then attach refuses naming the file or the missing key,
  creating nothing.

Edge cases:
- EC-1: instance already exists → reported `exists`, not duplicated.
- EC-2: a source script of an agent is unapproved → attach refuses for that agent with the approve
  command.

### US-003: Read project values from factory.toml

**As** factory agent, **I want** every project value from `factory.toml`, **so that** I work in any
project.

- AC-1: Given `[repo] github = "org/x"`, when the PR watcher validates a PR, then it uses `org/x` and the
  configured `base`/`release` branches.
- AC-2: Given `[ci] runner`, when hygiene checks workflows, then the configured runner rule is enforced.
- AC-3: Given `[language] posts = "pt-BR"`, then issues, comments and Jira text are written in pt-BR.

Edge cases:
- EC-1: a needed key is missing → the agent answers `refused: factory.toml missing <key>` and acts on
  nothing.

### US-004: Cut over acme-platform

**As** operator, **I want** acme-platform moved to the user-level agents, **so that** nothing
project-specific remains in agent definitions.

- AC-1: After cutover, the project has no `.compozy/agents/{sentinela,escrivao,maestro,vigia-pr,porteiro,auditor}`
  and old instances are archived.
- AC-2: Each new instance passes one `compozy agent instance check <agent> --dry-run`.
- AC-3: `factory.toml` holds the new/renamed keys and the dispatcher's source still selects issues
  labeled by `[authorization] authors`.

Edge cases:
- EC-1: a PR watched by `vigia-pr` at cutover time → its pointer is migrated (US-005) and
  `factory-pr-watcher` re-arms its monitor on first wake.

### US-005: Keep memory across the rename

**As** operator, **I want** old memory copied to the new agent, **so that** learned context survives.

- AC-1: Given `vigia-pr` memory in `acme-platform`, when I run `bin/migrate-memory --workspace
  acme-platform --map vigia-pr=factory-pr-watcher`, then `factory-pr-watcher` memory holds the same
  entries plus a first line naming the source.
- AC-2: The old memory remains readable.

Edge cases:
- EC-1: old agent has no memory → `skipped (empty)`.
- EC-2: new agent already has memory → refuses unless `--append`.

### US-006: New Relic defaults per workspace

**As** operator, **I want** account and app set per workspace, **so that** the sentinel's HEARTBEAT has
no project literals.

- AC-1: Given `NEW_RELIC_ACCOUNT_ID=1234567` set for workspace `acme-platform` and a source without
  `with.account_id`, then the poll queries account 1234567.
- AC-2: Given `NEW_RELIC_APP=acme-portal-production` and `where: "appName = '{{app}}'"`, then the NRQL
  uses `appName = 'acme-portal-production'`.
- AC-3: Given `with.account_id` present, it wins over the default.

Edge cases:
- EC-1: neither `with.account_id` nor the default → existing `params_invalid` naming both options.
- EC-2: `{{app}}` used without `NEW_RELIC_APP` → `params_invalid` naming `NEW_RELIC_APP`.
- EC-3: `NEW_RELIC_APP` containing a quote → rejected (`params_invalid`), never interpolated.
