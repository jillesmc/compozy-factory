"""Run: python3 -m pytest tools/test_factory_intake.py"""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_intake as fi  # noqa: E402

F = {"title": 'Malformed id: "/view" -> 500', "severity": "low", "count": 1,
     "error_class": "acme.exceptions:ValidationException",
     "transaction": "WebTransaction/Function/acme_api.routes:view", "evidence_nrql": "SELECT 1"}


def test():
    fp = fi.fingerprint("acme-api-production", F)
    assert fp == fi.fingerprint(" Acme-Api-Production ", dict(F, title="other title")), "title must not change fp"
    assert fp != fi.fingerprint("acme-api-production", dict(F, transaction="x")), "transaction is part of fp"
    assert fi.slug_for(F, fp) == f"fab-{fp[:6]}-malformed-id-view-500"

    calls, issues = [], []
    fi.gh = lambda *a, stdin=None: (calls.append(a), json.dumps(
        issues if a[:2] == ("issue", "list") else {"number": 7, "html_url": "https://x/7"}))[1]
    with tempfile.TemporaryDirectory() as tmp:
        fi.TASKS, fi.ROOT = Path(tmp) / ".compozy" / "tasks", Path(tmp)
        assert fi.check("app", F)["duplicate"] is False
        out = fi.create("app", F)
        task = (fi.TASKS / out["task_slug"] / "task_01.md").read_text()
        assert 'title: "Task 1: Malformed id: \\"/view\\" -> 500"' in task
        assert '# Task 1: Malformed id: "/view" -> 500' in task
        assert sum(1 for c in calls if c[-1].startswith("body=🏭")) == 1, "exactly one milestone comment"
        assert fi.check("app", F)["duplicate"] is True, "local open task dedupes"
        task_path = fi.TASKS / out["task_slug"] / "task_01.md"
        task_path.write_text(task.replace("status: pending", "status: completed"))
        assert fi.check("app", F)["duplicate"] is False, "completed task no longer blocks"
        issues.append({"number": 7, "url": "https://x/7", "body": f"<!-- fabrica-fingerprint: {fi.fingerprint('app', F)} -->"})
        assert fi.check("app", F)["duplicate"] is True, "open issue dedupes"
    print("ok")


if __name__ == "__main__":
    test()


def test_unsupported_post_language_is_refused_before_posting(tmp_path):
    (tmp_path / ".compozy" / "factory").mkdir(parents=True)
    (tmp_path / ".compozy" / "factory" / "factory.toml").write_text(
        '[repo]\ngithub = "acme/acme-api"\n[language]\nposts = "es"\n')
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "gh").write_text(f"#!/bin/sh\ntouch {tmp_path}/gh-called\n")
    (bin_dir / "gh").chmod(0o755)
    proc = subprocess.run([sys.executable, str(Path(fi.__file__)), "create", "--app", "acme-api-production"],
                          cwd=tmp_path, input=json.dumps(F), capture_output=True, text=True,
                          env={**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"})
    assert proc.returncode != 0
    assert proc.stderr.strip() == 'refused: [language] posts "es" not supported by factory_intake.py (pt-BR, en)'
    assert not (tmp_path / "gh-called").exists() and not (tmp_path / ".compozy" / "tasks").exists()
