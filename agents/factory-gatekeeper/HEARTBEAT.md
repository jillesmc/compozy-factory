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

If there is an open promotion PR (base branch → release branch of `factory.toml` `[repo]`) with green checks waiting for approval for more than 6 hours, propose reminding the operator. If a deploy monitor fired without its result being handled, propose handling it. Otherwise, nothing to do.
