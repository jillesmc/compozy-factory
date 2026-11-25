---
version: 1
enabled: true
summary: "Classify new New Relic errors, error logs and incidents of the project's app."
preferences:
  min_interval: "2h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "08:00"
      end: "22:00"
context:
  include: []
  sources:
    - id: nr-errors
      kind: newrelic/errors
      with:
        where: "appName = '{{app}}'"
        event_types: ["TransactionError"]
        window: "24h"
        min_count: 1
        notify_existing: true   # the first read already brings the current errors (the factory-scribe dedupes)
    - id: nr-logs
      kind: newrelic/logs
      with:
        where: "entity.name = '{{app}}' AND level IN ('ERROR', 'error', 'CRITICAL', 'critical')"
        facet: "message"
        window: "24h"
        threshold: 3
    - id: nr-incidents
      kind: newrelic/issues
      with:
        priorities: ["CRITICAL", "HIGH"]
    - id: nr-scope   # optional: no items unless .compozy/newrelic.toml sets logs_where and logs_facet
      kind: newrelic/logs
      with:
        where: "{{logs_where}}"
        facet: "{{logs_facet}}"
        window: "24h"
        threshold: 3
---

# Check-in

Every 2h within the 08:00-22:00 BRT window. The sources already filtered what is new or grew. Classify each item as the AGENT.md says and return proposals; do not send `intake` without a proposal chosen by the operator. Items of `nr-scope` are scope signals (AGENT.md, Scope signals): errors of this project seen inside the services that consume it.
