"""Agent content: UT-020, UT-021 (US-003) and the factory.toml contract of prompts and sources."""
import re
import subprocess
import sys

import factorylib as fl
from conftest import REPO, run

sys.path.insert(0, str(REPO / "tools"))
import factory_intake as fi  # noqa: E402

IDENTITY = {  # _dx.md › Agent identities
    "factory-sentinel": ("Factory Sentinel", "Reads New Relic errors, error logs and incidents for the project's app, classifies each signal and proposes to the operator which ones deserve an issue. Never opens issues or writes code."),
    "factory-scribe": ("Factory Scribe", "Turns findings and reports into deduplicated GitHub issues and, when the item is actionable, into the official Jira work item with the required fields."),
    "factory-dispatcher": ("Factory Dispatcher", "Accepts issues released with `factory:deliver`, sizes them, dispatches implementation to an isolated worktree and hands the PR to the PR watcher."),
    "factory-pr-watcher": ("Factory PR Watcher", "Follows executor PRs until they are green and reviewed, returns failures and findings to the executor, and hands ready heads to the gatekeeper."),
    "factory-gatekeeper": ("Factory Gatekeeper", "Revalidates ready PRs, merges them into the base branch, has the GitHub App open the promotion PR, merges it once the operator approves it on GitHub, and closes the Jira item after deploy."),
    "factory-auditor": ("Factory Auditor", "One window after deploy, checks in New Relic whether a closed factory issue's problem is gone; records the evidence on the issue and in Jira and reopens what came back."),
    "factory-reviewer": ("Factory Reviewer", "Reviews one factory PR head and posts the review through the GitHub App. Reads code only; never runs it."),
}
REVIEW_TOOLS = ["ext__github_app_review__pr_context", "ext__github_app_review__submit_review"]
DENYLIST = ["acme-reports", "acme-portal", "1234567", "ACME-", "acme",
            "codebuild-acme", "`staging`", "`production`"]
OLD_NAMES = re.compile(r"\b([Ss]entinela|[Ee]scriv[aã]o|[Mm]aestro|vigia-pr|[Pp]orteiro)\b|(?<!factory-)\bauditor\b")


def agent_text(name):
    return (REPO / "agents" / name / "AGENT.md").read_text()


def input_block(text):
    match = re.search(r"^## Input\n\n```text\n(.*?)\n```", text, re.S | re.M)
    return match.group(1).splitlines() if match else None


def test_ut_020_installed_files_carry_no_project_literals(home):
    proc = run("install", env={"COMPOZY_HOME": str(home)})
    assert proc.returncode == 0, proc.stderr
    hits = []
    for path in sorted(p for p in home.rglob("*") if p.is_file()):
        text = path.read_text(errors="replace")
        hits += [f"{path.relative_to(home)}: {word}" for word in DENYLIST if word in text]
    assert hits == []
    sentinel = (home / "agents" / "factory-sentinel" / "HEARTBEAT.md").read_text()
    assert "account_id" not in sentinel
    assert "where: \"appName = '{{app}}'\"" in sentinel
    assert "where: \"entity.name = '{{app}}' AND level IN ('ERROR', 'error', 'CRITICAL', 'critical')\"" in sentinel


def test_ut_021_identity_accepts_and_peer_names():
    assert sorted(p.name for p in (REPO / "agents").iterdir()) == sorted(fl.AGENTS + fl.ON_DEMAND)
    for name, (display, description) in IDENTITY.items():
        text = agent_text(name)
        assert fl.frontmatter_field(text, "name") == name
        assert fl.frontmatter_field(text, "display_name") == display
        assert fl.frontmatter_field(text, "description") == description and len(description) <= 280
        accepts = fl.frontmatter_field(text, "accepts")
        assert accepts == input_block(text), f"{name}: accepts must mirror ## Input"
        assert accepts is not None or name == "factory-sentinel", f"{name}: only the sentinel has no accepts"
        assert accepts is None or len(accepts) <= 8 and all(len(a) <= 160 for a in accepts)
        for path in (REPO / "agents" / name).rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                assert not OLD_NAMES.search(path.read_text()), f"old agent name in {path}"
        peers = re.findall(r"agent: `?([\w-]+)", text)
        assert all(p.startswith("factory-") for p in peers if p != "code_implementer"), (name, peers)


def test_prompts_read_factory_toml_and_refuse_on_missing_keys():
    for name in fl.AGENTS:
        text = agent_text(name)
        assert ".compozy/factory/factory.toml" in text
        assert "refused: factory.toml missing" in text
    for name in ("factory-scribe", "factory-dispatcher", "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"):
        assert "{language.posts}" in agent_text(name), name
    watcher = agent_text("factory-pr-watcher")
    assert "runs-on: {ci.runner}" in watcher and "base `{repo.base}`" in watcher
    assert "python3 ~/.compozy/factory/tools/factory_intake.py" in agent_text("factory-scribe")
    for name in ("factory-sentinel", "factory-auditor"):  # C-1: the account is pinned on every NRQL call
        assert "`[newrelic] account_id`" in agent_text(name) and "account `{newrelic.account_id}`" in agent_text(name)
    gatekeeper = agent_text("factory-gatekeeper")  # C-2: merge commit only
    assert "--{repo.merge}" not in gatekeeper and gatekeeper.count("--merge --match-head-commit") == 3
    assert 'refused: factory.toml [repo] merge must be "merge"' in gatekeeper
    assert set(fl.POSTS) == set(fi.TEXT), "attach and factory_intake.py support the same post languages"
    reviewer = agent_text("factory-reviewer")  # its grammar has no refused:, the missing config is a failed: code
    assert ".compozy/factory/factory.toml" in reviewer and "failed: #<pr> factory_toml_missing" in reviewer
    assert "{review.repo}" in reviewer and "{language.posts}" in reviewer


def test_reviewer_is_on_demand_with_only_the_review_tools():
    text = agent_text("factory-reviewer")
    assert fl.frontmatter_field(text, "kind") == "on_demand"
    assert (fl.frontmatter_field(text, "provider"), fl.frontmatter_field(text, "model")) == ("claude-reviewer", "claude-opus-5-5")
    assert "fallback_chain:\n  - provider: claude-reviewer-fallback\n    model: claude-opus-5-5\npermissions:" in text
    assert fl.frontmatter_field(text, "tools") == REVIEW_TOOLS
    for name in fl.AGENTS:
        assert fl.frontmatter_field(agent_text(name), "kind") == "always_on", name


def run_source(path, cwd, stdin="{}"):
    return subprocess.run([sys.executable, str(path)], cwd=cwd, input=stdin, capture_output=True, text=True)


def test_sources_read_only_new_keys(tmp_path):
    conf = tmp_path / ".compozy" / "factory"
    conf.mkdir(parents=True)
    (conf / "factory.toml").write_text('[autorizacao]\nautores = ["x"]\n[repo]\ngithub = "acme/acme-api"\n')
    deliver = REPO / "agents" / "factory-dispatcher" / "sources" / "deliver"
    proc = run_source(deliver, tmp_path)
    assert proc.returncode == 1
    assert "refused: factory.toml missing [authorization] authors" in proc.stderr
    (conf / "factory.toml").write_text("[language]\nposts = 'en'\n")
    proc = run_source(REPO / "agents" / "factory-auditor" / "sources" / "due", tmp_path)
    assert proc.returncode == 1 and "refused: factory.toml missing [repo] github" in proc.stderr


def test_ut_047_no_admin_bypass_in_any_agent():  # US-006.AC-3, BR-9, Safety Invariant 5
    hits = [str(p.relative_to(REPO)) for p in (REPO / "agents").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and "--admin" in p.read_text(errors="replace")]
    assert hits == []


def test_watcher_reviews_each_head_and_gates_ready_on_merge_gate():  # US-004, US-006
    watcher = agent_text("factory-pr-watcher")
    gate = '`ext__github_app_review__merge_gate` with `{"repo":"{review.repo}","number":<n>,"kind":"delivery"}`'
    assert gate in watcher and "`[review] repo`, `[review] agent`" in watcher
    assert "exactly `review: #<pr> <full-head-sha>`" in watcher and "`agent: {review.agent}`" in watcher
    assert "`compozy__worktree_create`" in watcher and "timeout_seconds: 1800" in watcher
    assert "`review-<pr>-<sha12>-a<attempt>`" in watcher and "review-<pr>-<sha12>`" not in watcher  # retries get new keys
    assert watcher.index("merge_gate") < watcher.index("exactly `ready: #<pr> <full-head-sha>`")
    assert fl.frontmatter_field(watcher, "tools") is None  # an allowlist would cut its other tools
    gatekeeper = agent_text("factory-gatekeeper")
    assert gate in gatekeeper and "`[review] repo`" in gatekeeper
    assert "refused #<pr>: merge_gate <reasons comma-separated>" in gatekeeper
    assert gatekeeper.index("merge_gate") < gatekeeper.index("## Merge into the base branch")


def test_watcher_carries_one_contest_rereview_per_head():  # US-005, BR-3
    watcher = agent_text("factory-pr-watcher")
    assert "contest: #<pr> <head-sha>" in fl.frontmatter_field(watcher, "accepts")
    assert "exactly `rereview: #<pr> <full-head-sha>`" in watcher and "`rereview-<pr>-<sha12>-a<attempt>`" in watcher
    assert "contesting: #<pr> <head-sha>" in watcher.split("## Grammar")[1]
    for refusal in ("refused: contest only from the executor of #<pr>", "refused: contest #<pr> <head-sha> is not the head",
                    "refused: contest #<pr> already re-reviewed", "rereview_not_eligible"):
        assert refusal in watcher, refusal


GATEKEEPER_TOOLS = ["compozy__*", "mcp__atlassian__*", "ext__newrelic__*",
                    "ext__github_app_review__merge_gate", "ext__github_app_review__upsert_promotion"]


def test_gatekeeper_tool_allowlist_and_submit_review_only_for_the_reviewer():  # Config Lifecycle: tools: grants writes
    assert fl.frontmatter_field(agent_text("factory-gatekeeper"), "tools") == GATEKEEPER_TOOLS
    listing = [n for n in fl.AGENTS + fl.ON_DEMAND
               if "ext__github_app_review__submit_review" in (fl.frontmatter_field(agent_text(n), "tools") or [])]
    assert listing == ["factory-reviewer"]
    upserting = [n for n in fl.AGENTS + fl.ON_DEMAND
                 if "ext__github_app_review__upsert_promotion" in (fl.frontmatter_field(agent_text(n), "tools") or [])]
    assert upserting == ["factory-gatekeeper"]


def test_gatekeeper_promotes_through_the_app_and_deploys_on_github_approval():  # US-007, BR-8, ADR-003
    text = agent_text("factory-gatekeeper")
    assert "aprovado" not in text, "approval is the operator's GitHub review, never a message"
    assert fl.frontmatter_field(text, "accepts") == ["ready: #<pr> <head-sha>"]
    assert '`ext__github_app_review__upsert_promotion` with `{"repo":"{review.repo}"}`' in text
    assert '{"repo":"{review.repo}","number":<number>,"kind":"promotion"}' in text
    assert "--merge --match-head-commit <head_sha>" in text
    assert "`promotion_conflict`" in text and "`promotion_not_approved`" in text
    assert "gh pr create" not in text and "gh pr edit" not in text, "the app owns the promotion PR"
    assert "factory-deploy:" in text and "close by hand" in text
