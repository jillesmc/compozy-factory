"""bin/conformance-trace and bin/conformance-record (spec factory-roles BR-11, US-010).

The record is written into a disposable repo copy (`repo`), from a real bin/install into a temporary
COMPOZY_HOME; CompozyOS is faked. No provider is called: the trace lines stand in for a walk.
"""
import json
import runpy
import subprocess

import factorylib as fl
import factory_guard as fg
from conftest import REPO, run

MATRIX = runpy.run_path(str(REPO / "bin" / "conformance-trace"))["MATRIX"]
CONFIGURED = [("signals", "newrelic"), ("code", "github"), ("work", "jira"),
              ("review", "github-app-review"), ("deploy", "github-deployments")]
EXTENSIONS = [{"name": "newrelic", "version": "0.9.0"}, {"name": "github-app-review", "version": "1.4.0"}]


def row(op):
    return next(r for r, (o, _) in MATRIX.items() if o == op)


def trace(repo, role, provider, op, outcome="pass", *extra):
    return run("conformance-trace", role, provider, op, "--row", row(op), "--outcome", outcome,
               "--effect", f"{op} observed", *extra, repo_root=repo)


def walk(repo, role, provider, skip=()):
    for op in fl.contract_operations(role):
        if op not in skip:
            proc = trace(repo, role, provider, op)
            assert proc.returncode == 0, proc.stdout + proc.stderr


def record(repo, workspace, env, role, provider):
    return run("conformance-record", role, provider, "--workspace-root", str(workspace), env=env, repo_root=repo)


def setup(fake, repo, env):
    """A git checkout (factory_commit) with the adapters installed and the extensions listed."""
    git = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com"]
    subprocess.run([*git, "init", "-q"], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "base"], check=True)
    assert run("install", env=env, repo_root=repo).returncode == 0
    fake.load()
    fake.state["extensions"] = EXTENSIONS
    fake.save()
    return subprocess.run([*git, "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()


def test_trace_validates_the_operation_and_the_matrix_row(repo):
    proc = run("conformance-trace", "code", "github", "code.issue_delete", "--row", "8", "--outcome", "pass",
               "--effect", "x", repo_root=repo)
    assert proc.stdout.strip() == "refused: code.issue_delete is not an operation of contracts/code.md"
    proc = run("conformance-trace", "code", "github", "code.issue_create", "--row", "9", "--outcome", "pass",
               "--effect", "x", repo_root=repo)
    assert proc.stdout.strip() == "refused: matrix row 9 is not code.issue_create (its rows: 8)"
    proc = run("conformance-trace", "code", "githb", "code.issue_create", "--row", "8", "--outcome", "pass",
               "--effect", "x", repo_root=repo)
    assert proc.stdout.strip() == "refused: no bundled adapter code/githb; pass --dir for a custom adapter"
    assert not (repo / "conformance").exists()


def test_trace_line_shape_and_redaction(repo):
    token = "ghp_" + "a" * 36
    proc = trace(repo, "code", "github", "code.issue_labels", "pass",
                 "--input", "issue=12", "--input", "add=factory:in-progress", "--input", f"GH_TOKEN={token}",
                 "--input", "body=" + "x" * 300, "--call", f"gh issue edit 12 --add-label x -H 'token {token}'", "issue #12",
                 "--call", "ext__github_app_review__pr_context", "acme/acme-api#3")
    assert proc.returncode == 0, proc.stdout
    text = (repo / "conformance" / "code-github.trace.jsonl").read_text()
    assert token not in text
    line = json.loads(text)
    assert set(line) == {"ts", "role", "provider", "operation", "inputs", "provider_calls", "outcome", "effect",
                         "equivalent_to"}
    assert line["inputs"]["GH_TOKEN"] == "[redacted]" and line["inputs"]["issue"] == "12"
    assert line["inputs"]["body"] == "x" * 200 + "…[truncated]"
    assert line["provider_calls"][0] == {"argv": ["gh", "issue", "edit", "12", "--add-label", "x", "-H", "[redacted]"],
                                         "target": "issue #12"}
    assert line["provider_calls"][1] == {"tool": "ext__github_app_review__pr_context", "target": "acme/acme-api#3"}
    assert line["equivalent_to"] == {"row": "14", "call": MATRIX["14"][1]}
    assert (line["outcome"], line["effect"]) == ("pass", "code.issue_labels observed")


def test_record_refuses_a_missing_or_failed_operation(fake, repo, workspace, home_env):
    setup(fake, repo, home_env)
    path = repo / "conformance" / "work-jira.json"
    walk(repo, "work", "jira", skip={"work.train_close"})
    proc = record(repo, workspace, home_env, "work", "jira")
    assert (proc.returncode, proc.stdout.strip()) == (1, "refused: no pass record for work/jira: missing work.train_close")
    assert not path.exists()
    trace(repo, "work", "jira", "work.train_close")
    trace(repo, "work", "jira", "work.comment", "fail")  # the latest line of an operation decides
    proc = record(repo, workspace, home_env, "work", "jira")
    assert (proc.returncode, proc.stdout.strip()) == (1, "refused: no pass record for work/jira: failed work.comment")
    assert not path.exists()
    trace(repo, "work", "jira", "work.comment")
    assert record(repo, workspace, home_env, "work", "jira").returncode == 0


def test_record_refuses_when_a_required_extension_is_absent(fake, repo, workspace, home_env):
    setup(fake, repo, home_env)
    fake.load()
    fake.state["extensions"] = EXTENSIONS[1:]
    fake.save()
    walk(repo, "signals", "newrelic")
    proc = record(repo, workspace, home_env, "signals", "newrelic")
    assert proc.stdout.strip() == "refused: extension newrelic required by signals/newrelic is not installed"
    assert not (repo / "conformance" / "signals-newrelic.json").exists()


def test_record_digest_is_the_guards_installed_tree_digest(fake, repo, workspace, home, home_env):
    commit = setup(fake, repo, home_env)
    walk(repo, "work", "jira")
    proc = record(repo, workspace, home_env, "work", "jira")
    assert proc.stdout.splitlines()[-1] == "work/jira supported", proc.stdout
    data = json.loads((repo / "conformance" / "work-jira.json").read_text())
    installed = home / "factory" / "adapters" / "work-jira"
    assert data["adapter_digest"] == fl.tree_digest(installed) == fg.installed_record(home)["adapters"]["work-jira"]["tree"]
    assert data == {"role": "work", "provider": "jira", "adapter_digest": data["adapter_digest"],
                    "environment": {"provider_site": "acme.atlassian.net", "extensions": {}, "factory_commit": commit},
                    "operations": {op: "pass" for op in fl.contract_operations("work")},
                    "trace": "conformance/work-jira.trace.jsonl", "outcome": "pass", "timestamp": data["timestamp"]}
    (installed / "SKILL.md").write_text((installed / "SKILL.md").read_text() + "\n")  # an installed tree drifts
    proc = record(repo, workspace, home_env, "work", "jira")
    assert proc.returncode == 1 and "tree" in proc.stdout and "run bin/install" in proc.stdout


def test_records_round_trip_through_attach(fake, repo, workspace, home_env):
    setup(fake, repo, home_env)
    sites = {}
    for role, provider in CONFIGURED:
        walk(repo, role, provider)
        proc = record(repo, workspace, home_env, role, provider)
        assert proc.returncode == 0 and proc.stdout.splitlines()[-1] == f"{role}/{provider} supported", proc.stdout
        sites[provider] = json.loads((repo / "conformance" / f"{role}-{provider}.json").read_text())["environment"]
    assert sites["newrelic"]["provider_site"] == "newrelic account 1234567 app acme-api-prod"
    assert sites["newrelic"]["extensions"] == {"newrelic": "0.9.0"}
    assert sites["github"]["provider_site"] == "github.com/acme/acme-api"
    assert sites["github-deployments"]["provider_site"].startswith("github.com/acme/acme-api environment ")
    proc = run("attach", str(workspace), env=home_env, repo_root=repo)
    assert proc.returncode == 0, proc.stdout
    assert "warning" not in proc.stdout and sum(x.startswith("attached ") for x in proc.stdout.splitlines()) == 6
