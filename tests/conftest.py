"""Shared fixtures: a disposable copy of the repo, a temporary COMPOZY_HOME and a fake `compozy` CLI.

The fake replaces CompozyOS at the subprocess boundary: it is an executable named `compozy` first
on PATH that logs every call and answers from a JSON state file.
"""
import json
import os
import pty
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

sys.dont_write_bytecode = True  # adapter trees refuse compiled Python (F-3); importing their modules must not add any
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))
NEWRELIC_TOML = 'app = "acme-api-prod"\naccount_id = 1234567\n'
ACP = "env -u ARGV0 CLAUDE_CONFIG_DIR=/x mise exec node@lts -- npx -y @agentclientprotocol/claude-agent-acp@latest"
PROVIDERS = {"claude": "authenticated", "claude-mm1": "authenticated", "claude-mm2": "authenticated",
             "claude-mm4": "authenticated", "claude-reviewer": "unknown", "claude-reviewer-fallback": "unknown"}
PROVIDER_CONFIG = {**{name: ACP for name in PROVIDERS},
                   "claude-reviewer": "{sandbox} --provider claude --login-dir /x --worktree-root /w",
                   "claude-reviewer-fallback": "{sandbox} --provider claude --login-dir /y --worktree-root /w"}
ROUTING = """[work]
primary = { provider = "claude-mm4", model = "sonnet" }
fallback = [{ provider = "claude-mm2", model = "sonnet" }, { provider = "claude-mm1", model = "sonnet" }]

[auditor]
primary = { provider = "claude", model = "sonnet" }
fallback = [{ provider = "claude-mm1", model = "sonnet" }]

[reviewer]
primary = { provider = "claude-reviewer", model = "opus" }
fallback = [{ provider = "claude-reviewer-fallback", model = "opus" }]
"""
NO_COMPOZY = Path(tempfile.mkdtemp(prefix="no-compozy-"))
(NO_COMPOZY / "compozy").write_text("#!/bin/sh\necho 'no compozy in unit tests' >&2\nexit 1\n")
(NO_COMPOZY / "compozy").chmod(0o755)

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
state_path, log_path = os.environ["FAKE_COMPOZY_STATE"], os.environ["FAKE_COMPOZY_LOG"]
args = [a for a in sys.argv[1:] if a not in ("-o", "json")]
with open(log_path, "a") as log:
    log.write(json.dumps(args) + "\n")
state = json.load(open(state_path))
def opt(name):
    return args[args.index(name) + 1] if name in args else None
def out(value):
    json.dump(value, sys.stdout)
    json.dump(state, open(state_path, "w"))
    sys.exit(0)
cmd = args[:3]
fail = state.get("fail", {})  # "<first two args>": count of calls that fail (bin/setup rollback tests)
if fail.get(" ".join(args[:2]), 0) > 0:
    fail[" ".join(args[:2])] -= 1
    json.dump(state, open(state_path, "w"))
    for warning in state.get("warnings", []):  # the CLI's config deprecation warnings come first on stderr
        print(warning, file=sys.stderr)
    print(json.dumps({"error": "fake: injected failure " + " ".join(args[:2])}), file=sys.stderr)
    sys.exit(1)
if args[:2] == ["workspace", "info"]:
    out({"workspace": {"root_dir": state["root"]}})
if cmd == ["agent", "instance", "list"]:
    active = sum(i.get("state") == "active" for i in state["instances"])  # daemon-wide: + other workspaces
    out({"instances": state["instances"], "active_count": active + state.get("active_elsewhere", 0),
         "max_active_instances": state.get("max_active_instances", 16)})
if cmd == ["agent", "instance", "create"]:
    inst = {"id": f"ainst-{args[3]}", "agent_name": args[3], "state": "active",
            "main_session_id": f"sess-{args[3]}", "provider": "claude-mm4"}
    state["instances"].append(inst)
    out({"instance": inst})
if cmd == ["agent", "instance", "pause"]:
    for i in state["instances"]:
        if args[3] in (i["id"], i["agent_name"]):
            i["state"] = "paused"
    out({})
if cmd == ["agent", "instance", "archive"]:
    before = len(state["instances"])
    state["instances"] = [i for i in state["instances"] if i["agent_name"] != args[3]]
    if len(state["instances"]) == before:
        print('{"error":"agent_instance_not_found"}', file=sys.stderr)
        sys.exit(1)
    out({})
if args[:2] == ["session", "prompt"]:  # the boundary turn of bin/attach and bin/reload
    if ("--message-id" in args) != ("--idempotency-key" in args):  # like the real CLI
        print("error: cli: --message-id and --idempotency-key must be provided together", file=sys.stderr)
        sys.exit(1)
    if args[2] in state.get("prompt_fail", []):
        print('{"error":"session_not_found"}', file=sys.stderr)
        sys.exit(1)
    out({"message_id": "msg-" + args[2]})
if cmd == ["agent", "heartbeat", "sources"]:
    out({"scripts": state["scripts"].get(args[3], [])})
if cmd == ["agent", "info", "factory-reviewer"]:
    info = state["agent_info"]
    if info is None:  # like the daemon: the installed user-level file, digest from its content
        import hashlib
        path = os.path.join(os.environ["COMPOZY_HOME"], "agents", "factory-reviewer", "AGENT.md")
        if not os.path.isfile(path):
            print('{"error":"agent_not_found"}', file=sys.stderr)
            sys.exit(1)
        info = {"origin": "global", "layer": "user",
                "definition_digest": hashlib.sha256(open(path, "rb").read()).hexdigest()}
    if isinstance(info, list):  # a sequence: each call reads the next answer, the last one repeats
        info = info.pop(0) if len(info) > 1 else info[0]
    if "error" in info:  # the daemon did not answer
        json.dump(state, open(state_path, "w"))
        print(json.dumps(info), file=sys.stderr)
        sys.exit(1)
    out(info)
if args[:2] == ["provider", "list"]:
    out({"providers": [{"name": name, "enabled": True, "auth_status": {"state": st}}
                       for name, st in state["providers"].items()]})
if args[:3] == ["config", "get", "providers"]:  # the reviewer overlays launch the extension's sandbox wrapper
    sandbox = os.path.join(os.environ["COMPOZY_HOME"], "extensions", "github-app-review", "bin", "reviewer-sandbox")
    out({"path": "providers", "value": {name: {"command": cmd.replace("{sandbox}", sandbox)}
                                        for name, cmd in state["provider_config"].items()}})
if args[:2] == ["skill", "info"]:
    if state.get("skill_info_error"):  # e.g. the CLI's refusal inside a managed session
        print(state["skill_info_error"], file=sys.stderr)
        sys.exit(1)
    name = args[2]
    out(state["skills"].get(name) or {"name": name, "source": "user",
                                      "path": os.path.join(os.environ["COMPOZY_HOME"], "skills", name)})
if args[:2] == ["extension", "list"]:
    out(state.get("extensions", []))
if args[:2] == ["extension", "exec"] and not state["policy"].get("enabled", True):
    print('{"error":"extension_disabled"}', file=sys.stderr)
    sys.exit(1)
if args[:2] == ["extension", "exec"] and opt("--cmd") == "policy":  # github-app-review's read-only status
    import hashlib
    working = os.environ["GITHUB_APP_POLICY_FILE"]
    digest, active = state["policy"]["running"], {"digest": None, "path": None, "state": "unset", "error": None}
    if digest:
        path = os.path.join(os.path.dirname(working), "policies", digest + ".toml")
        ok = os.path.isfile(path) and hashlib.sha256(open(path, "rb").read()).hexdigest() == digest
        active = {"digest": digest, "path": path if ok else None, "state": "approved" if ok else "unapproved",
                  "error": None if ok else "policy_unapproved"}
    wd = hashlib.sha256(open(working, "rb").read()).hexdigest() if os.path.isfile(working) else None
    out({"result": {"structured": {"working": {"path": working, "digest": wd, "error": None}, "active": active}}})
if args[:3] == ["extension", "secrets", "set"]:  # the running process sees a new secret only after a restart
    state["policy"]["secret"] = sys.stdin.read()
    for name, count in state.get("fail_after_set", {}).items():  # failures that start once the secret changed
        fail[name] = fail.get(name, 0) + count
    state["fail"], state["fail_after_set"] = fail, {}
    out({})
if args[:3] == ["extension", "secrets", "unset"]:
    state["policy"]["secret"] = None
    out({})
if args[:2] == ["extension", "disable"]:
    state["policy"]["enabled"] = False
    out({})
if args[:2] == ["extension", "enable"]:
    state["policy"]["enabled"] = True
    if not state["policy"].get("stuck"):  # stuck: the restarted process keeps reporting the old digest
        state["policy"]["running"] = state["policy"]["secret"]
    out({})
if args[:3] == ["tool", "invoke", "ext__github_app_review__pr_context"]:  # served only by the running version
    repo, digest = json.loads(opt("--input"))["repo"], state["policy"]["running"]
    path = os.path.join(os.path.dirname(os.environ["GITHUB_APP_POLICY_FILE"]), "policies", f"{digest}.toml")
    if digest and os.path.isfile(path) and f'[repos."{repo}"]' in open(path).read():
        out({"status": "completed", "result": {"structured": {"state": "merged"}}})
    print(json.dumps({"error": "repo_not_allowed" if digest else "policy_unapproved"}), file=sys.stderr)
    sys.exit(1)
if cmd == ["agent", "memory", "inspect"]:
    out({"content": state["memory"].get(args[3], ""), "digest": "digest-" + args[3]})
if cmd == ["agent", "memory", "write"]:
    assert opt("--expected-digest") == "digest-" + args[3]
    state["memory"][args[3]] = sys.stdin.read()
    out({})
print(json.dumps({"error": "fake: unhandled " + " ".join(args)}), file=sys.stderr)
sys.exit(2)
'''


@pytest.fixture
def repo(tmp_path):
    """A copy of the repo so tests can change agent files without touching the checkout."""
    dest = tmp_path / "repo"
    for part in ("agents", "tools", "bin", "skills", "contracts"):
        shutil.copytree(REPO / part, dest / part, ignore=shutil.ignore_patterns("__pycache__"))
    return dest


@pytest.fixture
def home(tmp_path):
    path = tmp_path / "compozy-home"
    path.mkdir()
    return path


class FakeCompozy:
    def __init__(self, tmp_path):
        self.dir = tmp_path / "fakebin"
        self.dir.mkdir()
        exe = self.dir / "compozy"
        exe.write_text(FAKE)
        exe.chmod(0o755)
        self.state_path, self.log_path = tmp_path / "state.json", tmp_path / "calls.jsonl"
        self.log_path.write_text("")
        self.state = {"root": "", "instances": [], "scripts": {}, "memory": {}, "skills": {},
                      "agent_info": None, "providers": dict(PROVIDERS), "provider_config": dict(PROVIDER_CONFIG),
                      "policy": {"secret": None, "running": None}}
        self.save()

    def save(self):
        self.state_path.write_text(json.dumps(self.state))

    def load(self):
        self.state = json.loads(self.state_path.read_text())
        return self.state

    @property
    def calls(self):
        return [json.loads(line) for line in self.log_path.read_text().splitlines()]

    def env(self):
        """Fake compozy first on PATH and an XDG_CONFIG_HOME holding today's routing (S8)."""
        routing = self.dir.parent / "xdg" / "compozy-factory" / "routing.toml"
        if not routing.parent.is_dir():
            routing.parent.mkdir(parents=True)
            routing.write_text(ROUTING)
        return {"PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}", "FACTORY_READBACK_TIMEOUT": "2",
                "FAKE_COMPOZY_STATE": str(self.state_path), "FAKE_COMPOZY_LOG": str(self.log_path),
                "XDG_CONFIG_HOME": str(routing.parent.parent)}


@pytest.fixture
def fake(tmp_path):
    return FakeCompozy(tmp_path)


def run(script, *args, env=None, repo_root=REPO, stdin=None):
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOZY_")}
    full_env["PATH"] = f"{NO_COMPOZY}{os.pathsep}{full_env['PATH']}"  # never the real CLI unless faked
    full_env.update(env or {})
    return subprocess.run([str(repo_root / "bin" / script), *args], capture_output=True, text=True,
                          env=full_env, input=stdin)


def run_tty(script, *args, env, answer="y\n", repo_root=REPO):
    """Run a bin/ script with a pseudo-terminal as stdin (an operator at the keyboard)."""
    full = {k: v for k, v in os.environ.items() if not k.startswith("COMPOZY_")}
    full.update(env)
    master, slave = pty.openpty()
    proc = subprocess.Popen([str(repo_root / "bin" / script), *args], stdin=slave, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=full)
    os.close(slave)
    os.write(master, answer.encode())
    out, err = proc.communicate(timeout=60)
    os.close(master)
    return subprocess.CompletedProcess(proc.args, proc.returncode, out, err)


@pytest.fixture
def workspace(tmp_path):
    """A project root with a valid factory.toml copied from factory.example.toml and its .compozy/newrelic.toml."""
    root = tmp_path / "acme-api"
    (root / ".compozy" / "factory").mkdir(parents=True)
    shutil.copy(REPO / "factory.example.toml", root / ".compozy" / "factory" / "factory.toml")
    (root / ".compozy" / "newrelic.toml").write_text(NEWRELIC_TOML)
    return root


@pytest.fixture
def home_env(fake, home):
    """Env for scripts writing to `home` with the fake compozy first on PATH."""
    return {**fake.env(), "COMPOZY_HOME": str(home)}


@pytest.fixture
def installed(fake, home):
    """Env for scripts after a real bin/install into `home` (agents, tools, adapters, reviewer digest read back):
    fake compozy first on PATH and COMPOZY_HOME set."""
    env = {**fake.env(), "COMPOZY_HOME": str(home)}
    proc = run("install", env=env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    return env
