# Developer Experience: Factory agents at user level

Companion to `_spec.md` and `_tests.md`.

## Golden Path

```console
$ git clone <private-remote>/compozy-factory ~/data/projects/platform/compozy-factory   # or the local repo
$ cd ~/data/projects/platform/compozy-factory && bin/install
installed factory-sentinel
installed factory-scribe
installed factory-dispatcher
installed factory-pr-watcher
installed factory-gatekeeper
installed factory-auditor
installed tools (factory_intake.py)
approve before attaching:
  compozy agent heartbeat sources approve factory-dispatcher deliver --sha256 9c1f… --yes
  compozy agent heartbeat sources approve factory-auditor due --sha256 41ab… --yes
  compozy agent heartbeat authority approve factory-pr-watcher --sha256 77d0… --yes

$ cp factory.example.toml ~/data/projects/acme-api/.compozy/factory/factory.toml   # then edit
$ printf '1234567' | compozy extension secrets set newrelic --workspace acme-api --value-stdin --env NEW_RELIC_ACCOUNT_ID
$ printf 'acme-api-production' | compozy extension secrets set newrelic --workspace acme-api --value-stdin --env NEW_RELIC_APP

$ bin/attach acme-api
attached factory-sentinel     (ainst-…)
attached factory-scribe       (ainst-…)
attached factory-dispatcher   (ainst-…)
attached factory-pr-watcher   (ainst-…)
attached factory-gatekeeper   (ainst-…)
attached factory-auditor      (ainst-…)
```

Update: `git pull && bin/install && bin/reload acme-api`.

## CLI

| Invocation | Output | Exit |
| --- | --- | --- |
| `bin/install [--dry-run] [--prune]` | One line per agent: `installed`, `updated`, `unchanged`, `stale`, `pruned`; then approval commands | 0; 1 on refusal |
| `bin/attach <workspace> [--agents a,b]` | `attached <agent> (<instance>)` or `exists <agent>` | 0; 1 on refusal |
| `bin/reload <workspace> [agent…]` | Archive + create per agent, as the old `reload-agents` | 0 |
| `bin/migrate-memory --workspace <ws> --map old=new[,old=new] [--append]` | `migrated old -> new (N lines)` / `skipped old (empty)` | 0; 1 on refusal |

Refusals:

```console
$ bin/attach acme-api
refused: /home/operator/data/projects/acme-api/.compozy/factory/factory.toml missing [ci] runner
$ bin/install
refused: /home/operator/.compozy/agents/factory-scribe is a symlink (symlink_not_supported)
$ bin/migrate-memory --workspace acme-platform --map vigia-pr=factory-pr-watcher
refused: factory-pr-watcher already has memory; pass --append
```

## factory.toml

Lives at `<project>/.compozy/factory/factory.toml`, versioned in the project. Keys read by the agents:

```toml
[language]
posts = "pt-BR"                      # language of issues, comments and Jira text the agents write

[authorization]
authors = ["jillesmc"]               # logins whose `factory:deliver` label is honored

[team]
agents = ["factory-sentinel", "factory-scribe", "factory-dispatcher",
          "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]

[repo]
github = "acme/acme-reports"
base = "staging"
release = "production"
merge = "merge"

[ci]
runner = "codebuild-acme-${{ github.run_id }}-${{ github.run_attempt }}"

[newrelic]
app_name = "acme-portal-production"   # also set as NEW_RELIC_APP for the workspace (sources)

[runtime]
executor = { agent = "code_implementer", provider = "claude-mm1", model = "claude-opus-5-5", reasoning_effort = "high" }

[jira]
site = "acme.atlassian.net"
project = "PLATFORM"
assets_workspace = "5cee96d4-e960-4188-91b8-f6ce02d7b73f"
issuetype = { bugfix = "10004", feature = "10008" }

[jira.fields]        # was [jira.campos]
documentacao_anexa = "customfield_14840"
utilizacao_ia = "customfield_14841"
tipo_atividade = "customfield_12088"
qual_tipo_de_valor = "customfield_24569"
plataformas_afetadas = "customfield_24570"

[jira.defaults]      # was [jira.padrao]
documentacao_anexa = "42185"
utilizacao_ia = "42186"
# [jira.tipo_atividade], [jira.tipo_de_valor], [jira.plataformas] keep their names and values
```

## New Relic workspace values (`compozy-newrelic`)

| Env | Kind | Effect |
| --- | --- | --- |
| `NEW_RELIC_ACCOUNT_ID` | optional, per workspace | Used when a source omits `with.account_id` |
| `NEW_RELIC_APP` | optional, per workspace | Replaces `{{app}}` in `with.where` |

Sentinel HEARTBEAT after the change:

```yaml
    - id: nr-errors
      kind: newrelic/errors
      with:
        where: "appName = '{{app}}'"
        event_types: ["TransactionError"]
        window: "24h"
        min_count: 1
        notify_existing: true
```

## Agent identities

| Name | display_name | description |
| --- | --- | --- |
| `factory-sentinel` | Factory Sentinel | Reads New Relic errors, error logs and incidents for the project's app, classifies each signal and proposes to the operator which ones deserve an issue. Never opens issues or writes code. |
| `factory-scribe` | Factory Scribe | Turns findings and reports into deduplicated GitHub issues and, when the item is actionable, into the official Jira work item with the required fields. |
| `factory-dispatcher` | Factory Dispatcher | Accepts issues released with `factory:deliver`, sizes them, dispatches implementation to an isolated worktree and hands the PR to the PR watcher. |
| `factory-pr-watcher` | Factory PR Watcher | Follows executor PRs until they are green and reviewed, returns failures and findings to the executor, and hands ready heads to the gatekeeper. |
| `factory-gatekeeper` | Factory Gatekeeper | Revalidates ready PRs, merges them into the base branch, maintains the promotion PR to the release branch, and closes the Jira item after deploy. |
| `factory-auditor` | Factory Auditor | One window after deploy, checks in New Relic whether a closed factory issue's problem is gone; records the evidence on the issue and in Jira and reopens what came back. |

## Errors

| Condition | Message | Action |
| --- | --- | --- |
| Agent dir is a symlink | `refused: <path> is a symlink (symlink_not_supported)` | Replace with a real directory |
| `factory.toml` missing/invalid | `refused: <path> missing <key>` | Fix the file |
| Unapproved source at attach | `refused: factory-dispatcher source deliver not approved; run <command>` | Approve |
| Memory target not empty | `refused: <agent> already has memory; pass --append` | Decide |
| Agent sees missing key | `refused: factory.toml missing <key>` (agent reply) | Fix the file |
| NR default absent | `params_invalid: set with.account_id or NEW_RELIC_ACCOUNT_ID for this workspace` | Set one |
