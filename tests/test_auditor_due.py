"""Run: python3 -m pytest tests/test_auditor_due.py"""
import importlib.machinery
import importlib.util
from datetime import datetime, timezone
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "agents" / "factory-auditor" / "sources" / "due"
loader = importlib.machinery.SourceFileLoader("due", str(SRC))
spec = importlib.util.spec_from_loader("due", loader)
due = importlib.util.module_from_spec(spec)
loader.exec_module(due)

NOW = datetime(2026, 11, 20, tzinfo=timezone.utc)
ISSUE = {"number": 7, "title": "x", "html_url": "u", "state_reason": "completed",
         "closed_at": "2026-11-10T00:00:00Z", "body": "<!-- factory-fingerprint: abc123 -->"}


def c(body, at="2026-11-12T00:00:00Z"):
    return {"body": body, "created_at": at}


def test_audit_state():
    # closed 10 days ago, no deploy marker -> due on closed_at, attempt 1
    item = due.audit_state(ISSUE, [], NOW)
    assert item and item["attributes"]["attempt"] == 1 and item["attributes"]["deploy_at"] == "2026-11-10T00:00:00Z"
    # legacy fabrica marker counts too
    assert due.audit_state({**ISSUE, "body": "<!-- fabrica-fingerprint: abc -->"}, [], NOW)
    # deploy marker wins over closed_at and is not due yet (deploy + 7d > now)
    assert due.audit_state(ISSUE, [c("<!-- factory-deploy: 2026-11-15T00:00:00Z -->", "2026-11-15T00:00:00Z")], NOW) is None
    # final verdicts stop the audit
    assert due.audit_state(ISSUE, [c("<!-- factory-audit: confirmed -->")], NOW) is None
    assert due.audit_state(ISSUE, [c("<!-- factory-audit: regressed -->")], NOW) is None
    # one inconclusive -> attempt 2 due at deploy + 14d (not yet on 11-20)
    assert due.audit_state(ISSUE, [c("<!-- factory-audit: inconclusive -->")], NOW) is None
    item = due.audit_state(ISSUE, [c("<!-- factory-audit: inconclusive -->")], datetime(2026, 11, 25, tzinfo=timezone.utc))
    assert item["attributes"]["attempt"] == 2 and item["key"].endswith("-a2")
    # three inconclusive -> give up
    assert due.audit_state(ISSUE, [c("<!-- factory-audit: inconclusive -->")] * 3, datetime(2027, 1, 1, tzinfo=timezone.utc)) is None
    # verdict older than a newer deploy does not count (reopened and redeployed)
    old = c("<!-- factory-audit: regressed -->", "2026-11-11T00:00:00Z")
    new_deploy = c("<!-- factory-deploy: 2026-11-12T00:00:00Z -->", "2026-11-12T00:00:00Z")
    assert due.audit_state(ISSUE, [old, new_deploy], NOW)["attributes"]["deploy_at"] == "2026-11-12T00:00:00Z"
    # duplicates and issues without fingerprint are ignored
    assert due.audit_state({**ISSUE, "state_reason": "not_planned"}, [], NOW) is None
    assert due.audit_state({**ISSUE, "body": "no marker"}, [], NOW) is None
