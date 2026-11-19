"""bin/install: UT-001 to UT-007 (factory-user-agents US-001); factory-roles records in tests/test_roles.py."""
import hashlib
import os
from pathlib import Path

import factorylib as fl
from conftest import run

AGENTS = fl.AGENTS + fl.ON_DEMAND  # install copies every factory-* dir, on-demand ones included
ADAPTERS = sorted(f"{r}-{p}" for r, p in fl.bundled_adapters())
TOOLS = "factory_envelope.py, factory_external.py, factory_guard.py, factory_intake.py"


def snapshot(root):
    return {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in sorted(Path(root).rglob("*")) if p.is_file()}


def test_ut_001_clean_home_gets_every_agent_and_tools(repo, home, home_env):
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 0, proc.stderr
    assert sorted(p.name for p in (home / "agents").iterdir()) == sorted(AGENTS)
    for agent in AGENTS:
        assert (home / "agents" / agent / "AGENT.md").is_file()
        assert not (home / "agents" / agent).is_symlink()
    assert (home / "factory" / "tools" / "factory_intake.py").is_file()
    assert not (home / "factory" / "tools" / "test_factory_intake.py").exists(), "tests are not installed"
    assert proc.stdout.splitlines()[:len(AGENTS) + 1] == [f"installed {a}" for a in AGENTS] + [f"installed tools ({TOOLS})"]
    assert os.access(home / "agents" / "factory-dispatcher" / "sources" / "deliver", os.X_OK)


def test_ut_002_second_run_is_unchanged_and_writes_nothing(repo, home, home_env):
    run("install", env=home_env, repo_root=repo)
    before = snapshot(home)
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 0
    lines = proc.stdout.splitlines()
    assert lines[:len(AGENTS) + 1] == [f"unchanged {a}" for a in AGENTS] + ["unchanged tools"]
    assert [line.split(" tree ")[0] for line in lines[len(AGENTS) + 1:-1]] == [f"unchanged adapter {a}" for a in ADAPTERS]
    digest = hashlib.sha256((repo / "agents" / "factory-reviewer" / "AGENT.md").read_bytes()).hexdigest()  # fake daemon
    assert lines[-1] == f"reviewer factory-reviewer digest {digest[:12]} (origin global, layer user) recorded"
    assert snapshot(home) == before


def test_ut_003_changed_deliver_prints_source_approval_with_new_hash(repo, home, home_env):
    run("install", env=home_env, repo_root=repo)
    deliver = repo / "agents" / "factory-dispatcher" / "sources" / "deliver"
    deliver.write_text(deliver.read_text() + "# changed\n")
    proc = run("install", env=home_env, repo_root=repo)
    digest = fl.script_hash(deliver)
    assert "updated factory-dispatcher" in proc.stdout
    assert f"compozy agent heartbeat sources approve factory-dispatcher deliver --sha256 {digest} --workspace @user --yes" in proc.stdout
    assert "factory-auditor" not in proc.stdout.split("approve before attaching:")[1], "unchanged agents need no approval"


def test_ut_004_changed_authority_block_prints_authority_approval(repo, home, home_env):
    run("install", env=home_env, repo_root=repo)
    heartbeat = repo / "agents" / "factory-auditor" / "HEARTBEAT.md"
    old = fl.authority_digest(heartbeat.read_text())
    heartbeat.write_text(heartbeat.read_text().replace('title: "Audit the fix in production"', 'title: "Audit the fix"'))
    new = fl.authority_digest(heartbeat.read_text())
    assert new and new != old
    proc = run("install", env=home_env, repo_root=repo)
    assert f"compozy agent heartbeat authority approve factory-auditor --sha256 {new} --workspace @user --yes" in proc.stdout
    # Digest reference computed with Go encoding/json (HTML escaping, sorted actions) as the daemon does.
    tricky = ('---\nversion: 1\nactions:\n  - id: zeta\n    title: "Z <tag> & \\"q\\""\n'
              '    instructions: "Linha com acentuação — e <!-- marker --> & more."\n    auto_accept: false\n'
              "  - id: auditar\n    title: Audit\n    instructions: 'x'\n    auto_accept: true\n---\n")
    assert fl.authority_digest(tricky) == "sha256:98a48e632fe01f3b04f9bbc7527281d6926735a5f523114ec9bfcfc1f351e453"
    assert fl.authority_digest("---\nversion: 1\n---\n") == ""


def test_ut_005_non_factory_agent_is_untouched(repo, home, home_env):
    general = home / "agents" / "general"
    general.mkdir(parents=True)
    (general / "AGENT.md").write_text("---\nname: general\n---\n")
    before = snapshot(general)
    proc = run("install", "--prune", env=home_env, repo_root=repo)
    assert proc.returncode == 0
    assert snapshot(general) == before
    assert "general" not in proc.stdout


def test_ut_006_stale_factory_agent_reported_and_pruned_only_with_prune(repo, home, home_env):
    old = home / "agents" / "factory-old"
    old.mkdir(parents=True)
    (old / "AGENT.md").write_text("old")
    proc = run("install", env=home_env, repo_root=repo)
    assert "stale factory-old" in proc.stdout.splitlines()
    assert old.is_dir()
    proc = run("install", "--prune", env=home_env, repo_root=repo)
    assert "pruned factory-old" in proc.stdout.splitlines()
    assert not old.exists()


def test_ut_007_symlinked_agent_dir_is_refused_before_writing(repo, home, home_env, tmp_path):
    target = tmp_path / "elsewhere"
    target.mkdir()
    (home / "agents").mkdir()
    (home / "agents" / "factory-scribe").symlink_to(target)
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 1
    assert proc.stdout.strip() == f"refused: {home / 'agents' / 'factory-scribe'} is a symlink (symlink_not_supported)"
    assert sorted(p.name for p in (home / "agents").iterdir()) == ["factory-scribe"]
    assert not (home / "factory").exists() and not any(target.iterdir())


def test_dry_run_writes_nothing(repo, home, home_env):
    proc = run("install", "--dry-run", env=home_env, repo_root=repo)
    assert proc.returncode == 0 and "installed factory-sentinel" in proc.stdout
    assert proc.stdout.rstrip().endswith("dry-run: nothing written")
    assert not any(home.iterdir())
