"""Agent content: UT-020, UT-021 (US-003) and the factory.toml contract of prompts and sources."""
import re
import subprocess
import sys

import factorylib as fl
from conftest import REPO, run

sys.path.insert(0, str(REPO / "tools"))
import factory_intake as fi  # noqa: E402

IDENTITY = {  # _dx.md › Agent identities
    "factory-sentinel": ("Factory Sentinel", "Reads the project's observability signals (errors, error logs, incidents), classifies each one and proposes to the operator which ones deserve an issue. Never opens issues or writes code."),
    "factory-scribe": ("Factory Scribe", "Turns findings and reports into deduplicated code issues and, when the item is actionable, into the project's work item with the facts its tracker requires."),
    "factory-dispatcher": ("Factory Dispatcher", "Accepts issues released with `factory:deliver`, sizes them, dispatches implementation to an isolated worktree and hands the PR to the PR watcher."),
    "factory-pr-watcher": ("Factory PR Watcher", "Follows executor PRs until they are green and reviewed, returns failures and findings to the executor, and hands ready heads to the gatekeeper."),
    "factory-gatekeeper": ("Factory Gatekeeper", "Revalidates ready PRs, merges them into the base branch, has the GitHub App open the promotion PR, merges it once the operator approves it on GitHub, and closes the work item after deploy."),
    "factory-auditor": ("Factory Auditor", "One window after deploy, checks in the project's observability whether a closed factory issue's problem is gone; records the evidence on the issue and its work item and reopens what came back."),
    "factory-reviewer": ("Factory Reviewer", "Reviews one factory PR head and posts the review through the GitHub App. Reads code only; never runs it."),
}
REVIEW_TOOLS = ["ext__github_app_review__pr_context", "ext__github_app_review__submit_review"]
DENYLIST = ["acme-reports", "acme-portal", "1234567", "ACME-", "acme",
            "codebuild-acme", "`staging`", "`production`"]
OLD_NAMES = re.compile(r"\b([Ss]entinela|[Ee]scriv[aã]o|[Mm]aestro|vigia-pr|[Pp]orteiro)\b|(?<!factory-)\bauditor\b")


def skill_text(name):
    return (REPO / "skills" / name / "SKILL.md").read_text()


def agent_text(name):
    return (REPO / "agents" / name / "AGENT.md").read_text()


def input_block(text):
    match = re.search(r"^## Input\n\n```text\n(.*?)\n```", text, re.S | re.M)
    return match.group(1).splitlines() if match else None


def test_ut_020_installed_files_carry_no_project_literals(home, home_env):
    proc = run("install", env=home_env)
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
    watcher = agent_text("factory-pr-watcher")  # hygiene through the code role, CI rule in the adapter
    assert "`code.check_hygiene`" in watcher and "base `{code.base}`" in watcher and "`ci_runner`" in watcher
    assert "runs-on: {ci_runner}" in skill_text("factory-code-github")
    assert "python3 ~/.compozy/factory/tools/factory_intake.py" in agent_text("factory-scribe")
    # C-1: the account is pinned on every query, now by the signals adapter the sentinel and auditor load
    assert "pass `account_id` on every NRQL call" in skill_text("factory-signals-newrelic")
    for name, op in (("factory-sentinel", "`signals.query_window`"), ("factory-auditor", "`signals.verify_gone`")):
        assert op in agent_text(name) and "factory_guard.py adapter" in agent_text(name), name
    gatekeeper = agent_text("factory-gatekeeper")  # C-2: merge commit only, through code.pr_merge
    assert gatekeeper.count("`code.pr_merge`") >= 3 and "gh pr merge" not in gatekeeper
    merge = skill_text("factory-code-github").split("## code.pr_merge")[1].split("## ")[0]
    assert "--merge --match-head-commit <head>" in merge and "never\n`--squash`, `--rebase`, `--auto` or `--admin`" in merge
    assert 'refused: factory.toml [roles.code] merge must be "merge"' in gatekeeper
    deploy_ok = gatekeeper.split("4. Deploy ok.")[1].split("\n5. ")[0]  # closed train issues lose factory:* state labels
    assert "`code.issue_labels` (remove only)" in deploy_ok and "keep `factory`)" in deploy_ok
    assert all(f"`factory:{s}`" in deploy_ok for s in ("in-progress", "blocked", "deliver"))
    assert set(fl.POSTS) == set(fi.TEXT), "attach and factory_intake.py support the same post languages"
    reviewer = agent_text("factory-reviewer")  # its grammar has no refused:, the missing config is a failed: code
    assert ".compozy/factory/factory.toml" in reviewer and "failed: #<pr> factory_toml_missing" in reviewer
    assert "{code.repo}" in reviewer and "{language.posts}" in reviewer


def test_reviewer_is_on_demand_with_only_the_review_tools():
    text = agent_text("factory-reviewer")
    assert fl.frontmatter_field(text, "kind") == "on_demand"
    assert (fl.frontmatter_field(text, "provider"), fl.frontmatter_field(text, "model")) == ("claude-reviewer", "opus")
    assert "fallback_chain:\n  - provider: claude-reviewer-fallback\n    model: opus\npermissions:" in text
    assert fl.frontmatter_field(text, "tools") == REVIEW_TOOLS
    for name in fl.AGENTS:
        assert fl.frontmatter_field(agent_text(name), "kind") == "always_on", name


def run_source(path, cwd, stdin="{}"):
    return subprocess.run([sys.executable, str(path)], cwd=cwd, input=stdin, capture_output=True, text=True)


def test_code_github_sources_read_only_role_keys(tmp_path):
    conf = tmp_path / ".compozy" / "factory"
    conf.mkdir(parents=True)
    (conf / "factory.toml").write_text('[autorizacao]\nautores = ["x"]\n[roles.code.github]\nrepo = "acme/acme-api"\n')
    sources = REPO / "skills" / "factory-code-github" / "sources"
    proc = run_source(sources / "deliver.py", tmp_path)
    assert proc.returncode == 1
    assert "refused: factory.toml missing [authorization] authors" in proc.stderr
    (conf / "factory.toml").write_text("[language]\nposts = 'en'\n[repo]\ngithub = 'acme/acme-api'\n")
    proc = run_source(sources / "due.py", tmp_path)
    assert proc.returncode == 1 and "refused: factory.toml missing [roles.code.github] repo" in proc.stderr


def test_ut_047_no_admin_bypass_in_any_agent():  # US-006.AC-3, BR-9, Safety Invariant 5
    hits = [str(p.relative_to(REPO)) for p in (REPO / "agents").rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and "--admin" in p.read_text(errors="replace")]
    assert hits == []


def test_watcher_reviews_each_head_and_gates_ready_on_merge_gate():  # US-004, US-006
    watcher = agent_text("factory-pr-watcher")
    gate = "`review.gate` with kind `delivery`"
    assert gate in watcher and "{review.agent}" not in watcher and "`[roles.review] provider`" in watcher
    assert "exactly `review: {code.repo}#<pr> <full-head-sha>`" in watcher and "`agent: factory-reviewer`" in watcher
    assert "`compozy__worktree_create`" in watcher and "timeout_seconds: 1800" in watcher
    assert "`review-<pr>-<sha12>-a<attempt>`" in watcher and "review-<pr>-<sha12>`" not in watcher  # retries get new keys
    assert watcher.index(gate) < watcher.index("exactly `ready: #<pr> <full-head-sha>`")
    assert fl.frontmatter_field(watcher, "tools") is None  # an allowlist would cut its other tools
    gatekeeper = agent_text("factory-gatekeeper")
    assert gate in gatekeeper and "`[roles.review] provider`" in gatekeeper
    assert "refused #<pr>: merge_gate <reasons comma-separated>" in gatekeeper
    assert gatekeeper.index(gate) < gatekeeper.index("## Merge into the base branch")
    assert "ext__github_app_review__merge_gate" in skill_text("factory-review-github-app-review")


def test_watcher_carries_one_contest_rereview_per_head():  # US-005, BR-3
    watcher = agent_text("factory-pr-watcher")
    assert "contest: #<pr> <head-sha>" in fl.frontmatter_field(watcher, "accepts")
    assert "exactly `rereview: {code.repo}#<pr> <full-head-sha>`" in watcher and "`rereview-<pr>-<sha12>-a<attempt>`" in watcher
    assert "contesting: #<pr> <head-sha>" in watcher.split("## Grammar")[1]
    for refusal in ("refused: contest only from the executor of #<pr>", "refused: contest #<pr> <head-sha> is not the head",
                    "refused: contest #<pr> already re-reviewed", "rereview_not_eligible"):
        assert refusal in watcher, refusal


GATEKEEPER_TOOLS = ["compozy__*", "ext__github_app_review__merge_gate", "ext__github_app_review__upsert_promotion"]


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
    assert fl.frontmatter_field(text, "accepts") == ["ready: #<pr> <head-sha>", "continue: #<pr> <merge-sha>"]
    assert "`code.promotion_upsert` with `key_pattern` = the `regex` of `work.key_format`" in text  # OQ-2
    assert "`ext__github_app_review__upsert_promotion` with `{\"repo\": \"{repo}\"}`, adding `\"key_pattern\"`" in skill_text(
        "factory-code-github")
    assert "`review.gate` with kind `promotion`" in text
    assert "`code.pr_merge` with head = that `head_sha` and expected base `{code.release}`" in text
    assert "`promotion_conflict`" in text and "`promotion_not_approved`" in text
    assert "gh pr create" not in text and "gh pr edit" not in text, "the app owns the promotion PR"
    assert "factory-deploy:" in text and "close by hand" in text


def test_watcher_review_retries_wake_on_a_timer_and_settle_reviewers():  # review-final G-2, G-5, G-6, G-14
    watcher = agent_text("factory-pr-watcher")
    assert "PR open and not draft" in watcher
    assert "`compozy__monitor_create kind: timer` of 5 min" in watcher and "pending `review` or `rereview` attempt" in watcher
    assert "`not_mergeable`, `history_incomplete`" in watcher and "5 min `kind: timer`" in watcher
    assert "`blocked: #<pr> review <code>`" in watcher and "`compozy__notify`" in watcher
    for code in ("credential_absent", "repo_not_installed", "config_invalid", "pr_not_reviewable", "factory_toml_missing"):
        assert code in watcher.split("**any other `failed: <code>`**")[1].split("\n")[0], code
    assert "`delete_branch: true`, `force: true`" in watcher and "`compozy__worktree_inspect`" in watcher
    assert watcher.count("settle any reviewer (step 5.4)") >= 3  # cap, merged, closed
    assert "Reviewer dispatch is exempt" in watcher.split("## Messages between agents")[1]


def test_watcher_counts_one_round_per_app_review_and_unblocks_approved_threads():  # review-final G-3, G-4, G-7, G-12
    watcher = agent_text("factory-pr-watcher")
    assert "**one** return and **one** round, whichever wake sees them first" in watcher
    assert "resolve each thread once fixed" in watcher and "they do not block" in watcher
    contest = watcher.split("## Contest")[1].split("## Grammar")[0]
    assert "at least 10 hex characters" in contest
    assert "answer `contesting: #<pr> <head-sha>` again, no round" in contest


def test_reviewer_ignores_conventions_changed_by_the_pr_it_reviews():  # review-final G-1, G-13
    text = agent_text("factory-reviewer")
    step = next(line for line in text.splitlines() if line.startswith("2. **Conventions.**"))
    assert "`pr_context.files` contains a protected path" in step
    assert all(f"`{path}`" in step for path in fl.PROTECTED_PATHS)
    assert "still judge it by the base rules" in step and "add a `major` finding" in step
    assert "Never read `REVIEW.md` or `factory.toml` from the worktree" in step
    assert "`pr_context.conventions`" in text, "rules come from the base branch, never the PR head"
    assert text.index("1. **Context.**") < text.index("2. **Conventions.**"), "conventions are known before they are applied"
    assert "every `renamed` one exists" in text


def test_gatekeeper_handles_every_upsert_promotion_failure():  # review-final G-8, G-10
    text = agent_text("factory-gatekeeper")
    assert "Cause `config_invalid`" in text and "do not retry" in text
    assert 'Any other cause (`rate_limited`' in text and '`kind: "timer"`' in text and "after 15 min" in text
    assert "mcp__" not in text and "`work.train_close`" in text, "work goes through the work adapter only"


def test_convention_change_finding_survives_contest_and_blocks():  # review-final G-15, G-17
    reviewer = agent_text("factory-reviewer").split("## Re-review")[1].split("## Errors")[0]
    assert "The step-2 convention/configuration finding is never dropped or lowered on re-review" in reviewer
    contest = agent_text("factory-pr-watcher").split("## Contest")[1].split("## Grammar")[0]
    assert "refused: contest #<pr> convention change needs the operator" in contest
    assert "`blocked: #<pr> convention change needs operator` to `agent: factory-dispatcher`" in contest
    assert "a rereview that settled as `rereview_not_eligible` posted none and does not count" in contest


def test_approved_unresolved_threads_rearm_a_timer_and_promotion_retries_notify_once():  # review-final G-16, G-18
    watcher = agent_text("factory-pr-watcher")
    line = next(l for l in watcher.splitlines() if l.lstrip().startswith("- `changes_requested`, `unresolved_threads`"))
    assert "5 min `kind: timer`" in line and "count no round" in line
    gatekeeper = agent_text("factory-gatekeeper")
    assert "once per cause" in gatekeeper and "retrying silently" in gatekeeper


def test_e2e_001_dispatch_and_review_launch_guards():  # E2E-001 findings, 2026-11-18
    dispatcher, watcher = agent_text("factory-dispatcher"), agent_text("factory-pr-watcher")
    fetch, create = dispatcher.index("git fetch origin {code.base}"), dispatcher.index("compozy__worktree_create`: name = entry ID")
    assert fetch < create, "the executor branch starts from a freshly fetched base"
    assert "never `compozy__session_spawn`" in watcher, "a spawned reviewer has no workspace paths and is refused"
    assert "run `code.pr_update_branch` yourself (no executor round; once per head)" in watcher


def test_gatekeeper_follows_the_deploy_through_github_deployments():  # E2E-003, 2026-11-18
    text = agent_text("factory-gatekeeper")
    step = text[text.index("3. **Follow the deploy"):text.index("4. Deploy ok.")]
    assert "`deploy.wait` for the merge commit in environment `{deploy.environment}`" in step
    assert "Never query the deploy platform itself" in step
    assert "aws " not in step and "codepipeline" not in step.lower(), "the deploy is read from the deploy role only"
    for outcome in ("`success`", "`failure` or `error`", "`absent`"):
        assert outcome in step
    adapter = skill_text("factory-deploy-github-deployments")
    assert "deployments?sha=<merge-sha>&environment=<env>" in adapter and "no Deployment exists\nafter 15 min" in adapter


def test_jira_fields_are_issue_facts_and_foreign_cards_are_left_to_the_operator():  # Q6 = a, Q7 = a, 2026-11-18
    scribe, gatekeeper = agent_text("factory-scribe"), agent_text("factory-gatekeeper")
    row = next(line for line in scribe.splitlines() if line.lstrip().startswith("- `attached_doc`:"))
    assert "A fact of the issue, not of its delivery" in row and "evidence in the body" in row
    assert "README" not in gatekeeper, "attached documentation is never derived from the PR diff"
    step = gatekeeper[gatekeeper.index("4. Deploy ok."):gatekeeper.index("5. Deploy failed")]
    assert "never re-derive them from the PR" in step
    assert "change neither its fields nor its status" in step and "step-6 notice" in step


def test_e2e_003_protected_path_pr_is_always_reviewed_before_blocking():  # E2E-003 PR #240, 2026-11-19
    watcher = agent_text("factory-pr-watcher")
    convention = "send `blocked: #<pr> convention change needs operator`"
    never = "never the convention-change reason, even for a protected-path PR"
    protected = watcher.split("## Protected paths")[1].split("## Input")[0]
    assert "It is still **always reviewed**" in protected and "never skip or short-circuit the reviewer dispatch" in protected
    procedure = watcher.split("## Procedure")[1].split("## Contest")[0]
    before_review = procedure[:procedure.index("5. **Review.**")]
    blocks = [l for l in before_review.splitlines() if "`blocked:" in l]
    assert len(blocks) == 1 and "On the **5th** round without green" in blocks[0] and never in blocks[0], \
        "the only block before step 5 is the round cap, under its own reason"
    review = procedure[procedure.index("5. **Review.**"):procedure.index("6. **Ready.**")]
    dispatch, wake = review.split("   2. ", 1)[1].split("   3. ", 1)
    assert "`compozy__session_create` with `agent: factory-reviewer`" in dispatch
    assert "first `compozy__session_prompt` exactly `review: {code.repo}#<pr> <full-head-sha>`" in dispatch
    guard = next(l for l in dispatch.splitlines() if "**Reviewer guard" in l)
    assert "`blocked: #<pr> review reviewer_untrusted`" in guard and never in guard
    assert convention not in dispatch
    wake = wake.split("   4. ", 1)[0]
    assert wake.count(convention) == 1 and procedure.count(convention) == 1, "the procedure blocks a convention change only on the reviewer wake"
    assert "the line is `reviewed: …` for the current head (either verdict)" in wake[:wake.index(convention)]
    assert "is not a review and follows the list below like any PR" in wake
    contest = watcher.split("## Contest")[1].split("## Grammar")[0]
    assert contest.index("has no app review on the head") < contest.index(convention), "the contest blocks only after an app review on the head"
