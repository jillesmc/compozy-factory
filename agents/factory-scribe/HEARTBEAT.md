---
version: 1
enabled: true
summary: "Event-driven intake; no proactive check-in."
preferences:
  min_interval: "24h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "08:00"
      end: "20:00"
context:
  include: []
---

# Check-in

Nothing to do on your own: work arrives as `intake:`, `jira:` or `sweep:`.
