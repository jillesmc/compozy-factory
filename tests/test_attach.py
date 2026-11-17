"""bin/attach (UT-010 to UT-012, US-002) and bin/reload, with CompozyOS faked at the subprocess boundary."""
import factorylib as fl
from conftest import run


def test_ut_010_valid_toml_creates_one_instance_per_team_agent(fake, workspace):
    proc = run("attach", str(workspace), env=fake.env())
    assert proc.returncode == 0, proc.stderr
    creates = [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]]
    assert creates == fl.AGENTS
    assert all(c[c.index("--workspace") + 1] == str(workspace) for c in fake.calls if "--workspace" in c)
    assert proc.stdout.splitlines()[0] == "attached factory-sentinel     (ainst-factory-sentinel)"
    assert len(proc.stdout.splitlines()) == 6


def test_ut_010_team_and_agents_flag_narrow_the_set(fake, workspace):
    toml = workspace / ".compozy" / "factory" / "factory.toml"
    toml.write_text(toml.read_text().replace('agents = ["factory-sentinel", "factory-scribe", "factory-dispatcher",\n'
                                             '          "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]',
                                             'agents = ["factory-scribe", "factory-auditor"]'))
    run("attach", str(workspace), env=fake.env())
    run("attach", str(workspace), "--agents", "factory-gatekeeper", env=fake.env())
    creates = [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]]
    assert creates == ["factory-scribe", "factory-auditor", "factory-gatekeeper"]


def test_ut_011_missing_ci_runner_refuses_with_zero_cli_calls(fake, workspace):
    toml = workspace / ".compozy" / "factory" / "factory.toml"
    toml.write_text(toml.read_text().replace('runner = "self-hosted-${{ github.run_id }}"', ""))
    proc = run("attach", str(workspace), env=fake.env())
    assert proc.returncode == 1
    assert proc.stdout.strip() == f"refused: {toml} missing [ci] runner"
    assert fake.calls == []


def test_ut_011_missing_file_refuses_naming_it(fake, workspace):
    toml = workspace / ".compozy" / "factory" / "factory.toml"
    toml.unlink()
    proc = run("attach", str(workspace), env=fake.env())
    assert (proc.returncode, proc.stdout.strip()) == (1, f"refused: {toml} missing")
    assert fake.calls == []


def test_ut_012_existing_instance_reported_exists_not_duplicated(fake, workspace):
    fake.state["instances"] = [{"id": "ainst-x", "agent_name": "factory-scribe", "state": "active"}]
    fake.save()
    proc = run("attach", str(workspace), env=fake.env())
    assert "exists factory-scribe" in proc.stdout.splitlines()
    creates = [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]]
    assert "factory-scribe" not in creates and len(creates) == 5


def test_unapproved_source_refuses_that_agent_with_the_approve_command(fake, workspace):
    fake.state["scripts"] = {"factory-dispatcher": [{"name": "deliver", "sha256": "sha256:abc", "approved": False}]}
    fake.save()
    proc = run("attach", str(workspace), env=fake.env())
    assert proc.returncode == 1
    assert ("refused: factory-dispatcher source deliver not approved; run "
            "compozy agent heartbeat sources approve factory-dispatcher deliver --sha256 sha256:abc --workspace @user --yes") in proc.stdout
    creates = [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]]
    assert "factory-dispatcher" not in creates and len(creates) == 5


def test_workspace_name_is_resolved_through_compozy(fake, workspace):
    fake.state["root"] = str(workspace)
    fake.save()
    proc = run("attach", "acme-api", env=fake.env())
    assert proc.returncode == 0, proc.stderr
    assert fake.calls[0] == ["workspace", "info", "acme-api"]


def test_reload_archives_and_recreates_each_agent(fake, workspace):
    fake.state["instances"] = [{"id": "ainst-old", "agent_name": "factory-scribe", "state": "active"}]
    fake.save()
    proc = run("reload", str(workspace), "factory-scribe", "factory-auditor", env=fake.env())
    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.splitlines() == [
        "== factory-scribe", "   main: sess-factory-scribe provider: claude-mm4",
        "== factory-auditor", "   (no active instance)", "   main: sess-factory-auditor provider: claude-mm4"]


INVALID = [  # (text in factory.example.toml, replacement, refusal tail); every case refuses before any CLI call
    ('authors = ["octocat"]', 'authors = "octocat"', "[authorization] authors must be a non-empty list of strings"),
    ('authors = ["octocat"]', "authors = []", "[authorization] authors must be a non-empty list of strings"),
    ('agents = ["factory-sentinel", "factory-scribe", "factory-dispatcher",\n'
     '          "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]', "agents = []",
     "[team] agents must be a non-empty list of strings"),
    ('merge = "merge"', 'merge = "squash"', '[repo] merge must be "merge"'),
    ('posts = "pt-BR"', 'posts = "es"', '[language] posts "es" not supported by factory_intake.py (pt-BR, en)'),
    ("account_id = 1234567", "", "missing [newrelic] account_id"),
    ("[jira.fields]\n", "[jira.x]\n", "missing [jira] fields"),
    ('[jira.defaults]\n', '[jira.d]\n', "missing [jira] defaults"),
    ('project = "ACME"', 'project = "ACME"\ndefaults = "x"', "[jira] defaults must be a table"),
]


def test_attach_refuses_invalid_values_with_zero_cli_calls(fake, workspace):
    toml = workspace / ".compozy" / "factory" / "factory.toml"
    original = toml.read_text()
    for old, new, tail in INVALID:
        assert old in original, old
        text = original.replace(old, new, 1)
        if tail.endswith("must be a table"):
            text = text.replace("[jira.defaults]\n", "[jira.d]\n")
        toml.write_text(text)
        proc = run("attach", str(workspace), env=fake.env())
        assert (proc.returncode, proc.stdout.strip()) == (1, f"refused: {toml} {tail}"), (new, proc.stdout)
    assert fake.calls == []


def test_omitted_team_means_all_six(fake, workspace):
    toml = workspace / ".compozy" / "factory" / "factory.toml"
    text = toml.read_text()
    toml.write_text(text[:text.index("[team]")] + text[text.index("[repo]"):])
    assert run("attach", str(workspace), env=fake.env()).returncode == 0
    assert [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]] == fl.AGENTS
