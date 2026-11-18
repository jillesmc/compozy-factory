#!/usr/bin/env bash
# IT-001..IT-004 against a real, isolated CompozyOS daemon.
#
# Starts a throwaway daemon with its own COMPOZY_HOME, HTTP port and socket, runs the real
# bin/install, bin/attach and bin/migrate-memory against it, checks the gatekeeper's tool allowlist, and stops it on exit. Every CLI call
# runs under `env -i` so the caller's COMPOZY_* session identity can never reach the operator daemon.
# Usage: tests/it/run.sh            (KEEP=1 keeps the temp dir for inspection)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
T=$(mktemp -d /tmp/cf-it.XXXXXX)
HOME_DIR=$T/home SCRATCH=$T/scratch
PORT=$(python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])')
mkdir -p "$HOME_DIR" "$SCRATCH/.compozy/factory"

iso() { env -i HOME="$HOME" PATH="$PATH" USER="${USER:-}" COMPOZY_HOME="$HOME_DIR" "$@"; }
cz() { iso compozy "$@"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
step() { printf '\n## %s\n' "$*"; }

cleanup() {
  cz daemon stop >/dev/null 2>&1 || true
  [ "${KEEP:-}" = 1 ] && echo "kept $T" || rm -rf "$T"
}
trap cleanup EXIT

# Heartbeat wakes are off so no check-in turn ever runs. The agents' `claude-mm4` provider is
# declared so instances can be created; sessions are lazy and no provider process is launched.
cat > "$HOME_DIR/config.toml" <<EOF
[http]
host = "127.0.0.1"
port = $PORT

[daemon]
socket = "$T/daemon.sock"

[agents.heartbeat]
enabled = false

[providers.claude-mm4]
display_name = "Claude (mm4, IT)"
harness = "acp"
command = "npx -y @agentclientprotocol/claude-agent-acp@latest"
runtime_provider = "claude"
auth_mode = "native_cli"

[tools.policy]
trusted_sources = ["extension:github-app-review"]
EOF

step "isolated daemon (COMPOZY_HOME=$HOME_DIR, port $PORT)"
cz daemon start >/dev/null
cz workspace list -o json | json 'assert d == [], d; print("isolation: workspace list is empty (fresh home)")'
json 'assert d["port"] == '"$PORT"', d; print("daemon.json port", d["port"], "pid", d["pid"])' < "$HOME_DIR/daemon.json"

step "IT-001 install"
iso "$REPO/bin/install" | tee "$T/install.out"
for _ in $(seq 1 30); do  # the daemon discovers new definitions asynchronously
  [ "$(cz agent list -o json | json 'print(sum(a["name"].startswith("factory-") for a in d))')" = 7 ] && break
  sleep 1
done
cz agent list -o json | json '
f = sorted((a["name"], a["origin"]) for a in d if a["name"].startswith("factory-"))
print("\n".join(f"{n} origin={o}" for n, o in f))
assert len(f) == 7 and all(o == "global" for _, o in f), f'
cz workspace add "$SCRATCH" --name scratch -o json | json 'print("workspace", d["name"], d["id"])'
python3 - "$T/install.out" > "$T/approvals" <<'EOF'
import sys
for line in open(sys.argv[1]):
    if line.strip().startswith("compozy agent heartbeat"):
        print(line.strip())
EOF
while read -r _ _ _ kind _ agent rest; do
  printed=$(sed -n 's/.*--sha256 \(sha256:[0-9a-f]*\).*/\1/p' <<<"$rest")
  if [ "$kind" = sources ]; then
    script=${rest%% *}
    daemon=$(cz agent heartbeat sources "$agent" --workspace scratch -o json |
      json "print(next(s['sha256'] for s in d['scripts'] if s['name'] == '$script'))")
    label="source $agent/$script"
  else
    daemon=$(cz agent heartbeat authority show "$agent" --workspace scratch -o json | json 'print(d["digest"])')
    label="authority $agent"
  fi
  [ "$printed" = "$daemon" ] || { echo "MISMATCH $label printed=$printed daemon=$daemon"; exit 1; }
  echo "hash match $label $daemon"
done < "$T/approvals"

step "approve: run each command exactly as bin/install printed it (operator, isolated daemon)"
while read -r line; do
  read -ra cmd <<<"$line"
  (cd "$T" && iso "${cmd[@]}" >/dev/null 2>"$T/approve.err") ||
    { echo "FAILED: $line"; cat "$T/approve.err"; exit 1; }
  echo "ok: $line"
done < "$T/approvals"
cz agent heartbeat sources factory-dispatcher --workspace scratch -o json | json 'assert all(s["approved"] for s in d["scripts"]); print("daemon: factory-dispatcher sources approved")'
cz agent heartbeat sources factory-auditor --workspace scratch -o json | json 'assert all(s["approved"] for s in d["scripts"]); print("daemon: factory-auditor sources approved")'
cz agent heartbeat authority show factory-auditor --workspace scratch -o json | json 'assert d["status"] == "approved", d["status"]; print("daemon: factory-auditor authority", d["status"])'

step "IT-002 attach scratch"
cp "$REPO/factory.example.toml" "$SCRATCH/.compozy/factory/factory.toml"
echo "attach by path: bin/attach $SCRATCH"
iso "$REPO/bin/attach" "$SCRATCH"
cz agent instance list --workspace scratch -o json | json '
act = sorted(i["agent_name"] for i in d["instances"] if i["state"] == "active")
print("active instances:", " ".join(act))
assert act == sorted(["factory-sentinel", "factory-scribe", "factory-dispatcher", "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]), act'
echo "attach by name: bin/attach scratch"
iso "$REPO/bin/attach" scratch | tee "$T/attach2.out"
[ "$(grep -c '^exists factory-' "$T/attach2.out")" = 6 ] || { echo "attach by name did not see the six instances"; exit 1; }

step "IT-003 migrate vigia-pr memory"
mkdir -p "$SCRATCH/.compozy/agents/vigia-pr"
printf -- '---\nname: vigia-pr\ndisplay_name: Vigia de PR\ndescription: legacy fixture\nkind: always_on\nprovider: claude-mm4\nmodel: sonnet\n---\n\nLegacy fixture.\n' \
  > "$SCRATCH/.compozy/agents/vigia-pr/AGENT.md"
for _ in $(seq 1 30); do  # the daemon picks up the new workspace agent asynchronously
  cz agent memory inspect vigia-pr --workspace scratch -o json >/dev/null 2>&1 && break
  sleep 1
done
cz agent memory add vigia-pr "#226 -> sess-abc (round 1)" --workspace scratch -o json >/dev/null
cz agent memory add vigia-pr "PRs target the base branch" --workspace scratch -o json >/dev/null
iso "$REPO/bin/migrate-memory" --workspace scratch --map vigia-pr=factory-pr-watcher
echo "compozy agent memory inspect factory-pr-watcher:"
cz agent memory inspect factory-pr-watcher --workspace scratch -o json | json '
print(d["content"], end="")
assert d["content"].startswith("Migrated from vigia-pr (scratch) on ") and "#226 -> sess-abc" in d["content"]'
cz agent memory inspect vigia-pr --workspace scratch -o json | json 'assert "#226" in d["content"]; print("old vigia-pr memory still readable")'
echo "re-run without --append:"
iso "$REPO/bin/migrate-memory" --workspace scratch --map vigia-pr=factory-pr-watcher || echo "exit $?"

step "IT-004 gatekeeper tool allowlist (agent policy)"
# The installed github-app-review package is only read as an install source; dummy secrets let it
# register its tools in this home. No tool is invoked.
EXT=${GHAR_EXT:-$HOME/.compozy/extensions/github-app-review}
if [ -f "$EXT/extension.toml" ]; then
  cz extension install "$EXT" --allow-unverified --yes >/dev/null
  for kv in GITHUB_APP_BASE=develop GITHUB_APP_ID=1 GITHUB_APP_INSTALLATION_ID=1 GITHUB_APP_PRIVATE_KEY=dummy \
            "GITHUB_APP_PROMOTION=acme/acme-api:develop->main" GITHUB_APP_PROMOTION_APPROVERS=octocat GITHUB_APP_REPOS=acme/acme-api; do
    printf '%s' "${kv#*=}" | cz extension secrets set github-app-review --env "${kv%%=*}" --value-stdin >/dev/null
  done
  cz extension enable github-app-review >/dev/null
  echo "github-app-review installed from $EXT (dummy secrets)"
else
  echo "github-app-review not found at $EXT: checking native tools only"
fi
# Same for the newrelic extension, so the gatekeeper's ext__newrelic__* pattern is checked too.
NR=${NR_EXT:-$HOME/.compozy/extensions/newrelic}
if [ -f "$NR/extension.toml" ]; then
  cz extension install "$NR" --allow-unverified --yes >/dev/null
  for kv in NEW_RELIC_ACCOUNT_ID=1 NEW_RELIC_API_KEY=dummy NEW_RELIC_APP=acme-api; do
    printf '%s' "${kv#*=}" | cz extension secrets set newrelic --env "${kv%%=*}" --value-stdin >/dev/null
  done
  cz extension enable newrelic >/dev/null
  echo "newrelic installed from $NR (dummy secrets)"
else
  echo "newrelic not found at $NR: ext__newrelic__* not checked"
fi
for _ in $(seq 1 30); do  # extension tools register asynchronously
  cz tool list --workspace scratch -o json | json '
import sys
ids = {t["descriptor"]["tool_id"] for t in d["tools"]}
need = [p for p, on in (("ext__github_app_review__", "'"$([ -f "$EXT/extension.toml" ] && echo 1)"'"),
                        ("ext__newrelic__", "'"$([ -f "$NR/extension.toml" ] && echo 1)"'")) if on]
sys.exit(0 if all(any(i.startswith(p) for i in ids) for p in need) else 1)' && break
  sleep 1
done
cz tool list --workspace scratch --agent factory-gatekeeper -o json |
  EXT_ON=$([ -f "$EXT/extension.toml" ] && echo 1) NR_ON=$([ -f "$NR/extension.toml" ] && echo 1) json '
import os
pol = {t["descriptor"]["tool_id"]: t["decision"]["agent_policy_result"] for t in d["tools"]}
want = ["compozy__session_prompt", "compozy__monitor_create", "compozy__notify", "compozy__terminal_exec",
        "compozy__session_status", "compozy__worktree_remove"]
deny = []
if os.environ.get("EXT_ON"):
    want += ["ext__github_app_review__merge_gate", "ext__github_app_review__upsert_promotion"]
    deny = ["ext__github_app_review__submit_review", "ext__github_app_review__pr_context"]
if os.environ.get("NR_ON"):
    nr = sorted(t for t in pol if t.startswith("ext__newrelic__"))
    assert nr, "newrelic installed but no ext__newrelic__ tool registered"
    want += nr
for tool in want + deny:
    print(f"factory-gatekeeper {tool}: {pol.get(tool)}")
assert all(pol.get(t) == "allowed" for t in want), want
assert all(pol.get(t) == "denied" for t in deny), deny
natives = [t for t in pol if t.startswith("compozy__")]
assert natives and all(pol[t] == "allowed" for t in natives), "compozy__* must allow every native tool"
print(f"all {len(natives)} compozy__* native tools allowed")'

step "IT-001..IT-004 passed"
