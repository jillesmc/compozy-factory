---
version: 1
enabled: true
summary: "Re-verify watched PRs only when a monitor wake was lost."
preferences:
  min_interval: "2h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "08:00"
      end: "20:00"
context:
  include: [monitors_fired]
---

# Check-in

The `pull_request` monitors already wake this agent. In the check-in, only check whether a PR watched in memory was left without an active monitor or without verification since its last head change, and propose re-verifying it.
