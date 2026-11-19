---
version: 1
enabled: true
summary: "Audit the closed factory issues whose deploy completed the observation window."
preferences:
  min_interval: "24h"
  active_hours:
    - timezone: "America/Sao_Paulo"
      start: "12:00"
      end: "14:00"
context:
  include: []
  sources:
    - id: due
      script: due
actions:
  - id: auditar
    title: "Audit the fix in production"
    instructions: "Run the post-deploy audit of the proposal's issue following the AGENT.md: measure before and after the deploy with signals.verify_gone, comment the verdict with the evidence and the factory-audit marker on the issue (and on its work item, if it has its own key) and, on regression, reopen the issue and warn the operator. Use the item's attributes: number, deploy_at, window_days, attempt, max_attempts."
    auto_accept: true
---

# Check-in

The `due` source already filtered the closed `factory` issues whose post-deploy window elapsed and that have no verdict yet. This step is triage only: do not query the observability tool, do not read the issue and do not comment anything. For each item, return a proposal with `action` equal to `auditar`, `item_keys` with the item's key, title `auditar: #<number> <short title>` and a one-line body, without backticks or brackets: `auditar: #<number> deploy=<deploy_at> janela=<window_days> tentativa=<attempt>/<max_attempts>`. Answer only with the proposals object, with no text before or after.
