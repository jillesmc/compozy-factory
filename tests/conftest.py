"""Shared fixtures: a disposable copy of the repo, a temporary COMPOZY_HOME and a fake `compozy` CLI.

The fake replaces CompozyOS at the subprocess boundary: it is an executable named `compozy` first
on PATH that logs every call and answers from a JSON state file.
"""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "bin"))

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
if args[:2] == ["workspace", "info"]:
    out({"workspace": {"root_dir": state["root"]}})
if cmd == ["agent", "instance", "list"]:
    out({"instances": state["instances"]})
if cmd == ["agent", "instance", "create"]:
    inst = {"id": f"ainst-{args[3]}", "agent_name": args[3], "state": "active",
            "main_session_id": f"sess-{args[3]}", "provider": "claude-mm4"}
    state["instances"].append(inst)
    out({"instance": inst})
if cmd == ["agent", "instance", "archive"]:
    before = len(state["instances"])
    state["instances"] = [i for i in state["instances"] if i["agent_name"] != args[3]]
    if len(state["instances"]) == before:
        print('{"error":"agent_instance_not_found"}', file=sys.stderr)
        sys.exit(1)
    out({})
if cmd == ["agent", "heartbeat", "sources"]:
    out({"scripts": state["scripts"].get(args[3], [])})
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
    for part in ("agents", "tools", "bin"):
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
        self.state = {"root": "", "instances": [], "scripts": {}, "memory": {}}
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
        return {"PATH": f"{self.dir}{os.pathsep}{os.environ['PATH']}",
                "FAKE_COMPOZY_STATE": str(self.state_path), "FAKE_COMPOZY_LOG": str(self.log_path)}


@pytest.fixture
def fake(tmp_path):
    return FakeCompozy(tmp_path)


def run(script, *args, env=None, repo_root=REPO, stdin=None):
    full_env = {k: v for k, v in os.environ.items() if not k.startswith("COMPOZY_")}
    full_env.update(env or {})
    return subprocess.run([str(repo_root / "bin" / script), *args], capture_output=True, text=True,
                          env=full_env, input=stdin)


@pytest.fixture
def workspace(tmp_path):
    """A project root with a valid factory.toml copied from factory.example.toml."""
    root = tmp_path / "acme-api"
    (root / ".compozy" / "factory").mkdir(parents=True)
    shutil.copy(REPO / "factory.example.toml", root / ".compozy" / "factory" / "factory.toml")
    return root
