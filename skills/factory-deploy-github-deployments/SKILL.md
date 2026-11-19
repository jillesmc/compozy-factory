---
name: factory-deploy-github-deployments
description: Factory adapter for the deploy role through GitHub Deployments (deploy of a merge commit). Follow contracts/deploy.md.
role: deploy
provider: github-deployments
requires_extensions: []
requires_roles: {"code": "github"}
schema:
  environment: {"type": "string", "required": false}
---

# deploy / github-deployments

The deploy tool reports to GitHub: a Deployment appears for the merge commit's `sha` in environment
`{environment}` (`[roles.deploy.github-deployments] environment`, default `[roles.code.github] release`),
and its statuses end in `success`, `failure` or `error`. Never query the deploy platform itself.
`gh` uses the keyring login (`env -u GH_TOKEN`).

## deploy.wait

Run with `compozy__terminal_exec` a bounded command (at most `bound` minutes, one read every 30 s)
that reads `gh api 'repos/<repo>/deployments?sha=<merge-sha>&environment=<env>'` and the newest status
of each Deployment (`gh api repos/<repo>/deployments/<id>/statuses`), exiting on the first terminal
state; register `compozy__monitor_create kind: terminal` on it. Outcome: `success` when every
Deployment's newest status is `success` (`finished_at` its `created_at`, `link` its
`log_url`/`target_url`); `failure` or `error` when any has it; `absent` when no Deployment exists
after 15 min or none finished within the bound.
