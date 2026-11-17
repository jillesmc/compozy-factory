# Test Specification: Factory agents at user level

Companion to `_spec.md`.

## Strategy

- `compozy-factory`: pytest for `tools/` (existing `test_factory_intake.py`, `test_auditor_due.py` move
  here) and for `bin/` scripts, run against a temporary `COMPOZY_HOME`; CompozyOS CLI calls are faked at the
  subprocess boundary except in IT cases.
- `compozy-newrelic`: bun tests in its existing suite.
- Cutover: live checks on the operator host.

## Coverage Matrix

| Source | Behavior | Unit | Integration | E2E |
| --- | --- | --- | --- | --- |
| US-001 | Install, idempotency, approvals list | UT-001–UT-004 | IT-001 | — |
| US-001.EC-1–EC-3 | Non-factory untouched, stale/prune, symlink refusal | UT-005–UT-007 | — | — |
| US-002 | Attach and refusals | UT-010–UT-012 | IT-002 | — |
| US-003 | No project literals; refusal on missing key | UT-020, UT-021 | — | E2E-001 |
| US-004 | Cutover | — | IT-010 | E2E-001 |
| US-005 | Memory migration | UT-030–UT-032 | IT-003 | — |
| US-006 | NR defaults | UT-040–UT-045 | — | — |

## Unit Tests

### bin/install
- **UT-001** (happy): clean home → six `agents/factory-*` dirs and `factory/tools/factory_intake.py`.
- **UT-002** (idempotency): second run → all `unchanged`, no write (mtimes equal).
- **UT-003** (happy): changed `sources/deliver` → output contains `sources approve factory-dispatcher deliver --sha256 <new hash>`.
- **UT-004** (happy): changed authority block → output contains `authority approve <agent> --sha256 <digest>`.
- **UT-005** (boundary): `agents/general` present → byte-identical after install.
- **UT-006** (state): `agents/factory-old` not in repo → `stale`; with `--prune` → removed.
- **UT-007** (error): `agents/factory-scribe` symlink → `symlink_not_supported`, exit 1, nothing written.

### bin/attach
- **UT-010** (happy): valid toml, fake CLI → one `instance create` per `[team].agents`.
- **UT-011** (error): missing `[ci] runner` → refusal naming the key; zero CLI calls.
- **UT-012** (idempotency): instance exists → `exists`, no create.

### Agent content
- **UT-020** (happy): installed files contain none of: `acme-reports`, `acme-portal`,
  `1234567`, `ACME-`, `acme`, `codebuild-acme`, and the backticked branch names
  `` `staging` `` and `` `production` `` (plain English words such as "production incident" are allowed).
- **UT-021** (happy): every AGENT.md has English `display_name`, `description` ≤ 280 chars, `accepts`
  matching its `## Input` block, and peer references only to `factory-*` names.

### bin/migrate-memory
- **UT-030** (happy): fake memory read of `vigia-pr` → write to `factory-pr-watcher` with first line
  `Migrated from vigia-pr (<workspace>) on <date>`.
- **UT-031** (state): empty source → `skipped (empty)`.
- **UT-032** (error): non-empty target without `--append` → refusal.

### compozy-newrelic params
- **UT-040** (happy): no `with.account_id`, `NEW_RELIC_ACCOUNT_ID=1234567` → accountId 1234567.
- **UT-041** (happy): both present → `with.account_id` wins.
- **UT-042** (error): neither → `params_invalid` naming both.
- **UT-043** (happy): `{{app}}` + `NEW_RELIC_APP=acme-portal-production` → expanded where.
- **UT-044** (error): `{{app}}` without `NEW_RELIC_APP` → `params_invalid` naming it.
- **UT-045** (error): `NEW_RELIC_APP` with `'` → `params_invalid`; no query sent.

## Integration Tests

- **IT-001**: real `bin/install` into an isolated `COMPOZY_HOME` with a QA daemon → `compozy agent list`
  shows six `factory-*` agents, origin global.
- **IT-002**: in that daemon, `bin/attach` on a scratch workspace with a valid `factory.toml` → six
  active instances.
- **IT-003**: in that daemon, seed memory for `vigia-pr`, run `bin/migrate-memory` → `compozy agent
  memory inspect factory-pr-watcher` shows the entries.
- **IT-010**: cutover on the operator host → `compozy agent instance check <agent> --dry-run
  --workspace acme-platform` succeeds for all six.

## End-to-End Tests

- **E2E-001**: after cutover, the next real check-in of `factory-sentinel` (active hours) produces a
  report referencing `acme-portal-production`, read from workspace values, and `factory-pr-watcher`
  validates the next factory PR against `[repo]` and `[ci]` from `factory.toml`.
