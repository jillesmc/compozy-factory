# Factory agents at user level (`factory-user-agents`)

Status: draft for operator approval. Decisions from the 2026-11-17 session: Q19 = a (prefix `factory-`),
Q20 = b (separate repo + install script), Q21 = a (project values in `factory.toml`, per-workspace New
Relic defaults), Q22 = a (migrate memory), Q23 = a (English definitions; post language configurable).

---

# Part I — Product

## Overview

- **Motivating Problem**: the factory's always-on agents (`sentinela`, `escrivao`, `maestro`, `vigia-pr`,
  `porteiro`, `auditor`) live in `acme-platform/.compozy/agents` with Portuguese names and prompts that
  hard-code `acme-reports`, `ACME-`, New Relic account `1234567`, `acme-portal-production`,
  branch names and the CodeBuild runner rule. They cannot serve another project, and the operator works
  across several companies. The simplest end-to-end behavior that solves it: **the same English-named
  factory agents, installed once at user level from their own repository, run in `acme-platform` with
  all project values read from that project's `.compozy/factory/factory.toml`, behaving as they do today.**
- **Who it is for**: the operator, who wants to attach the factory to any project.
- **Why it is valuable**: one versioned definition per agent; a project opts in with a config file and
  instance creation; nothing company-specific in the agents.

## Goals

- Agents are defined once under `~/.compozy/agents/factory-*`, installed from repo `compozy-factory`.
- A project joins the factory by adding `.compozy/factory/factory.toml` and running one attach command.
- No agent prompt, heartbeat, or source script contains a project-specific value.
- `acme-platform` keeps working after cutover with the same behavior, its memory, and re-approved
  sources/authority.
- Names, display names, descriptions and instructions are English; text the agents post (issues,
  comments, Jira) follows `factory.toml [language].posts` (default `pt-BR`).

## User Stories

- US-001–US-003: install, attach, and run in a project — [catalog](_user_stories.md)
- US-004–US-005: cutover of `acme-platform` and memory migration
- US-006: per-workspace New Relic defaults

[Full user stories](_user_stories.md)

## Core Features

1. **Factory repository** `compozy-factory`: `agents/factory-*/` (AGENT.md, HEARTBEAT.md, sources),
   `tools/` (shared scripts such as `factory_intake.py`), `bin/install`, `bin/attach`, `bin/reload`,
   `bin/migrate-memory`, `factory.example.toml`, tests.
2. **Rename and rewrite** (Q19, Q23):

   | Old | New | display_name |
   | --- | --- | --- |
   | `sentinela` | `factory-sentinel` | Factory Sentinel |
   | `escrivao` | `factory-scribe` | Factory Scribe |
   | `maestro` | `factory-dispatcher` | Factory Dispatcher |
   | `vigia-pr` | `factory-pr-watcher` | Factory PR Watcher |
   | `porteiro` | `factory-gatekeeper` | Factory Gatekeeper |
   | `auditor` | `factory-auditor` | Factory Auditor |
   | (planned `revisor`, spec `github-app-review`) | `factory-reviewer` | Factory Reviewer |

   Each gets an English `description` (≤ 280 chars) and an `accepts` list mirroring its input grammar.
   Peer references inside prompts (`agent: maestro`, `ready:` to `porteiro`, …) use the new names.
3. **Project values in `factory.toml`** (Q21): prompts read `.compozy/factory/factory.toml` from the
   workspace root (cwd of every session); source scripts read it the same way.
4. **Per-workspace New Relic defaults** (Q21): `compozy-newrelic` accepts `NEW_RELIC_ACCOUNT_ID` and
   `NEW_RELIC_APP` as per-workspace extension secrets/values. A source without `with.account_id` uses the
   default account; `{{app}}` in `with.where` expands to `NEW_RELIC_APP`. The sentinel's HEARTBEAT then
   carries no account or app.
5. **Cutover** of `acme-platform`: archive old instances, install, migrate memory, re-approve,
   attach, verify one check-in per agent, then delete the old definitions from the project repo.

## Business Rules

- **BR-1** `bin/install` writes only `~/.compozy/agents/factory-*` and `~/.compozy/factory/tools/`. It
  never overwrites a non-`factory-*` directory and never deletes anything outside those paths;
  `--prune` removes `factory-*` agents no longer in the repo.
- **BR-2** Install is idempotent: a second run with no repo change writes nothing and reports
  `unchanged`.
- **BR-3** `bin/attach <workspace>` refuses unless `<workspace>/.compozy/factory/factory.toml` exists and
  validates against the schema (required keys below). It creates one instance per agent listed in
  `factory.toml [team].agents` (default: all six; `factory-reviewer` once `github-app-review` ships).
- **BR-4** Agents must stop with `refused: factory.toml missing <key>` when a key they need is absent;
  no defaulting to old hard-coded values.
- **BR-5** Required `factory.toml` keys: `[repo] github, base, release, merge`; `[jira] site, project`
  (+ existing field maps); `[newrelic] app_name` (account and app also set as workspace extension
  values, US-006); `[ci] runner` (workflow `runs-on` rule); `[language] posts`; `[autorizacao]` renamed
  `[authorization] authors` (BR-6).
- **BR-6** Renamed `factory.toml` keys (`[autorizacao] autores` → `[authorization] authors`;
  `[jira.campos]` → `[jira.fields]`; `[jira.padrao]` → `[jira.defaults]`) are migrated in the same
  cutover commit; the agents read only the new keys.
- **BR-7** Memory migration copies each old agent's memory document to the new agent in the same
  profile/workspace and records the source in the first line; old memory is left in place until the
  operator deletes it.
- **BR-8** Heartbeat source scripts and authority blocks are re-approved by the operator after install;
  `bin/install` prints the exact `compozy agent heartbeat sources approve` / `authority approve` commands
  with hashes.
- **BR-9** Active hours stay in each HEARTBEAT.md (operator-wide, `America/Sao_Paulo`), not per project.

## User Experience

Operator flow: clone `compozy-factory` → `bin/install` → approve printed hashes → in a project, write
`factory.toml` from `factory.example.toml` → set New Relic workspace values → `bin/attach <workspace>`.
Updating: `git pull && bin/install && bin/reload <workspace>`.

## High-Level Technical Constraints

- CompozyOS ≥ 0.7.3. Agent directories cannot be symlinks (discovery opens them with `O_NOFOLLOW`);
  install copies.
- Extension packaging is not used: extension source scripts never execute and extension HEARTBEAT.md is
  write-protected (dispatcher and auditor depend on scripts).
- Global definitions are shadowed by same-name workspace definitions; the `factory-` prefix avoids
  collisions with upstream agents (`maestro`, `pr-watcher`, `orchestrator`).

## Non-Goals (Out of Scope)

- Changing what each agent does (behavior parity is the acceptance bar).
- Per-project active hours.
- Publishing `compozy-factory` publicly; it stays a private repo.

## Open Questions

None.

---

# Part II — Technical

## Executive Summary

A new private repo holds the agent definitions, shared tools and operator scripts. Install copies them
into `$COMPOZY_HOME`. Prompts and scripts resolve every project value from the workspace's
`factory.toml`. `compozy-newrelic` gains per-workspace defaults so the sentinel's sources need no
project literals. `acme-platform` is cut over in one coordinated change.

## MVP Boundary

S1–S3 are the MVP. S3 delivers the Motivating Problem (acme-platform runs on user-level agents). The
`github-app-review` factory slices (F1–F4) then target `compozy-factory` for agent changes and
`acme-platform` for `factory.toml`/`REVIEW.md`.

## Developer Experience

- [Developer experience contract](_dx.md): install/attach/reload/migrate-memory CLI, `factory.toml`
  schema, New Relic workspace values, agent identity table.

## System Architecture

| Component | Location | Responsibility |
| --- | --- | --- |
| `compozy-factory` repo | `~/data/projects/platform/compozy-factory` (git, private remote later) | Source of truth for agents, tools, scripts |
| Installed agents | `~/.compozy/agents/factory-*` | What CompozyOS loads (global origin) |
| Installed tools | `~/.compozy/factory/tools/` | Shared scripts called by prompts with absolute path |
| Project config | `<project>/.compozy/factory/factory.toml` | All project values; versioned in the project |
| `compozy-newrelic` | its repo | Per-workspace account/app defaults |

## Architectural Boundaries

- Agents never import from a project path except `.compozy/factory/factory.toml`.
- `bin/*` scripts touch only `$COMPOZY_HOME/agents/factory-*`, `$COMPOZY_HOME/factory/`, and CompozyOS
  through its CLI.

## Implementation Design

### Core Interfaces

`bin/install [--prune] [--dry-run]`, `bin/attach <workspace> [--agents a,b]`,
`bin/reload <workspace> [agents…]`, `bin/migrate-memory --workspace <ws> --map old=new[,…]`
(exact output in `_dx.md`). Source scripts keep the CompozyOS check-in source v1 contract.

### Data Models

`factory.toml` keys (BR-5/BR-6) are specified in `_dx.md` › factory.toml.

## Impact Analysis

| Component | Impact Type | Description and Risk | Required Action |
| --- | --- | --- | --- |
| `acme-platform/.compozy/agents/*` | deleted (internal) | **Delete targets**: the six definitions; replaced by user-level copies | S3 |
| `acme-platform/.compozy/factory/bin/*` | moved | `factory_intake.py`, tests → `compozy-factory/tools`; `reload-agents` → `bin/reload` | S2, S3 |
| `acme-platform/.compozy/factory/factory.toml` | modified (user config) | New/renamed keys (BR-5/BR-6), migrated in the cutover commit | S3 |
| Always-on instances in `acme-platform` | replaced | Archive old, create new; memory migrated | S3 (operator approvals) |
| `compozy-newrelic` | modified (public extension surface, additive) | Optional `NEW_RELIC_ACCOUNT_ID`, `NEW_RELIC_APP`; `with.account_id` stays accepted | S1 |
| Spec `github-app-review` | modified | F1–F4 use `factory-reviewer`, `factory-pr-watcher`, `factory-gatekeeper` in `compozy-factory` | after S3 |

## Extensibility Integration Plan

`compozy-newrelic` adds two optional env keys; no new kinds. No hooks, MCP sidecars or skills change.

## Agent Manageability Plan

Operators use `bin/*` (wrapping `compozy agent instance`, `agent memory`, `agent heartbeat`). Agents gain
no new tools.

## Config Lifecycle

CompozyOS `config.toml` unchanged. `factory.toml` changes per BR-5/BR-6 (project-versioned).

## Testing Approach

Python unit tests for `tools/` (existing tests move) and a `factory.toml` validator; shell tests for
`bin/install` idempotency and refusal rules in a temporary `COMPOZY_HOME`; bun tests in `compozy-newrelic`;
cutover verified by one dry-run check per agent in `acme-platform`. Details in `_tests.md`.

## Development Sequencing

### Build Order

S1 (`compozy-newrelic`) and S2 (`compozy-factory`) in parallel; S3 (cutover) after both.

### Technical Dependencies

Operator approvals of source hashes and authority blocks during S3.

## Monitoring and Observability

`bin/*` print one line per action (`installed`, `unchanged`, `pruned`, `attached`, `migrated`). Check-in
reports remain the runtime signal.

## Technical Considerations

### Key Decisions

See ADR-001 (install by copy from a separate repo) and ADR-002 (project values only in `factory.toml`).

### Known Risks

| Risk | Mitigation |
| --- | --- |
| Behavior drift while translating prompts | Side-by-side review per agent; dry-run checks (IT-010) |
| Missed literal left in a prompt | UT-020 scans installed files for a denylist of project literals |
| Forgetting to re-approve sources | `bin/install` prints exact commands; `bin/attach` refuses while a source is unapproved |

## Safety Invariants

1. `bin/install` never writes outside `$COMPOZY_HOME/agents/factory-*` and `$COMPOZY_HOME/factory/`.
2. No installed agent file contains a value from the project denylist (UT-020).
3. Old memory is never deleted by tooling.

## File References

### Repo Files

- `acme-platform/.compozy/agents/*/` — current definitions to port.
- `acme-platform/.compozy/factory/factory.toml` — current config to extend.
- `acme-platform/.compozy/factory/bin/` — tools and tests to move.
- `compozy-newrelic/src/params.ts:25-32` — `account_id` requirement to relax.
- `compozy/internal/config/agent_discovery.go:57-115` — discovery without symlinks.

### Design and Analysis Sources

- Session 2026-11-17, decisions Q19–Q23.

## Assumptions and Defaults

- Private remote for `compozy-factory` is created later by the operator; work happens locally.
- `[language] posts = "pt-BR"` for `acme-platform`.
- `factory-reviewer` is added to `[team].agents` only when `github-app-review` F1 lands.

## Architecture Decision Records

- [ADR-001: Separate repo, installed by copy](adrs/adr-001.md)
- [ADR-002: Project values only in factory.toml; New Relic defaults per workspace](adrs/adr-002.md)
