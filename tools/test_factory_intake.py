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


def test_signature_dedupes_a_library_error_across_consumers():
    """Real lib-schema-migration-apply errors: volatile parts go, the step and the defect stay."""
    cases = [
        ("apply failed: atlas command failed: signal: killed: ",
         "apply failed: atlas command failed: signal: killed", "apply",
         "apply failed: atlas command failed: signal: killed%"),
        ('status failed: atlas command failed: exit status 1: Error: mysql: query "SELECT * FROM t WHERE a = \'x\'": '
         "Error 1146 (42S02): Table 'db.t' doesn't exist",
         "status failed: atlas command failed: exit status <n>: Error: mysql: query <q>: Error <n> (<sqlstate>): "
         "Table <q> doesn't exist", "status",
         "status failed: atlas command failed: exit status %: Error: mysql: query %: Error % (%): Table % "
         "doesn\\'t exist%"),
        ('validate failed: atlas command failed: exit status 1: Error: replaying the migration directory: sql/migrate: '
         'executing statement "CREATE VIEW `v` AS\nWITH RECURSIVE `g` (`email`) AS (',
         "validate failed: atlas command failed: exit status <n>: Error: replaying the migration directory: "
         "sql/migrate: executing statement <q>", "validate",
         "validate failed: atlas command failed: exit status %: Error: replaying the migration directory: "
         "sql/migrate: executing statement %"),
        ("apply failed: atlas command failed: exit status 1: Error: mysql://u:p@db.prod.example.com:3306/x: "
         "dial tcp 10.0.3.4:3306: lookup db.prod.example.com: no such host (SQLSTATE HY000)",
         "apply failed: atlas command failed: exit status <n>: Error: <url> dial tcp <host>: lookup <host>: "
         "no such host (SQLSTATE <sqlstate>)", "apply",
         "apply failed: atlas command failed: exit status %: Error: % dial tcp %: lookup %: no such host (SQLSTATE %)%"),
    ]
    for error, error_class, location, like in cases:
        assert fe.signature(error) == {"error_class": error_class, "location": location, "like": like}, error
    panics = [f"apply failed: atlas command failed: exit status 2: panic: runtime error: index out of range [0] "
              f"with length 0\n\ngoroutine 1 [running]:\nExecute(0xc000{n}e310)" for n in (68, 69)]
    assert fe.signature(panics[0]) == fe.signature(panics[1])
    assert fe.signature("weird", "status")["location"] == "status"
    assert fe.signature("weird", None)["location"] == "unknown"
    assert len(fe.signature("x failed: " + "y" * 400)["error_class"]) == 200
    proc = subprocess.run([sys.executable, str(REPO / "tools" / "factory_envelope.py"), "signature"],
                          input=json.dumps({"error": cases[0][0], "step": None}), capture_output=True, text=True)
    assert json.loads(proc.stdout)["error_class"] == cases[0][1]
