---
version: 1
enabled: true
summary: "Event-driven merges; the check-in only checks whether a green promotion PR is waiting for approval."
preferences:
  min_interval: "6h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "08:00"
      end: "20:00"
context:
  include: [monitors_fired]
---

# Check-in

If there is an open promotion PR (`base` → `release` of `factory.toml` `[roles.code.<provider>]`) with green checks waiting for approval for more than 6 hours, propose reminding the operator. If a deploy monitor fired without its result being handled, propose handling it. If a continuation pointer is still `running` with no armed promotion or deploy monitor, no promotion in flight and no train in Deploy, propose resuming it (AGENT.md, Recovery). Otherwise, nothing to do.
