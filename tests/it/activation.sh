#!/usr/bin/env bash
# factory-roles IT-006: the activation runbook (docs/activation-runbook.md, R0-R5) against a real, isolated
# CompozyOS daemon, a monorepo-shaped fixture project and a fake GitHub. Nothing reaches the operator's daemon,
# home, extensions or policy: every CLI call runs under `env -i` with its own COMPOZY_HOME, port and socket.
#
# R0 is the pre-roles world: the RELEASED extension builds (github-app-review v0.2.2, whose command catalog
# is `doctor` only, and newrelic v0.3.1 with NEW_RELIC_APP), downloaded from their GitHub releases and
# checked against the published sha256; the factory agents installed from the pre-S1 commit 0ea0254; the
# old-grammar factory.toml; one open delivery PR with its R0 gate verdict. The walk then runs R1-R4 with a
# probe after each (R3 recreates the team with bin/reload --pause and asserts no check-in until the end of R4),
# injects an R4 failure and rolls back to the R0 set (reinstalls pinned with --expected-digest), asserting it
# before bin/reload.
#
# Test hooks (not product behavior): each extension manifest gets a `[subprocess] env` with
# GITHUB_APP_API_URL (the fake GitHub, served by compozy-github-app's own test/fake-github.ts at v0.3.0)
# and GITHUB_APP_POLICY_FILE under the temp dir; newrelic gets an unreachable HTTPS proxy so no query
# leaves the machine. A `compozy` shim logs every CLI call so the walk can assert that no
# `extension exec ... policy-stage` happens before R3.
# Usage: tests/it/activation.sh     (KEEP=1 keeps the temp dir; needs gh (read-only release downloads), bun, git)
set -euo pipefail

REPO=$(cd "$(dirname "$0")/../.." && pwd)
R0_COMMIT=0ea0254  # last factory commit before the role grammar (factory-roles S1)
GHAR_OLD=v0.2.2 GHAR_NEW=v0.3.0 NR_OLD=v0.3.1 NR_NEW=v0.4.0
T=$(mktemp -d /tmp/cf-it6.XXXXXX)
HOME_DIR=$T/home WS=$T/acme-api
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }
PORT=$(free_port) FAKE_PORT=$(free_port)
FAKE=http://127.0.0.1:$FAKE_PORT
mkdir -p "$HOME_DIR" "$T/shim" "$T/bin" "$T/rel" "$T/policy"
REAL_CZ=$(command -v compozy)
printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> %s/compozy.log\nexec %s "$@"\n' "$T" "$REAL_CZ" > "$T/shim/compozy"
chmod +x "$T/shim/compozy"
iso() { env -i HOME="$HOME" PATH="$T/shim:$PATH" USER="${USER:-}" COMPOZY_HOME="$HOME_DIR" "$@"; }
cz() { iso compozy "$@"; }
json() { python3 -c "import json,sys; d=json.load(sys.stdin); $1"; }
step() { printf '\n## %s\n' "$*"; }
fail() { echo "FAILED: $*"; exit 1; }
ok() { local msg=$1; shift; "$@" >/dev/null || fail "$msg"; echo "$msg"; }  # ok <message> <command...>
gitc() { env GIT_CONFIG_GLOBAL="$T/gitconfig" GIT_CONFIG_NOSYSTEM=1 git "$@"; }
printf '[user]\n\tname = IT\n\temail = it@example.invalid\n[init]\n\tdefaultBranch = main\n' > "$T/gitconfig"

cleanup() {
  [ -n "${PUB:-}" ] && env -i HOME="$HOME" PATH="$PATH" COMPOZY_HOME="$PUB" compozy daemon stop >/dev/null 2>&1 || true
  [ -n "${FAKE_PID:-}" ] && kill "$FAKE_PID" 2>/dev/null || true
  cz daemon stop >/dev/null 2>&1 || true
  [ "${KEEP:-}" = 1 ] && echo "kept $T" || rm -rf "$T"
}
trap cleanup EXIT

step "IT-006 released extension builds (GitHub releases, sha256 checked)"
for spec in "compozy-github-app $GHAR_OLD github-app-review" "compozy-github-app $GHAR_NEW github-app-review" \
            "compozy-newrelic $NR_OLD newrelic" "compozy-newrelic $NR_NEW newrelic"; do
  read -r gh_repo tag name <<<"$spec"
  dir=$T/rel/$name-$tag
  mkdir -p "$dir/pkg"
  gh release download "$tag" -R "jillesmc/$gh_repo" -D "$dir" -p "$name-$tag.tar.gz*"
  (cd "$dir" && sha256sum --quiet -c "$name-$tag.tar.gz.sha256")
  tar -xzf "$dir/$name-$tag.tar.gz" -C "$dir/pkg"
  if [ "$name" = github-app-review ]; then
    hook="GITHUB_APP_API_URL = \"$FAKE\", GITHUB_APP_POLICY_FILE = \"$T/policy/policy.toml\""
  else
    hook="NODE_USE_ENV_PROXY = \"1\", HTTPS_PROXY = \"http://127.0.0.1:9\""
  fi
  python3 - "$dir/pkg/extension.toml" "$hook" <<'EOF'
import sys
path, hook = sys.argv[1:]
text = open(path).read()
assert text.count("[subprocess]\n") == 1 and "\n  env = " not in text, "unexpected manifest shape"
open(path, "w").write(text.replace("[subprocess]\n", "[subprocess]\n  env = { " + hook + " }\n"))
EOF
  echo "$name $tag $(grep -m1 '^  version' "$dir/pkg/extension.toml")"
done
mkdir -p "$T/fakegh/test"
for f in fake-github.ts fixtures.ts; do
  gh api -H "Accept: application/vnd.github.raw" "repos/jillesmc/compozy-github-app/contents/test/$f?ref=$GHAR_NEW" > "$T/fakegh/test/$f"
done
(cd "$T/fakegh" && exec bun test/fake-github.ts --port "$FAKE_PORT") > "$T/fakegh.log" 2>&1 &
FAKE_PID=$!

cat > "$HOME_DIR/config.toml" <<EOF
[http]
host = "127.0.0.1"
port = $PORT

[daemon]
socket = "$T/daemon.sock"

# Heartbeats on, so "agent instance check --dry-run" polls sources (the R4 newrelic smoke). Instances are
# paused for R1-R4; while active, their sources reach only the fake gh and fake GitHub, and check-in turns
# go to tests/it/fake-acp (no model, no credential).
[agents.heartbeat]
enabled = true

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
trusted_sources = ["extension:github-app-review", "extension:newrelic"]
EOF

step "P-11 published-source pin (a second, short-lived isolated daemon)"
# How a published install (the live factory's) is recorded and pinned. The daemon reads GitHub through
# `gh auth token`, so this daemon has the real gh on PATH; the walk's daemon keeps only the fake one. Nothing is
# bound and no tool is called; the daemon is stopped before the walk starts.
PUB=$T/pub-home PUB_PORT=$(free_port)
mkdir -p "$PUB"
printf '[http]\nhost = "127.0.0.1"\nport = %s\n\n[daemon]\nsocket = "%s/pub.sock"\n' "$PUB_PORT" "$T" > "$PUB/config.toml"
pcz() { env -i HOME="$HOME" PATH="$PATH" USER="${USER:-}" COMPOZY_HOME="$PUB" compozy "$@"; }
pcz daemon start >/dev/null
out=$(pcz extension install "github:jillesmc/compozy-github-app@$GHAR_OLD" --allow-unverified --yes 2>&1) || { echo "$out"; fail "published install"; }
GHAR_SRC="github:jillesmc/compozy-github-app@$GHAR_OLD"
RELEASE_SHA=$(cut -d' ' -f1 "$T/rel/github-app-review-$GHAR_OLD/github-app-review-$GHAR_OLD.tar.gz.sha256")
pcz extension list -o json | python3 -c "
import importlib.machinery, importlib.util, json, sys
loader = importlib.machinery.SourceFileLoader('r0', '$REPO/bin/runbook-r0')
r0 = importlib.util.module_from_spec(importlib.util.spec_from_loader('r0', loader)); loader.exec_module(r0)
e = next(e for e in json.load(sys.stdin) if e['name'] == 'github-app-review'); p = e['provenance']
assert (p['installed_from'], p['archive_digest_sha256']) == ('github', '$RELEASE_SHA'), p
assert p['checksum_sha256'] != p['archive_digest_sha256']
ext = {'version': e['version'], **{k: p.get(k) for k in ('installed_from', 'slug', 'source_url', 'checksum_sha256', 'archive_digest_sha256')}}
command, problem = r0.rollback('github-app-review', ext)
assert problem is None, problem
print(command)" > "$T/published-rollback.cmd"
cat "$T/published-rollback.cmd"
ok "published install: archive_digest_sha256 = the release's .tar.gz.sha256; rollback command pins it" \
  grep -qx "compozy extension install $GHAR_SRC --expected-digest $RELEASE_SHA --allow-unverified" "$T/published-rollback.cmd"
read -ra cmd < "$T/published-rollback.cmd"
pinned=$(pcz "${cmd[@]:1}" --yes 2>&1) || { echo "$pinned"; fail "pinned published reinstall"; }
ok "pinned published reinstall: $(grep -o '✓ install github-app-review' <<<"$pinned")" grep -q '✓ install github-app-review' <<<"$pinned"
wrong=$(pcz extension install "$GHAR_SRC" --expected-digest "$(printf '0%.0s' $(seq 64))" --allow-unverified --yes 2>&1) &&
  fail "a wrong archive pin was accepted"
ok "wrong archive pin refused: source changed since it was listed" grep -q 'source changed since it was listed' <<<"$wrong"
pcz daemon stop >/dev/null

step "isolated daemon (COMPOZY_HOME=$HOME_DIR, port $PORT)"
iso env PATH="$T/bin:$T/shim:$PATH" compozy daemon start >/dev/null  # sources see the fake gh, never the real one
cz workspace list -o json | json 'assert d == [], d; print("isolation: workspace list is empty (fresh home)")'

# One fake `gh` for the whole walk: bin/runbook-r0 lists delivery PRs, bin/migrate-config opens its PR.
cat > "$T/bin/gh" <<'EOF'
#!/usr/bin/env python3
import json, sys
a = sys.argv[1:]
state = a[a.index("--state") + 1] if "--state" in a else ""
if a[:2] == ["pr", "list"] and "--head" in a:
    print("[]")
elif a[:2] == ["pr", "list"] and state == "open":
    print(json.dumps([{"number": 231, "headRefOid": "9f2c1e0a7b3d4c5e6f708192a3b4c5d6e7f80912", "title": "ACME-7: export"}]))
elif a[:2] == ["pr", "list"] and state == "merged":
    print(json.dumps([{"number": 230, "mergeCommit": {"oid": "5" * 40}, "title": "ACME-6: totals"}]))
elif a[:2] == ["pr", "create"]:
    print("https://github.com/acme/acme-api/pull/240")
else:
    sys.exit("fake gh: unexpected " + " ".join(a))
EOF
chmod +x "$T/bin/gh"
op() { iso env PATH="$T/bin:$T/shim:$PATH" GIT_CONFIG_GLOBAL="$T/gitconfig" GIT_CONFIG_NOSYSTEM=1 "$@"; }  # the operator's shell
ws_cz() { (cd "$WS" && cz "$@"); }
log_mark() { wc -l < "$T/compozy.log"; }
TEAM="factory-sentinel factory-scribe factory-dispatcher factory-pr-watcher factory-gatekeeper factory-auditor"

# Fake GitHub fixture: open delivery PR #231 into develop, approved by the App on its head, checks green;
# #230 merged into develop. pr_context/merge_gate read the fake's current `pr`, so a probe selects one.
for _ in $(seq 1 50); do curl -fs "$FAKE/_fake/state" >/dev/null && break; sleep 0.2; done
HEAD_SHA=9f2c1e0a7b3d4c5e6f708192a3b4c5d6e7f80912
OPEN_PR='{"number":231,"state":"open","draft":false,"merged":false,"merged_at":null,"title":"ACME-7: export","body":"","user":{"login":"octocat"},"changed_files":1,"head":{"sha":"'$HEAD_SHA'","ref":"ACME-7-export"},"base":{"ref":"develop","repo":{"default_branch":"main"}}}'
MERGED_PR='{"number":230,"state":"closed","draft":false,"merged":true,"merged_at":"2026-11-18T12:00:00Z","title":"ACME-6: totals","body":"","user":{"login":"octocat"},"changed_files":1,"head":{"sha":"'$HEAD_SHA'","ref":"ACME-6-totals"},"base":{"ref":"develop","repo":{"default_branch":"main"}}}'
fake_state() { curl -fsS -X POST "$FAKE/_fake/state" -d "$1" >/dev/null; }
fake_state '{"repo":"acme/acme-api","installedRepos":["acme-api"],"pr":'"$OPEN_PR"',"reviews":[{"id":1,"user":{"login":"widgets-reviewer[bot]","type":"Bot"},"state":"APPROVED","commit_id":"'$HEAD_SHA'","body":"","submitted_at":"2026-11-18T13:00:00Z"}]}'
invoke() { cz tool invoke "ext__github_app_review__$1" --workspace acme --input "$2" -o json; }
gate() { fake_state '{"pr":'"$OPEN_PR"'}'; invoke merge_gate '{"repo":"acme/acme-api","number":231,"kind":"delivery"}' | json 'print(json.dumps(d["result"]["structured"], sort_keys=True))'; }
context_merged() {  # pr_context on the merged PR #230; prints the state and head ref it read
  local out rc=0
  fake_state '{"pr":'"$MERGED_PR"'}'
  out=$(invoke pr_context '{"repo":"acme/acme-api","number":230}' | json 's = d["result"]["structured"]; print(s["state"], s["head_ref"])') || rc=$?
  fake_state '{"pr":'"$OPEN_PR"'}'
  echo "$out"; return $rc
}

# A test-only check-in agent with one newrelic source (no active hours, unlike the sentinel's daily
# window) so `agent instance check --dry-run` always polls it: `{{app}}` resolved → the query is attempted
# and fails at the unreachable proxy; unresolved → source_params_invalid before any query.
mkdir -p "$HOME_DIR/agents/it-nr-probe"
printf -- '---\nname: it-nr-probe\ndisplay_name: IT newrelic probe\ndescription: test-only\nkind: always_on\nprovider: claude-mm4\nmodel: sonnet\n---\n\nTest-only.\n' \
  > "$HOME_DIR/agents/it-nr-probe/AGENT.md"
printf -- '---\nversion: 1\nenabled: true\nsummary: "IT probe"\ncontext:\n  include: []\n  sources:\n    - id: nr-errors\n      kind: newrelic/errors\n      with:\n        where: "appName = '"'"'{{app}}'"'"'"\n---\n\nProbe.\n' \
  > "$HOME_DIR/agents/it-nr-probe/HEARTBEAT.md"
nr_probe() {  # prints the probe source's error code and detail
  cz agent instance check it-nr-probe --workspace acme --dry-run -o json | json '
s = d["sources"][0]; e = s.get("error") or {}
print(s["result"], e.get("code"), e.get("detail"))'
}
expect_nr_resolved() {
  local out; out=$(nr_probe); echo "newrelic source: $out"
  case "$out" in *source_params_invalid*) echo "{{app}} not resolved"; exit 1 ;; *ECONNREFUSED*) ;; *) echo "unexpected probe result"; exit 1 ;; esac
}

step "R0 world: factory $R0_COMMIT, old extensions, old-grammar project, instances attached"
approve_printed() {  # run each approval line bin/install printed, exactly as printed (operator)
  { grep '^  compozy agent heartbeat' "$1" || true; } | while read -r line; do
    read -ra cmd <<<"$line"; (cd "$T" && iso "${cmd[@]}" >/dev/null 2>"$T/approve.err") || { cat "$T/approve.err"; fail "$line"; }
    echo "ok: $line"
  done
}
gitc clone --quiet --no-checkout "$REPO" "$T/factory"
gitc -C "$T/factory" checkout --quiet --detach "$R0_COMMIT"
iso "$T/factory/bin/install" | tee "$T/install-r0.out"
approve_printed "$T/install-r0.out"
gitc init --quiet --bare "$T/origin.git"
gitc init --quiet "$WS"
mkdir -p "$WS/.compozy/factory"
cp "$REPO/tests/fixtures/old-factory.toml" "$WS/.compozy/factory/factory.toml"
printf '# acme-api\n' > "$WS/README.md"
printf '# Review conventions\n\nFixture.\n' > "$WS/REVIEW.md"
gitc -C "$WS" add . && gitc -C "$WS" commit --quiet -m "init"
gitc -C "$WS" remote add origin "$T/origin.git"
gitc -C "$WS" push --quiet origin HEAD:main HEAD:develop
gitc -C "$WS" checkout --quiet -b develop --track origin/develop
cz workspace add "$WS" --name acme -o json | json 'print("workspace", d["name"], d["id"])'

openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$T/throwaway-app-key" 2>/dev/null
secret() { printf '%s' "$3" | cz extension secrets set "$1" --env "$2" --value-stdin >/dev/null; }
restart() { cz extension disable "$1" >/dev/null; cz extension enable "$1" >/dev/null; }
cz extension install "$T/rel/github-app-review-$GHAR_OLD/pkg" --allow-unverified --yes >/dev/null 2>&1
for kv in GITHUB_APP_ID=123456 GITHUB_APP_INSTALLATION_ID=7890123 GITHUB_APP_REPOS=acme/acme-api GITHUB_APP_BASE=develop \
          "GITHUB_APP_PROMOTION=acme/acme-api:develop->main" GITHUB_APP_PROMOTION_APPROVERS=octocat; do
  secret github-app-review "${kv%%=*}" "${kv#*=}"
done
secret github-app-review GITHUB_APP_PRIVATE_KEY "$(cat "$T/throwaway-app-key")"
cz extension install "$T/rel/newrelic-$NR_OLD/pkg" --allow-unverified --yes >/dev/null 2>&1
for kv in NEW_RELIC_ACCOUNT_ID=1234567 NEW_RELIC_API_KEY=NRAK-IT-DUMMY NEW_RELIC_APP=acme-api-prod; do
  secret newrelic "${kv%%=*}" "${kv#*=}"
done
for e in github-app-review newrelic; do restart "$e"; done
iso "$T/factory/bin/attach" acme | tee "$T/attach-r0.out"
[ "$(grep -c '^attached factory-' "$T/attach-r0.out")" = 6 ]
for _ in $(seq 1 30); do cz agent info it-nr-probe -o json >/dev/null 2>&1 && break; sleep 1; done
cz agent instance create it-nr-probe --workspace acme -o json >/dev/null
cz agent instance pause it-nr-probe --workspace acme -o json >/dev/null
expect_nr_resolved
ok "R0: pr_context on merged #230 through the old extension" test "$(context_merged)" = "merged ACME-6-totals"

step "R0 record (bin/runbook-r0)"
tree_state() { for d in "$WS" "$T/factory"; do gitc -C "$d" status --porcelain --ignored; gitc -C "$d" rev-parse HEAD; done; }
before_r0=$(tree_state)
op "$REPO/bin/runbook-r0" acme --factory "$T/factory" --out "$T/record/r0.json" | tee "$T/r0.out"
R0_GATE=$(json 'print(json.dumps(d["smoke"]["gate"]["verdict"], sort_keys=True))' < "$T/record/r0.json")
echo "R0 gate verdict #231: $R0_GATE"
[ "$R0_GATE" = "$(gate)" ]
python3 - "$T/record/r0.json" <<'EOF'
import json, sys
r = json.load(open(sys.argv[1]))
assert {n: e["version"] for n, e in r["extensions"].items()} == {"github-app-review": "0.2.2", "newrelic": "0.3.1"}, r["extensions"]
assert r["factory"]["commit"].startswith("0ea0254"), r["factory"]
assert len(r["agents"]) == 7 and all(a["definition_digest"] for a in r["agents"].values()), r["agents"]
assert r["factory_toml"]["at_base"] == r["factory_toml"]["checkout"] and not r["newrelic_toml_present"]
assert "GITHUB_APP_REPOS" in r["extensions"]["github-app-review"]["bound_env_names"]
assert r["smoke"]["last_merged_delivery_pr"][0]["number"] == 230 and r["smoke"]["gate"]["number"] == 231
print("R0 record: extensions 0.2.2/0.3.1, factory 0ea0254, 7 agent digests, factory.toml at base, smoke PRs")
EOF
cmp "$T/record/r0.json.factory.toml" "$WS/.compozy/factory/factory.toml"
# Read-only: the record left the workspace and the factory checkout untouched.
ok "R0 capture changed nothing in the workspace or the factory checkout" test "$(tree_state)" = "$before_r0"
inside=$(op "$REPO/bin/runbook-r0" acme --factory "$T/factory" --out "$WS/r0.json") && fail "record inside the workspace accepted"
ok "R0 record inside the workspace refused" grep -q "^refused: the record must live outside $WS\$" <<<"$inside"
# An incomplete capture is refused with no record: here the gate read fails (GitHub rejects the token).
fake_state '{"rejectTokens":1000}'
broken=$(op "$REPO/bin/runbook-r0" acme --factory "$T/factory" --out "$T/record/broken.json") && fail "incomplete capture recorded"
fake_state '{"rejectTokens":0}'
ok "R0 capture with a failed gate read refused, nothing written" test ! -e "$T/record/broken.json"
grep '^refused: incomplete R0 capture' <<<"$broken" | cut -c1-160
compare() { op "$REPO/bin/runbook-r0" acme --factory "$T/factory" --compare "$T/record/r0.json"; }

step "R1 pause the six instances"
for a in $TEAM; do cz agent instance pause "$a" --workspace acme >/dev/null; done
probe_paused() {
  cz agent instance list --workspace acme -o json | json '
team = "'"$TEAM"'".split()
st = {i["agent_name"]: i["state"] for i in d["instances"] if i["state"] != "archived"}
assert all(st.get(a) == "paused" for a in team), st
print("probe: six instances paused")'
}
probe_paused
compare

step "R2 stage: bin/migrate-config, migration PR merged outside the factory, bin/stage-policy"
op env GITHUB_APP_POLICY_FILE="$T/policy/policy.toml" python3 - "$REPO/bin/migrate-config" acme <<'EOF' | tee "$T/migrate.out"
import os, pty, subprocess, sys
master, slave = pty.openpty()
proc = subprocess.Popen(sys.argv[1:], stdin=slave)
os.close(slave); os.write(master, b"octocat\n"); sys.exit(proc.wait())
EOF
grep -q '^policy     staged .*policies/' "$T/migrate.out"
# GitHub's merge button, simulated in a separate clone: the workspace checkout only fast-forwards to it.
gitc clone --quiet -b develop "$T/origin.git" "$T/github"
gitc -C "$T/github" merge --quiet --no-ff -m "Merge pull request #240 from acme/factory/migrate-roles" origin/factory/migrate-roles
gitc -C "$T/github" push --quiet origin develop
MERGE_SHA=$(gitc -C "$T/github" rev-parse HEAD)
gitc -C "$WS" pull --quiet --ff-only
[ "$(gitc -C "$WS" rev-parse HEAD)" = "$MERGE_SHA" ]
for f in .compozy/factory/factory.toml .compozy/newrelic.toml; do
  gitc -C "$WS" show "origin/factory/migrate-roles:$f" | cmp - "$WS/$f"
done
echo "workspace HEAD = migration merge $MERGE_SHA; factory.toml and newrelic.toml bytes equal the PR's"
op env GITHUB_APP_POLICY_FILE="$T/policy/policy.toml" "$REPO/bin/stage-policy" | tee "$T/stage.out"
DIGEST=$(python3 -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" "$T/policy/policy.toml")
grep -q "^unchanged  policies/$DIGEST.toml" "$T/stage.out"
cmp "$T/policy/policy.toml" "$T/policy/policies/$DIGEST.toml"
probe_paused
expect_nr_resolved  # old newrelic: app still from NEW_RELIC_APP
ok "probe: old github-app-review ignores the new files; gate #231 = R0 verdict" test "$(gate)" = "$R0_GATE"
ok "probe: github-app-review catalog is doctor only" grep -q 'available commands: doctor' <<<"$(ws_cz extension exec github-app-review --cmd policy 2>&1)"
compare | tee "$T/compare-r2.out" || true
ok "probe: only factory.toml (base, checkout) and newrelic.toml moved" test "$(grep '^differs' "$T/compare-r2.out" | cut -c10- | cut -d: -f1 | paste -sd,)" = "factory.toml at base,factory.toml in checkout,newrelic.toml absent"
R3_MARK=$(log_mark)
if head -n "$R3_MARK" "$T/compozy.log" | grep -nE 'policy[-_]stage'; then echo "policy-stage called before R3"; exit 1; fi
echo "no policy-stage / policy_stage call (exec or tool invoke) before R3 ($R3_MARK CLI calls logged)"

step "R3 activate together: extensions, digest, bin/install, bin/reload"
cz extension install "$T/rel/newrelic-$NR_NEW/pkg" --replace --allow-unverified --yes >/dev/null 2>&1
cz extension install "$T/rel/github-app-review-$GHAR_NEW/pkg" --replace --allow-unverified --yes >/dev/null 2>&1
policy() { ws_cz extension exec github-app-review --cmd policy -o json | json 's = d["result"]["structured"]; print(s["active"]["state"], s["active"]["digest"], s["working"]["digest"])'; }
read -r state active working <<<"$(policy)"
echo "policy before the switch: active $state, working $working"
ok "policy: staged working copy, digest unset" test "$state $working" = "unset $DIGEST"
secret github-app-review GITHUB_APP_POLICY_DIGEST "$DIGEST"
read -r state active _ <<<"$(policy)"
echo "after secrets set, before the restart: active $state"
restart github-app-review  # unconditional, as the runbook does (the running process may keep the old env)
read -r state active _ <<<"$(policy)"
ok "policy active $DIGEST (approved)" test "$state $active" = "approved $DIGEST"
# Move the factory checkout to the code under test (this working tree, committed in the clone).
gitc -C "$T/factory" checkout --quiet --detach "$(git -C "$REPO" rev-parse HEAD)"
(cd "$REPO" && git ls-files -co --exclude-standard -z) | (cd "$REPO" && tar --null -T - -cf -) | tar -xf - -C "$T/factory"
gitc -C "$T/factory" add -A && { gitc -C "$T/factory" diff --cached --quiet || gitc -C "$T/factory" commit --quiet -m "IT: working tree"; }
NEW_COMMIT=$(gitc -C "$T/factory" rev-parse HEAD)
iso "$T/factory/bin/install" | tee "$T/install-r3.out"
grep -q '^reviewer factory-reviewer digest .* recorded' "$T/install-r3.out"
approve_printed "$T/install-r3.out"
# P-12: an active test-only instance with no active hours is due at once; its check-in record shows the scheduler
# evaluated wakes in the same window the six are probed in.
mkdir -p "$HOME_DIR/agents/it-tick"
printf -- '---\nname: it-tick\ndisplay_name: IT tick\ndescription: test-only\nkind: always_on\nprovider: claude-mm4\nmodel: sonnet\n---\n\nTest-only.\n' \
  > "$HOME_DIR/agents/it-tick/AGENT.md"
printf -- '---\nversion: 1\nenabled: true\nsummary: "IT tick"\ncontext:\n  include: []\n---\n\nNothing to do.\n' \
  > "$HOME_DIR/agents/it-tick/HEARTBEAT.md"
for _ in $(seq 1 30); do cz agent info it-tick -o json >/dev/null 2>&1 && break; sleep 1; done
cz agent instance create it-tick --workspace acme -o json >/dev/null
iso "$T/factory/bin/reload" acme --pause | tee "$T/reload-r3.out"
[ "$(grep -c '^boundary factory-.* ok$' "$T/reload-r3.out")" = 6 ]
[ "$(grep -c '^   paused factory-' "$T/reload-r3.out")" = 6 ]
probe_paused
no_check_ins() {  # the six instances bin/reload --pause created have no check-in record (checked again after R4)
  local n=0
  for _ in $(seq 1 90); do  # trust the empty lists only once the active tick instance has a check-in
    n=$(cz agent instance check-ins it-tick --workspace acme -o json | json 'print(len(d["check_ins"]))')
    [ "$n" -ge 1 ] && break; sleep 1
  done
  [ "$n" -ge 1 ] || fail "the active it-tick instance got no check-in: the probe proves nothing"
  echo "probe: active it-tick instance has $n check-in record(s) in the same window"
  for a in $TEAM; do
    cz agent instance check-ins "$a" --workspace acme -o json | json 'assert d["check_ins"] == [], ("'"$a"'", d)'
  done
  echo "probe: no check-in occurrence on the six new instances ($1)"
}
no_check_ins "after bin/reload --pause"
compare | tee "$T/compare-r3.out" || true
ok "probe: extensions, factory commit and agents moved" test "$(grep -c '^differs  \(extensions\|factory commit\|agents\)' "$T/compare-r3.out")" = 3

step "R4 smoke"
read -r state active _ <<<"$(policy)"; ok "smoke: policy $DIGEST active" test "$state $active" = "approved $DIGEST"
ok "smoke: pr_context on merged #230 ok" test "$(context_merged)" = "merged ACME-6-totals"
ok "smoke: merge_gate #231 = R0 verdict $R0_GATE" test "$(gate)" = "$R0_GATE"
expect_nr_resolved
mv "$WS/.compozy/newrelic.toml" "$T/newrelic.toml.aside"   # control: v0.4.0 takes the app only from the file
nr_probe | tee "$T/nr-control.out"; grep -q 'source_params_invalid.*{{app}}' "$T/nr-control.out"
mv "$T/newrelic.toml.aside" "$WS/.compozy/newrelic.toml"
echo "smoke: {{app}} comes from .compozy/newrelic.toml (absent → source_params_invalid; NEW_RELIC_APP ignored)"
iso python3 "$HOME_DIR/factory/tools/factory_guard.py" reviewer acme | tee "$T/guard.out"; grep -q '^ok: factory-reviewer' "$T/guard.out"
iso "$T/factory/bin/attach" acme --dry-run --allow-unsupported-adapter | tee "$T/attach-r4.out"
[ "$(grep -c '^exists factory-' "$T/attach-r4.out")" = 6 ]

step "R4 injected failure → rollback to the R0 set"
fake_state '{"rejectTokens":1000}'
if out=$(context_merged 2>&1); then echo "injected failure not detected: $out"; exit 1; fi
echo "smoke failed as injected (GitHub rejects the token): rolling back"
no_check_ins "through R4"
for e in github-app-review newrelic; do  # the rollback command bin/runbook-r0 recorded (local path: --replace, checksum pin)
  read -ra cmd <<<"$(json 'print(d["extensions"]["'"$e"'"]["rollback"]["command"])' < "$T/record/r0.json")"
  echo "rollback: ${cmd[*]}"
  out=$(cz "${cmd[@]:1}" --yes 2>&1) || { echo "$out"; fail "pinned reinstall of $e"; }
  ok "pinned reinstall: ✓ install $e" grep -q "✓ install $e" <<<"$out"
done
gitc -C "$T/factory" checkout --quiet --detach "$R0_COMMIT"
iso "$T/factory/bin/install" | tee "$T/install-rollback.out"
approve_printed "$T/install-rollback.out"
# Revert PR, merged outside the factory; the workspace checkout fast-forwards to the revert's merge.
gitc -C "$T/github" checkout --quiet -b revert-240 develop
gitc -C "$T/github" revert --no-edit -m 1 "$MERGE_SHA" >/dev/null
gitc -C "$T/github" checkout --quiet develop
gitc -C "$T/github" merge --quiet --no-ff -m "Merge pull request #241 from acme/revert-240" revert-240
gitc -C "$T/github" push --quiet origin develop
REVERT_SHA=$(gitc -C "$T/github" rev-parse HEAD)
gitc -C "$WS" pull --quiet --ff-only

echo "-- assertions before bin/reload"
ok "workspace HEAD = revert merge $REVERT_SHA" test "$(gitc -C "$WS" rev-parse HEAD)" = "$REVERT_SHA"
ok "factory.toml bytes = R0" cmp "$T/record/r0.json.factory.toml" "$WS/.compozy/factory/factory.toml"
ok ".compozy/newrelic.toml absent" test ! -e "$WS/.compozy/newrelic.toml"
ok "factory checkout at $R0_COMMIT" test "$(gitc -C "$T/factory" rev-parse HEAD)" = "$(gitc -C "$T/factory" rev-parse "$R0_COMMIT")"
for _ in $(seq 1 30); do compare > "$T/compare-rollback.out" && break; sleep 1; done  # the daemon reloads definitions asynchronously
cat "$T/compare-rollback.out"
compare >/dev/null
ok "approved policy version kept on disk ($DIGEST)" test -f "$T/policy/policies/$DIGEST.toml"
pre_reload=$(grep -c 'agent instance create' "$T/compozy.log")

iso "$T/factory/bin/reload" acme | tee "$T/reload-rollback.out"
[ "$(grep -c 'agent instance create' "$T/compozy.log")" -gt "$pre_reload" ]
fake_state '{"rejectTokens":0}'
ok "after reload: gate #231 through $GHAR_OLD = R0 verdict (removed env keys never unset: no re-entry)" test "$(gate)" = "$R0_GATE"
expect_nr_resolved
cz agent instance list --workspace acme -o json | json '
st = {i["agent_name"]: i["state"] for i in d["instances"] if i["state"] != "archived"}
assert all(st.get(a) == "active" for a in "'"$TEAM"'".split()), st
print("resumed: six instances active on the R0 set")'

step "factory-roles IT-006 passed"
