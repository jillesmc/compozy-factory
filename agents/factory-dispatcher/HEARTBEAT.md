---
version: 1
enabled: true
summary: "Accept issues released with factory:deliver by an authorized author and check the work log."
preferences:
  min_interval: "60m"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "08:00"
      end: "20:00"
context:
  include: [work_log, session_health]
  sources:
    - id: deliver
      script: deliver
---

# Check-in

The `deliver` source lists the open issues whose last `factory:deliver` label came from an author in `factory.toml`. For each one still without a work log entry, propose `accept: #<n>`; for those already in `[~]`, cross them with the session health and propose a return or a close with evidence. For each `[~]` entry with `pr:`, check the PR (`code.pr_get`): open and without `watcher: ok` (or legacy `vigia: ok`) on the entry, propose resending `watch:` to the factory-pr-watcher; open, green and idle for more than 2 h without merge, propose chasing the factory-pr-watcher and the factory-gatekeeper; merged or closed without the entry closed, propose closing it with the evidence. Triage is read-only: do not edit the log or dispatch in this turn.
