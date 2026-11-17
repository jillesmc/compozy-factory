# Slices: factory-user-agents

| Slice | Layer | Depends on | Scope | Spec refs |
| ----- | ----- | ---------- | ----- | --------- |
| S1 | extension (`compozy-newrelic`) | none | Optional `NEW_RELIC_ACCOUNT_ID` default and `{{app}}` expansion from `NEW_RELIC_APP`; README; UT-040–UT-045 | US-006 |
| S2 | factory repo (`compozy-factory`) | none | Port the six agents with new names, English identity and instructions, project values read from `factory.toml`; move tools and tests; `bin/install`, `bin/attach`, `bin/reload`, `bin/migrate-memory`; `factory.example.toml`; UT-001–UT-032; IT-001–IT-003 | US-001, US-002, US-003, US-005 |
| S3 | cutover (`acme-platform` + operator) | S1, S2 | `factory.toml` new/renamed keys; delete old definitions and `factory/bin`; archive old instances; install; migrate memory; operator approvals; NR workspace values; attach; IT-010; E2E-001 | US-004, Motivating Problem |
