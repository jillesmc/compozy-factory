---
version: 1
enabled: true
summary: "Classify new New Relic errors, error logs and incidents of the project's app."
preferences:
  min_interval: "24h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "10:00"
      end: "12:00"
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
---

# Check-in

Once a day (10:00 to 12:00 BRT window). The sources already filtered what is new or grew. Classify each item as the AGENT.md says and return proposals; do not send `intake` without a proposal chosen by the operator.
