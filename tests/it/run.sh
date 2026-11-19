#!/usr/bin/env bash
# IT-001..IT-004 (factory-user-agents), IT-007..IT-009 and IT-001 (factory-roles: bin/migrate-config, then
# bin/attach --dry-run) against a real, isolated CompozyOS daemon; then IT-006 (tests/it/activation.sh, the
# activation runbook R0-R4 and rollback from the released extension builds) on a second isolated daemon.
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

# Heartbeat wakes are off so no check-in turn ever runs. The agents' providers (`claude-mm4`, and
# `claude` for the auditor) run tests/it/fake-acp: instances can be created and the boundary prompt of
# bin/attach gets its turn, but no real provider (no model, no operator credential) is ever launched.
cat > "$HOME_DIR/config.toml" <<EOF
[http]
host = "127.0.0.1"
port = $PORT

[daemon]
socket = "$T/daemon.sock"

[agents.heartbeat]
enabled = false

[providers.claude-mm4]
display_name = "Claude (mm4, IT stub)"
harness = "acp"
command = "$REPO/tests/it/fake-acp"
runtime_provider = "claude"
auth_mode = "native_cli"

[providers.claude]
display_name = "Claude (IT stub)"
harness = "acp"
command = "$REPO/tests/it/fake-acp"
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
printf 'app = "acme-api-prod"\naccount_id = 1234567\n' > "$SCRATCH/.compozy/newrelic.toml"
echo "attach by path: bin/attach $SCRATCH --allow-unsupported-adapter (no conformance records before the S5 walks)"
iso "$REPO/bin/attach" "$SCRATCH" --allow-unsupported-adapter | tee "$T/attach1.out"
[ "$(grep -c '^boundary factory-.* ok$' "$T/attach1.out")" = 6 ] || { echo "expected one boundary prompt per attached agent"; exit 1; }
cz agent instance list --workspace scratch -o json | json '
act = sorted(i["agent_name"] for i in d["instances"] if i["state"] == "active")
print("active instances:", " ".join(act))
assert act == sorted(["factory-sentinel", "factory-scribe", "factory-dispatcher", "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]), act'
echo "attach by name: bin/attach scratch"
iso "$REPO/bin/attach" scratch --allow-unsupported-adapter | tee "$T/attach2.out"
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
dummy_secrets() {  # $1 extension, $2 dir: a dummy value for every env name this build declares (they change by release)
  for name in $(python3 -c 'import sys, tomllib; print(" ".join(tomllib.load(open(sys.argv[1], "rb"))["extension"]["requires_env"]))' "$2/extension.toml"); do
    case $name in
      GITHUB_APP_BASE) v=develop ;; GITHUB_APP_PROMOTION) v="acme/acme-api:develop->main" ;;
      GITHUB_APP_PROMOTION_APPROVERS) v=octocat ;; GITHUB_APP_REPOS) v=acme/acme-api ;;
      GITHUB_APP_POLICY_FILE) v="$T/policy.toml" ;; NEW_RELIC_APP) v=acme-api ;; *_ID) v=1 ;; *) v=dummy ;;
    esac
    printf '%s' "$v" | cz extension secrets set "$1" --env "$name" --value-stdin >/dev/null
  done
}
EXT=${GHAR_EXT:-$HOME/.compozy/extensions/github-app-review}
if [ -f "$EXT/extension.toml" ]; then
  cz extension install "$EXT" --allow-unverified --yes >/dev/null
  dummy_secrets github-app-review "$EXT"
  cz extension enable github-app-review >/dev/null
  echo "github-app-review installed from $EXT (dummy secrets)"
else
  echo "github-app-review not found at $EXT: checking native tools only"
fi
# Same for the newrelic extension, so the gatekeeper's ext__newrelic__* pattern is checked too.
NR=${NR_EXT:-$HOME/.compozy/extensions/newrelic}
if [ -f "$NR/extension.toml" ]; then
  cz extension install "$NR" --allow-unverified --yes >/dev/null
  dummy_secrets newrelic "$NR"
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
    deny += nr  # the gatekeeper has no signals role: its allowlist dropped ext__newrelic__* (factory-roles S4)
for tool in want + deny:
    print(f"factory-gatekeeper {tool}: {pol.get(tool)}")
assert all(pol.get(t) == "allowed" for t in want), want
assert all(pol.get(t) == "denied" for t in deny), deny
natives = [t for t in pol if t.startswith("compozy__")]
assert natives and all(pol[t] == "allowed" for t in natives), "compozy__* must allow every native tool"
print(f"all {len(natives)} compozy__* native tools allowed")'

step "IT-007 reviewer digest read back by bin/install equals the daemon's semantic digest"
python3 - "$HOME_DIR/factory/installed.json" "$(cz agent info factory-reviewer --workspace scratch -o json)" <<'EOF'
import json, sys
rec = json.load(open(sys.argv[1]))["reviewer"]
info = json.loads(sys.argv[2])
print("installed.json reviewer", rec["definition_digest"][:12], "daemon", info["definition_digest"][:12],
      "origin", info["origin"], "layer", info.get("layer"))
assert rec["definition_digest"] == info["definition_digest"], "recorded digest is the daemon's"
assert (info["origin"], info.get("layer")) == ("global", "user")
EOF
iso python3 "$HOME_DIR/factory/tools/factory_guard.py" reviewer scratch | tee "$T/guard.out"
grep -q '^ok: factory-reviewer origin global layer user' "$T/guard.out"

# Shared fixtures for IT-008/IT-009: a fake `gh` for the bundled code/github adapter, a custom code
# adapter (a copy of code/github with a stub deliver.py and an ops.py that records provider effects),
# and a pty so bin/install-adapter sees an operator at the terminal.
mkdir -p "$T/fakebin"
cat > "$T/fakebin/gh" <<'EOF'
#!/usr/bin/env python3
import json, sys
path = sys.argv[-1]
if "/events" in path:
    page = [{"id": 9, "event": "labeled", "label": {"name": "factory:deliver"}, "actor": {"login": "octocat"},
             "created_at": "2026-11-19T00:00:00Z"}]
else:
    page = [{"number": 1, "title": "github item", "html_url": "u", "labels": [{"name": "factory:deliver"}]}]
print(json.dumps([page]))
EOF
chmod +x "$T/fakebin/gh"
stub_adapter() {  # $1 dir, $2 marker that makes the tree digest unique
  rm -rf "$1"; cp -r "$REPO/skills/factory-code-github" "$1"; rm -rf "$1/sources/__pycache__"
  printf 'import json, sys\nprint(json.dumps({"items": [{"key": "stub-%s", "title": "stub item"}]}))\n' "$2" > "$1/sources/deliver.py"
  printf 'import json, os, sys\nwith open(os.environ["STUB_TRACE"], "a") as f:\n    f.write(json.dumps({"op": sys.argv[1], "args": sys.argv[2:]}) + "\\n")\n' > "$1/ops.py"
  printf '\nStub provider: `code.issue_comment` is `python3 <installed adapter>/ops.py issue_comment <n> <markdown>` (%s).\n' "$2" >> "$1/SKILL.md"
}
new_ws() {  # $1 dir, $2 name, $3 adapter dir
  mkdir -p "$1/.compozy/factory"
  sed "s#^provider = \"github\"\$#provider = \"github\"\nadapter = \"$3\"#" "$REPO/factory.example.toml" > "$1/.compozy/factory/factory.toml"
  printf 'app = "acme-api-prod"\naccount_id = 1234567\n' > "$1/.compozy/newrelic.toml"
  cz workspace add "$1" --name "$2" -o json | json 'print("workspace", d["name"], d["id"])'
}
tty_install() {  # bin/install-adapter with a pseudo-terminal on stdin answering y
  iso python3 - "$REPO/bin/install-adapter" "$@" <<'EOF'
import os, pty, subprocess, sys
master, slave = pty.openpty()
proc = subprocess.Popen(sys.argv[1:], stdin=slave)
os.close(slave); os.write(master, b"y\n"); sys.exit(proc.wait())
EOF
}
poll() {  # run the installed provider-neutral deliver exactly as the daemon does: cwd = workspace root, JSON stdin
  (cd "$1" && echo '{"limits": {"max_items": 20}}' | iso env PATH="$T/fakebin:$PATH" "$HOME_DIR/agents/factory-dispatcher/sources/deliver")
}

step "IT-008 two workspaces, one approved deliver source, per-project adapter code"
WSB=$T/wsb
stub_adapter "$T/adapter-b" b
new_ws "$WSB" wsb "$T/adapter-b"
approved_before=$(cz agent heartbeat sources factory-dispatcher --workspace scratch -o json | json 'print(next(s["sha256"] for s in d["scripts"] if s["name"] == "deliver"))')
tty_install code "$T/adapter-b" --workspace wsb
python3 -c "import json; b = json.load(open('$HOME_DIR/factory/bindings.json'))['bindings']; print('bindings:', [(x['role'], x['provider'], x['skill']) for x in b])"
poll "$SCRATCH" | tee "$T/poll-a.json" | json 'assert [i["key"] for i in d["items"]] == ["issue-1@9"], d; print("scratch (bundled code/github) items:", [i["key"] for i in d["items"]])'
poll "$WSB" | json 'assert [i["key"] for i in d["items"]] == ["stub-b"], d; print("wsb (custom adapter) items:", [i["key"] for i in d["items"]])'
for ws in scratch wsb; do
  cz agent heartbeat sources factory-dispatcher --workspace "$ws" -o json | json '
s = next(s for s in d["scripts"] if s["name"] == "deliver")
assert s["approved"] and s["sha256"] == "'"$approved_before"'", s
print("'"$ws"': deliver approved with the same sha256 (no new approval)", s["sha256"][:19])'
done
echo "# one byte" >> "$T/adapter-b/sources/deliver.py"
if poll "$WSB" 2> "$T/poll-b.err"; then echo "changed custom adapter still ran"; exit 1; fi
cat "$T/poll-b.err"; grep -q '^source_failed: custom adapter code/github changed .*run bin/install-adapter' "$T/poll-b.err"
poll "$SCRATCH" | json 'assert d["items"]; print("scratch still works:", [i["key"] for i in d["items"]])'
tty_install code "$T/adapter-b" --workspace wsb
poll "$WSB" | json 'assert [i["key"] for i in d["items"]] == ["stub-b"], d; print("wsb restored after re-install:", [i["key"] for i in d["items"]])'

step "IT-008 (daemon path) the daemon runs both approved sources from its temporary copy (dry-run check, heartbeats on)"
# The daemon copies an approved source to a temp dir and runs it with a scrubbed environment (HOME and PATH,
# no COMPOZY_HOME), so the wrapper must find $COMPOZY_HOME as ~/.compozy. This daemon restart gets a HOME
# whose .compozy is the isolated home and the fake gh first on PATH, and the installed auditor's active hours
# (12:00-14:00) widen to the whole day so its gate reaches the source; then both go back.
mkdir -p "$T/operator"; ln -s "$HOME_DIR" "$T/operator/.compozy"
HB=$HOME_DIR/agents/factory-auditor/HEARTBEAT.md
cp "$HB" "$T/auditor-heartbeat.md"
sed -i 's/start: "12:00"/start: "00:00"/; s/end: "14:00"/end: "23:59"/' "$HB"
sed -i 's/^enabled = false$/enabled = true/' "$HOME_DIR/config.toml"
cz daemon stop >/dev/null
env -i HOME="$T/operator" PATH="$T/fakebin:$PATH" USER="${USER:-}" COMPOZY_HOME="$HOME_DIR" compozy daemon start >/dev/null
for agent in factory-dispatcher factory-auditor; do
  cz agent instance check "$agent" --workspace scratch --dry-run -o json | json '
src = d.get("sources") or []
print("'"$agent"' sources:", [(s["id"], s["result"], s["items"]) for s in src])
assert src and all(s["result"] != "failing" for s in src), src'
done
cp "$T/auditor-heartbeat.md" "$HB"
sed -i 's/^enabled = true$/enabled = false/' "$HOME_DIR/config.toml"
cz daemon stop >/dev/null; cz daemon start >/dev/null

step "IT-009 two workspaces, two digest-qualified user skills for one custom adapter"
WSC=$T/wsc
stub_adapter "$T/adapter-c" c
new_ws "$WSC" wsc "$T/adapter-c"
tty_install code "$T/adapter-c" --workspace wsc
for ws in wsb wsc; do
  skill=$(python3 -c "import json; print(next(b['skill'] for b in json.load(open('$HOME_DIR/factory/bindings.json'))['bindings'] if b['root'].endswith('/$ws')))")
  for _ in $(seq 1 30); do  # the daemon discovers new user skills asynchronously
    cz skill info "$skill" --workspace "$ws" -o json 2>/dev/null | grep -q '"source": "user"' && break; sleep 1
  done
  line=$(iso python3 "$HOME_DIR/factory/tools/factory_guard.py" adapter code github "$ws") || { echo "$ws: $line"; exit 1; }
  echo "$ws: $line"
  cz skill info "$skill" --workspace "$ws" -o json | json '
assert d["source"] == "user" and d["path"] == "'"$HOME_DIR/skills/$skill"'", d
print("daemon resolves", d["name"], "from", d["source"], d["path"])'
  echo "$skill" >> "$T/skills"
done
[ "$(sort -u "$T/skills" | wc -l)" = 2 ] || { echo "expected two distinct skills"; exit 1; }
installed_c=$(python3 -c "import json; print(next(b['installed'] for b in json.load(open('$HOME_DIR/factory/bindings.json'))['bindings'] if b['root'].endswith('/wsc')))")
(cd "$WSC" && iso env STUB_TRACE="$T/trace.jsonl" python3 "$installed_c/ops.py" issue_comment 7 "hello")
json 'print("provider effect recorded:", d)' < "$T/trace.jsonl"

step "factory-roles IT-001 bin/migrate-config on a copy of an old factory.toml, then bin/attach --dry-run"
# MIGRATE_SOURCE=<path> runs it on a copy of the operator's file (default: the old-grammar fixture). git runs
# against a local bare origin with an isolated git config; gh is a fake; the policy home is under $T.
SRC=${MIGRATE_SOURCE:-$REPO/tests/fixtures/old-factory.toml}
MIG=$T/migrate
mkdir -p "$MIG/bin" "$MIG/cfg"
printf '#!/bin/sh\ncase "$1 $2" in "pr list") echo "[]" ;; *) echo https://github.com/acme/acme-api/pull/1 ;; esac\n' \
  > "$MIG/bin/gh"; chmod +x "$MIG/bin/gh"
printf '[user]\n\tname = IT\n\temail = it@example.invalid\n' > "$MIG/gitconfig"
gitm() { env GIT_CONFIG_GLOBAL="$MIG/gitconfig" GIT_CONFIG_NOSYSTEM=1 git "$@"; }
BASE=$(python3 -c "import sys, tomllib; print(tomllib.load(open(sys.argv[1], 'rb'))['repo']['base'])" "$SRC")
gitm init --quiet --bare "$MIG/origin.git"
gitm init --quiet "$MIG/project"
mkdir -p "$MIG/project/.compozy/factory"; cp "$SRC" "$MIG/project/.compozy/factory/factory.toml"
(cd "$MIG/project" && gitm add . && gitm commit --quiet -m init && gitm remote add origin "$MIG/origin.git" &&
  gitm push --quiet origin "HEAD:$BASE")
env GIT_CONFIG_GLOBAL="$MIG/gitconfig" GIT_CONFIG_NOSYSTEM=1 PATH="$MIG/bin:$PATH" \
  GITHUB_APP_POLICY_FILE="$MIG/cfg/policy.toml" python3 - "$REPO/bin/migrate-config" "$MIG/project" <<'EOF'
import os, pty, subprocess, sys
master, slave = pty.openpty()
proc = subprocess.Popen(sys.argv[1:], stdin=slave)
os.close(slave); os.write(master, b"octocat\n"); sys.exit(proc.wait())
EOF
mkdir -p "$MIG/migrated/.compozy/factory"
for f in .compozy/factory/factory.toml .compozy/newrelic.toml; do
  gitm -C "$MIG/project" show "origin/factory/migrate-roles:$f" > "$MIG/migrated/$f"
done
python3 -c "import sys; sys.path.insert(0, sys.argv[1]); import factorylib as fl; from pathlib import Path
_, c = fl.load_factory_toml(Path(sys.argv[2]))
print('migrated factory.toml validates:', {r: c['roles'][r]['provider'] for r in fl.ROLES})" "$REPO/bin" "$MIG/migrated"
cz workspace add "$MIG/migrated" --name migrated -o json | json 'print("workspace", d["name"], d["id"])'
iso "$REPO/bin/attach" migrated --dry-run --allow-unsupported-adapter | tee "$T/dry.out"
[ "$(grep -c '^would attach factory-' "$T/dry.out")" = 6 ] || { echo "attach --dry-run did not accept the migrated file"; exit 1; }
cz agent instance list --workspace migrated -o json | json 'assert not d["instances"], d; print("dry-run created no instance")'

step "factory-roles UT-029 on the real daemon: bin/reload runs the reviewer guard and gives the new main session its boundary turn"
iso "$REPO/bin/reload" scratch factory-scribe | tee "$T/reload.out"
grep -q '^reviewer factory-reviewer origin global layer user' "$T/reload.out"
grep -q '^boundary factory-scribe ok$' "$T/reload.out"

step "factory-roles IT-006 activation runbook (tests/it/activation.sh: its own isolated daemon)"
KEEP="${KEEP:-}" "$REPO/tests/it/activation.sh"

step "IT-001..IT-004, IT-007..IT-009 (factory-user-agents/factory-roles), factory-roles IT-001 and IT-006 passed"
