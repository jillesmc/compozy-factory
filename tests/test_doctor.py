"""bin/doctor: read-only report of factory instances that are not running unattended, across workspaces."""
from conftest import run

import factorylib as fl


def inst(agent, ws="ws_a", **extra):
    return {"id": f"ainst-{agent}", "agent_name": agent, "workspace_id": ws, "state": "active",
            "unattended_turns": 0, "unattended_turn_limit": 300, "failure_streak": 0, "fatigue_limit": 10,
            "checks_enabled": True, "needs_attention": False, "sources": [], **extra}


def doctor(fake, workspace, instances, *args):
    fake.state["workspaces"] = [{"id": "ws_a", "name": "acme", "root_dir": str(workspace)},
                                {"id": "ws_b", "name": "plain", "root_dir": str(workspace.parent / "none")}]
    fake.state["instances"] = instances
    fake.save()
    return run("doctor", *args, env=fake.env())


def test_healthy_team_is_ok_and_changes_nothing(fake, workspace):
    proc = doctor(fake, workspace, [inst(a) for a in fl.AGENTS])
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines() == ["ok 6 instances"]
    assert all(c[:3] == ["agent", "instance", "list"] or c == ["workspace", "list"] for c in fake.calls)
    assert "--all-workspaces" in fake.calls[0]


def test_reports_missing_paused_attention_and_warnings(fake, workspace):
    team = [inst(a) for a in fl.AGENTS if a != "factory-auditor"]
    team[0] = inst("factory-sentinel", state="paused", pause_reason="unattended")
    team[1] = inst("factory-scribe", needs_attention=True, main_session_id="sess-1")
    team[2] = inst("factory-dispatcher", unattended_turns=250, sources=[{"id": "deliver", "failure_streak": 2}])
    proc = doctor(fake, workspace, team + [inst("other-agent", state="paused", pause_reason="fatigue")])
    assert proc.returncode == 1
    lines = proc.stdout.splitlines()
    assert "missing   acme factory-auditor no instance: bin/attach acme" in lines
    assert any(l.startswith("paused    acme factory-sentinel unattended:") for l in lines)
    assert any(l.startswith("attention acme factory-scribe main session sess-1") for l in lines)
    assert "warn      acme factory-dispatcher unattended turns 250/300" in lines
    assert "warn      acme factory-dispatcher source deliver failing (2x)" in lines
    assert not any("other-agent" in l for l in lines)
    assert lines[-1] == "ok 5 instances"


def test_warnings_alone_exit_zero(fake, workspace):
    now = "2026-11-26T18:00:00.123456789Z"
    team = [inst(a, now=now, next_wake_at="2026-11-26T19:00:00Z", last_wake_at="2026-11-26T16:00:00Z")
            for a in fl.AGENTS]
    team[0]["failure_streak"] = 3
    team[1]["next_wake_at"] = "2026-11-26T17:00:00.5Z"
    team[2]["last_wake_at"] = "2026-11-25T12:00:00Z"
    proc = doctor(fake, workspace, team)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert "warn      acme factory-sentinel failure streak 3/10" in lines
    assert "warn      acme factory-scribe check-in overdue since 2026-11-26T17:00:00.5Z" in lines
    assert "warn      acme factory-dispatcher no check-in since 2026-11-25T12:00:00Z" in lines
    assert len(lines) == 4


def test_fix_wakes_only_stopped_mains_that_wait_for_nobody(fake, workspace):
    team = [inst(a) for a in fl.AGENTS]
    team[0].update(needs_attention=True, main_session_id="sess-dead")
    team[1].update(needs_attention=True, main_session_id="sess-asks")
    fake.state["sessions"] = {
        "sess-dead": {"state": "stopped", "pending_interactions": [], "updated_at": "t1"},
        "sess-asks": {"state": "stopped", "pending_interactions": [{"kind": "approval"}], "updated_at": "t1"}}
    proc = doctor(fake, workspace, team, "--fix")
    assert proc.returncode == 1  # the pending approval still needs the operator
    lines = proc.stdout.splitlines()
    assert "fixed     acme factory-sentinel woke stopped main session sess-dead (ainst-factory-sentinel)" in lines
    assert any(l.startswith("attention acme factory-scribe main session sess-asks needs you: main waits") for l in lines)
    key = "doctor-wake-sess-dead-t1"
    assert [c for c in fake.calls if c[:2] == ["session", "prompt"]] == [
        ["session", "prompt", "sess-dead", fl.BOUNDARY, "--message-id", key, "--idempotency-key", key]]


def test_fixed_alone_exits_zero_and_without_fix_nothing_is_touched(fake, workspace):
    team = [inst(a) for a in fl.AGENTS]
    team[0].update(needs_attention=True, main_session_id="sess-dead")
    fake.state["sessions"] = {"sess-dead": {"state": "stopped", "pending_interactions": []}}
    assert doctor(fake, workspace, team).returncode == 1
    assert not [c for c in fake.calls if c[:2] in (["session", "prompt"], ["session", "status"])]
    assert doctor(fake, workspace, team, "--fix").returncode == 0
