"""bin/migrate-memory: UT-030 to UT-032 (US-005)."""
from datetime import date

from conftest import run

OLD = "- 2026-11-16 · monorepo · #226 -> sess-abc (round 1)\n- 2026-11-16 · monorepo · second\n"


def test_ut_030_copies_memory_with_source_first_line(fake):
    fake.state["memory"] = {"vigia-pr": OLD}
    fake.save()
    proc = run("migrate-memory", "--workspace", "acme-api", "--map", "vigia-pr=factory-pr-watcher", env=fake.env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.strip() == "migrated vigia-pr -> factory-pr-watcher (2 lines)"
    memory = fake.load()["memory"]
    assert memory["factory-pr-watcher"] == f"Migrated from vigia-pr (acme-api) on {date.today().isoformat()}\n" + OLD
    assert memory["vigia-pr"] == OLD, "old memory stays"


def test_ut_031_empty_source_is_skipped(fake):
    proc = run("migrate-memory", "--workspace", "acme-api", "--map", "maestro=factory-dispatcher", env=fake.env())
    assert (proc.returncode, proc.stdout.strip()) == (0, "skipped maestro (empty)")
    assert not any(c[:3] == ["agent", "memory", "write"] for c in fake.calls)


def test_ut_032_non_empty_target_refused_without_append(fake):
    fake.state["memory"] = {"vigia-pr": OLD, "porteiro": OLD, "factory-pr-watcher": "- existing\n"}
    fake.save()
    proc = run("migrate-memory", "--workspace", "acme-api",
               "--map", "porteiro=factory-gatekeeper,vigia-pr=factory-pr-watcher", env=fake.env())
    assert (proc.returncode, proc.stdout.strip()) == (1, "refused: factory-pr-watcher already has memory; pass --append")
    assert not any(c[:3] == ["agent", "memory", "write"] for c in fake.calls), "a refusal writes nothing"
    proc = run("migrate-memory", "--workspace", "acme-api", "--map", "vigia-pr=factory-pr-watcher", "--append",
               env=fake.env())
    assert proc.returncode == 0
    assert fake.load()["memory"]["factory-pr-watcher"].startswith("- existing\nMigrated from vigia-pr (acme-api) on ")
