"""Run: python3 -m pytest tools/test_factory_intake.py"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_envelope as fe  # noqa: E402
import factory_intake as fi  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
INPUTS = {"service": "acme-api-production", "error_class": "acme.exceptions:ValidationException",
          "location": "WebTransaction/Function/acme_api.routes:view"}
ENV = {"provider": "newrelic", "provider_finding_id": "nr-errors:1", "fingerprint_inputs": INPUTS,
       "evidence": [{"query": "SELECT 1", "since": "a", "until": "b", "summary": "1 error"}],
       "verifier": {"provider": "newrelic", "payload": {"query": "SELECT 1"}}}
F = {"title": 'Malformed id: "/view" -> 500', "severity": "low", "count": 1, "evidence_query": "SELECT 1",
     "envelope": ENV}
CONF = {"roles": {"code": {"provider": "github"}, "work": {"provider": "jira"}}}


def fake_gh(calls, issues):
    def gh(*a, stdin=None):
        calls.append((a, stdin))
        return json.dumps(issues if a[:2] == ("issue", "list") else {"number": 7, "html_url": "https://x/7"})
    return gh


def test():
    env = fe.envelope(ENV)
    fp = env["fingerprint"]
    assert fp == fe.envelope({**ENV, "fingerprint_inputs": {**INPUTS, "service": " Acme-Api-Production "}})["fingerprint"]
    assert fp != fe.envelope({**ENV, "fingerprint_inputs": {**INPUTS, "location": "x"}})["fingerprint"]
    assert fi.slug_for(F, fp) == f"fab-{fp[:6]}-malformed-id-view-500"

    calls, issues = [], []
    fi.gh = fake_gh(calls, issues)
    with tempfile.TemporaryDirectory() as tmp:
        fi.TASKS, fi.ROOT = Path(tmp) / ".compozy" / "tasks", Path(tmp)
        assert fi.check(env)["duplicate"] is False
        out = fi.create(CONF, F, env)
        assert out["work_key"] == "", "work/jira: the scribe performs work.create"
        task = (fi.TASKS / out["task_slug"] / "task_01.md").read_text()
        assert 'title: "Task 1: Malformed id: \\"/view\\" -> 500"' in task
        assert '# Task 1: Malformed id: "/view" -> 500' in task and 'service: "acme-api-production"' in task
        assert sum(1 for c in calls if c[0][-1].startswith("body=🏭")) == 1, "exactly one milestone comment"
        body = json.loads(next(c[1] for c in calls if c[0][:2] == ("api", "-X") and c[0][3].endswith("/issues")))["body"]
        assert fe.read_block(fe.FINDING, body) == env, "the envelope travels in the issue body"
        assert fe.read_block(fe.FACTS, body) == {"kind": "bugfix", "attached_doc": False, "origin": "newrelic",
                                                 "touches_ci": False}
        assert f"<!-- factory-fingerprint: {fp} -->" in body, "existing issues keep deduplicating"
        assert fi.check(env)["duplicate"] is True, "local open task dedupes"
        task_path = fi.TASKS / out["task_slug"] / "task_01.md"
        task_path.write_text(task.replace("status: pending", "status: completed"))
        assert fi.check(env)["duplicate"] is False, "completed task no longer blocks"
        issues.append({"number": 7, "url": "https://x/7", "body": f"<!-- fabrica-fingerprint: {fp} -->"})
        assert fi.check(env)["duplicate"] is True, "open issue dedupes"
    print("ok")


def test_work_item_on_github_takes_its_key_from_the_work_adapter(monkeypatch):  # BR-5, US-008
    calls = []
    fi.gh = fake_gh(calls, [])
    adapter = REPO / "skills" / "factory-work-github"
    monkeypatch.setattr(fi.fg, "resolve", lambda root, role, provider, conf: (f"factory-{role}-{provider}", adapter))
    conf = {"roles": {"code": {"provider": "github"}, "work": {"provider": "github"}}}
    with tempfile.TemporaryDirectory() as tmp:
        fi.TASKS, fi.ROOT = Path(tmp) / ".compozy" / "tasks", Path(tmp)
        out = fi.create(conf, F, fe.envelope(ENV))
        assert out["work_key"] == "#7"
        patch = next(c[0] for c in calls if "PATCH" in c[0])
        assert patch[-1] == 'title=#7: Malformed id: "/view" -> 500'
        assert 'work_key: "#7"' in (fi.TASKS / out["task_slug"] / "task_01.md").read_text()


def intake(tmp_path, toml, finding=F):
    (tmp_path / ".compozy" / "factory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".compozy" / "factory" / "factory.toml").write_text(toml)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {tmp_path}/gh-called\n")
    (bin_dir / "gh").chmod(0o755)
    return subprocess.run([sys.executable, str(Path(fi.__file__)), "create"], cwd=tmp_path, input=json.dumps(finding),
                          capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})


def test_unsupported_post_language_or_code_provider_is_refused_before_posting(tmp_path):
    roles = '[roles.code]\nprovider = "github"\n[roles.code.github]\nrepo = "acme/acme-api"\n'
    proc = intake(tmp_path, '[language]\nposts = "es"\n' + roles)
    assert proc.returncode != 0
    assert proc.stderr.strip() == 'refused: [language] posts "es" not supported by factory_intake.py (pt-BR, en)'
    proc = intake(tmp_path, '[language]\nposts = "en"\n[roles.code]\nprovider = "gitlab"\n')
    assert proc.stderr.strip() == 'refused: factory_intake.py creates issues through code/github only ([roles.code] provider "gitlab")'
    proc = intake(tmp_path, '[language]\nposts = "en"\n' + roles, {**F, "envelope": {"provider": "newrelic"}})
    assert proc.stderr.strip().startswith("refused: envelope: envelope needs provider_finding_id")
    assert not (tmp_path / "gh-called").exists() and not (tmp_path / ".compozy" / "tasks").exists()
