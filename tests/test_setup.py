"""bin/setup (US-011, US-013 preflight marks): UT-025, UT-027, UT-028, UT-044, UT-046, UT-055 and BR-11.

CompozyOS is faked at the subprocess boundary (tests/conftest.py, with github-app-review's `policy` status,
`secrets set`, restart and `pr_context` answered from the staged version files) and `gh` by a logging fake.
COMPOZY_HOME comes from a real bin/install (`installed`); the policy working copy lives under tmp_path.
Repo A (acme/other-api) is served by the approved version V1; setup onboards B (acme/acme-api).
"""
import hashlib
import json
import os
import shutil
import subprocess

import factorylib as fl
import pytest
from conftest import REPO, run, run_tty

FAKE_GH = r'''#!/usr/bin/env python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
state = json.load(open(os.environ["FAKE_GH_STATE"]))
if " ".join(args[:2]) in state.get("fail", []):
    sys.exit("fake gh: injected failure")
if args[:2] == ["label", "list"]:
    print(json.dumps([{"name": n} for n in state["labels"]]))
elif args[:2] == ["label", "create"]:
    state["labels"].append(args[2])
elif args[:2] == ["pr", "list"] and "--head" in args:
    print(json.dumps(state["open"]))
elif args[:2] == ["pr", "list"]:
    print(json.dumps(state["prs"].get(args[args.index("--repo") + 1], [])))
elif args[:2] == ["pr", "create"]:
    state["open"] = [{"number": 241}]
    print("https://github.com/acme/acme-api/pull/241")
else:
    sys.exit(f"fake gh: unhandled {args}")
json.dump(state, open(os.environ["FAKE_GH_STATE"], "w"))
'''
V1_POLICY = ('[repos."acme/other-api"]\ndelivery_base = "develop"\npromotion = { head = "develop", base = "main" }\n'
             'approvers = ["octocat"]\n')
ANSWERS = "acme-api-prod\n1234567\noctocat\ny\n"  # New Relic app, account, promotion approvers, confirmation
READ_ONLY = (["workspace", "info"], ["extension", "list"],
             ["extension", "exec", "github-app-review", "--cmd", "policy"], ["agent", "info"],
             ["agent", "instance", "list"], ["agent", "heartbeat", "sources"], ["skill", "info"])


@pytest.fixture
def env(fake, installed, tmp_path):
    """Fake compozy and gh, extensions active, V1 staged and active for repo A, empty project directory."""
    (fake.dir / "gh").write_text(FAKE_GH)
    (fake.dir / "gh").chmod(0o755)
    gh_state, gh_log = tmp_path / "gh.json", tmp_path / "gh.jsonl"
    gh_state.write_text(json.dumps({"labels": [], "open": [], "prs": {"acme/other-api": [{"number": 7}]}}))
    gh_log.write_text("")
    cfg = tmp_path / "cfg"
    cfg.mkdir()
    (cfg / "policy.toml").write_text(V1_POLICY)
    policy_env = {"GITHUB_APP_POLICY_FILE": str(cfg / "policy.toml")}
    _, v1, _ = fl.stage_policy(policy_env)
    fake.load()
    fake.state["extensions"] = [{"name": "newrelic", "version": "0.4.0", "state": "active"},
                                {"name": "github-app-review", "version": "0.3.0", "state": "active"}]
    fake.state["policy"] = {"secret": v1, "running": v1}
    fake.save()
    git_env = {"GIT_CONFIG_GLOBAL": str(tmp_path / "gitconfig"), "GIT_CONFIG_NOSYSTEM": "1"}
    (tmp_path / "gitconfig").write_text("[user]\n\tname = Test\n\temail = test@example.invalid\n"
                                        "[init]\n\tdefaultBranch = develop\n")
    root, origin = tmp_path / "acme-api", tmp_path / "origin.git"
    for args in (["init", "-q", "--bare", str(origin)], ["init", "-q", str(root)]):
        git(git_env, *args)
    (root / "README.md").write_text("# acme-api\n")
    for args in (["add", "."], ["commit", "-q", "-m", "init"], ["remote", "add", "origin", str(origin)],
                 ["push", "-q", "origin", "HEAD:develop"]):
        git(git_env, "-C", str(root), *args)
    fake.log_path.write_text("")  # drop bin/install's read-back calls
    return {**installed, **policy_env, **git_env, "FAKE_GH_STATE": str(gh_state), "FAKE_GH_LOG": str(gh_log),
            "V1": v1, "ORIGIN": str(origin)}


def git(env, *args):
    return subprocess.run(["git", *args], capture_output=True, text=True, check=True,
                          env={**{k: v for k, v in os.environ.items() if not k.startswith("GIT_")}, **env}).stdout


@pytest.fixture
def project(tmp_path):
    return tmp_path / "acme-api"


def setup(project, env, answers=ANSWERS, tty=True, repo_root=REPO):
    args = (str(project), "--from", str(REPO / "factory.example.toml"))
    if tty:
        return run_tty("setup", *args, env=env, answer=answers, repo_root=repo_root)
    return run("setup", *args, env=env, stdin=answers, repo_root=repo_root)


def gh_calls(env):
    return [json.loads(c) for c in open(env["FAKE_GH_LOG"]).read().splitlines()]


def mutations(fake):
    return [c for c in fake.calls if not any(c[:len(r)] == r for r in READ_ONLY)]


def snapshot(*dirs):
    return {str(p): p.read_bytes() for d in dirs for p in sorted(d.rglob("*")) if p.is_file()}


def policy_dir(env):
    return fl.policy_paths(env)[0].parent


def test_ut_025_fresh_project_writes_files_creates_labels_and_attaches(fake, env, project):
    proc = setup(project, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert (project / ".compozy/factory/factory.toml").read_bytes() == (REPO / "factory.example.toml").read_bytes()
    assert (project / ".compozy/newrelic.toml").read_text() == 'app = "acme-api-prod"\naccount_id = 1234567\n'
    assert (project / "REVIEW.md").read_text().startswith("# Review conventions")
    fl.load_factory_toml(project)
    assert [c[2] for c in gh_calls(env) if c[:2] == ["label", "create"]] == [
        "factory", "factory:deliver", "factory:in-progress", "factory:blocked"]
    assert [c[3] for c in fake.calls if c[:3] == ["agent", "instance", "create"]] == fl.AGENTS
    assert sum(c[:2] == ["session", "prompt"] for c in fake.calls) == 6  # one boundary turn per new instance
    out = proc.stdout
    for text in ("factory    .compozy/factory/factory.toml written", "newrelic   .compozy/newrelic.toml written",
                 "review     REVIEW.md written from template (edit it)", "labels     created factory, factory:deliver",
                 "attach     boundary factory-auditor ok", "todo       App installation includes acme/acme-api",
                 "todo       the deploy tool reports GitHub Deployments for environment main"):
        assert text in out, out
    # the preflight came first, the confirmation before any mutation
    assert (out.index("preflight  attach 6 agents") < out.index("apply all of the above? [y/N]")
            < out.index("policy     staged"))


def test_s6_2_files_go_out_as_a_reviewed_pr_and_are_staged_in_the_checkout(fake, env, project):
    proc = setup(project, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    assert "preflight  checkout on branch develop" in out
    assert "preflight  open a PR from branch factory/setup into develop with these files" in out
    assert "branch     factory/setup from origin/develop, pushed" in out
    assert "pr         #241 opened into develop (protected paths: review it and merge it outside the factory)" in out
    assert "todo       review and merge PR #241 outside the factory (protected paths)" in out
    files = (".compozy/factory/factory.toml", ".compozy/newrelic.toml", "REVIEW.md")
    for rel in files:  # the branch carries exactly the files written to the checkout
        assert git(env, "--git-dir", env["ORIGIN"], "show", f"factory/setup:{rel}") == (project / rel).read_text()
    assert git(env, "-C", str(project), "diff", "--cached", "--name-only").split() == sorted(files)
    create = next(c for c in gh_calls(env) if c[:2] == ["pr", "create"])
    assert create[create.index("--base") + 1] == "develop" and create[create.index("--head") + 1] == "factory/setup"
    # PR before the checkout write, then labels, then attach
    assert out.index("pr         #241") < out.index("factory    .compozy/factory/factory.toml written") \
        < out.index("labels     created") < out.index("attach     attached")


def test_ut_027_policy_switch_keeps_repo_a_served_and_second_run_is_policy_ok(fake, env, project):
    v1 = env["V1"]
    proc = setup(project, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    working = fl.policy_paths(env)[0]
    v2 = hashlib.sha256(working.read_bytes()).hexdigest()
    assert v2 != v1 and '[repos."acme/acme-api"]\n' in working.read_text()
    assert 'approvers = ["octocat"]' in working.read_text()
    assert (policy_dir(env) / "policies" / f"{v2}.toml").read_bytes() == working.read_bytes()
    assert (policy_dir(env) / "policies" / f"{v1}.toml").is_file()  # V1 stays on disk
    state = fake.load()["policy"]
    assert (state["secret"], state["running"]) == (v2, v2)
    out = proc.stdout
    assert (f"preflight  policy: active digest changes {v1[:12]} -> {v2[:12]} "
            "(repos kept: acme/other-api; added: acme/acme-api)") in out
    assert f"policy     GITHUB_APP_POLICY_DIGEST set; active digest verified {v2[:12]} (old version kept)" in out
    assert "policy     acme/other-api still served (pr_context smoke ok)" in out
    # order: stage → set → (stale) → restart → verified → smoke → project files
    names = [" ".join(c[:3]) for c in fake.calls]
    order = ["extension secrets set", "extension disable github-app-review", "extension enable github-app-review",
             "tool invoke ext__github_app_review__pr_context"]
    assert [names.index(n) for n in order] == sorted(names.index(n) for n in order)
    assert out.index("still served") < out.index(".compozy/factory/factory.toml written")
    smoke = run_cz(env, "tool", "invoke", "ext__github_app_review__pr_context", "--input",
                   json.dumps({"repo": "acme/other-api", "number": 7}))
    assert smoke.returncode == 0  # repo A right after the switch

    versions = sorted((policy_dir(env) / "policies").iterdir())
    again = setup(project, env, answers="")
    assert again.returncode == 0, again.stdout
    assert f"preflight  policy ok (acme/acme-api served by the active version {v2[:12]})" in again.stdout
    assert sorted((policy_dir(env) / "policies").iterdir()) == versions


def run_cz(env, *args):
    return subprocess.run(["compozy", *args], capture_output=True, text=True, env={**os.environ, **env})


def test_ut_028_rerun_on_a_configured_project_changes_nothing(fake, env, project):
    assert setup(project, env).returncode == 0
    before = snapshot(project, policy_dir(env))
    fake.log_path.write_text("")
    open(env["FAKE_GH_LOG"], "w").close()
    proc = setup(project, env, answers="", tty=False)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines()[-1] == "setup      nothing to apply: every step ok/exists"
    for text in ("preflight  exists .compozy/factory/factory.toml", "preflight  exists .compozy/newrelic.toml",
                 "preflight  exists REVIEW.md", "preflight  labels exist", "preflight  policy ok",
                 "preflight  attach ok (instances exist)"):
        assert text in proc.stdout, proc.stdout
    assert "write " not in proc.stdout and "apply all" not in proc.stdout
    assert snapshot(project, policy_dir(env)) == before
    assert mutations(fake) == []
    assert [c[:2] for c in gh_calls(env)] == [["label", "list"]]


def test_ut_044_policy_needed_without_tty_refuses_with_zero_mutations(fake, env, project):
    before = snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"]))
    proc = setup(project, env, answers="acme-api-prod\n1234567\n", tty=False)
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1] == "refused: policy confirmation needs an interactive terminal"
    assert "preflight  policy: setup adds this block to the working copy" in proc.stdout
    assert 'approvers = <asked at the terminal>' in proc.stdout
    assert mutations(fake) == []
    assert gh_calls(env) == []
    assert snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"])) == before


def test_ut_046_preflight_marks_each_protected_write(fake, env, project):
    proc = setup(project, env, answers="acme-api-prod\n1234567\n", tty=False)
    marked = [line for line in proc.stdout.splitlines() if line.endswith("(protected path)")]
    assert [line.split()[2] for line in marked] == [".compozy/factory/factory.toml", ".compozy/newrelic.toml",
                                                    "REVIEW.md"]
    assert all(line.startswith("preflight  write ") for line in marked)
    assert fl.is_protected(".compozy/profiles/default/agents/factory-reviewer/AGENT.md")  # same list, UT-071


def test_ut_055_unverified_switch_sets_the_digest_back_and_writes_no_project_file(fake, env, project):
    fake.load()
    fake.state["policy"]["stuck"] = True
    fake.save()
    proc = setup(project, env)
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout.splitlines()[-1] == ("refused: policy switch not verified (active digest not verified after a "
                                            "restart); no project file written")
    sets = [c for c in fake.calls if c[:3] == ["extension", "secrets", "set"]]
    assert len(sets) == 2
    assert_rolled_back(fake, env, project, proc)


def assert_rolled_back(fake, env, project, proc):
    """Digest back on V1 and verified, extension enabled, no project file, no PR, label or instance; exit 1."""
    assert proc.returncode == 1, proc.stdout
    assert f"policy     GITHUB_APP_POLICY_DIGEST set back to {env['V1'][:12]}; verified active, extension enabled" \
        in proc.stdout, proc.stdout
    state = fake.load()["policy"]
    assert (state["secret"], state["running"], state.get("enabled", True)) == (env["V1"], env["V1"], True)
    assert not (project / ".compozy").exists() and not (project / "REVIEW.md").exists()
    assert not [c for c in gh_calls(env) if c[:2] in (["label", "create"], ["pr", "create"])]
    assert not [c for c in fake.calls if c[:3] == ["agent", "instance", "create"]]
    smoke = run_cz(env, "tool", "invoke", "ext__github_app_review__pr_context", "--input",
                   json.dumps({"repo": "acme/other-api", "number": 7}))
    assert smoke.returncode == 0  # repo A still served


def inject(fake, **state):
    fake.load()
    fake.state.update(state)
    fake.save()


def test_s6_1_gh_pr_list_failure_during_the_smoke_rolls_back(fake, env, project):
    gh_state = json.loads(open(env["FAKE_GH_STATE"]).read())
    gh_state["fail"] = ["pr list"]
    open(env["FAKE_GH_STATE"], "w").write(json.dumps(gh_state))
    proc = setup(project, env)
    assert proc.stdout.splitlines()[-1].startswith("refused: policy switch not verified (gh pr list")
    assert_rolled_back(fake, env, project, proc)


def test_s6_1_extension_enable_failure_after_the_restart_rolls_back_and_reenables(fake, env, project):
    inject(fake, fail={"extension enable": 1})  # the restart's enable fails once, leaving the extension disabled
    proc = setup(project, env)
    assert "injected failure extension enable" in proc.stdout.splitlines()[-1]
    enables = [c for c in fake.calls if c[:2] == ["extension", "enable"]]
    assert len(enables) == 2  # the failed one and the rollback's
    assert_rolled_back(fake, env, project, proc)


def test_s6_1_policy_status_failure_after_secrets_set_rolls_back(fake, env, project):
    inject(fake, fail_after_set={"extension exec": 1})
    proc = setup(project, env)
    assert "injected failure extension exec" in proc.stdout.splitlines()[-1]
    assert_rolled_back(fake, env, project, proc)


def test_s6_1_failed_rollback_prints_the_manual_commands(fake, env, project):
    inject(fake, fail={"extension enable": 5})
    proc = setup(project, env)
    assert proc.returncode == 1
    assert "policy     GITHUB_APP_POLICY_DIGEST set back NOT verified" in proc.stdout
    assert f"             printf {env['V1']} | compozy extension secrets set github-app-review" in proc.stdout
    assert "             compozy extension enable github-app-review" in proc.stdout
    assert not (project / ".compozy").exists()


def test_s6_3_preflight_shows_the_exact_block_of_every_added_repository(fake, env, project):
    working = fl.policy_paths(env)[0]
    extra = '[repos."acme/c"]\ndelivery_base = "main"\napprovers = ["mallory"]\n'
    working.write_text(V1_POLICY + "\n" + extra)
    proc = setup(project, env, answers="acme-api-prod\n1234567\noctocat\nn\n")
    out = proc.stdout
    assert "preflight  policy: this block, already in the working copy" in out
    assert "\n".join("             " + t for t in extra.splitlines()) in out
    assert "preflight  policy: setup adds this block to the working copy" in out
    assert "added: acme/acme-api, acme/c)" in out
    # the onboarded repo already in the working copy: its exact block is shown too
    working.write_text(V1_POLICY + "\n" + fl.policy_block("acme/acme-api", "develop", "main", ["hubot"]))
    out = setup(project, env, answers="acme-api-prod\n1234567\nn\n").stdout
    assert ("preflight  policy: this block, already in the working copy" in out
            and '             approvers = ["hubot"]' in out)


def test_s6_4_a_working_copy_edited_during_the_prompt_is_refused(fake, env, project):
    working = fl.policy_paths(env)[0]
    import pty
    full = {**{k: v for k, v in os.environ.items() if not k.startswith("COMPOZY_")}, **env}
    master, slave = pty.openpty()
    argv = [str(REPO / "bin" / "setup"), str(project), "--from", str(REPO / "factory.example.toml")]
    proc = subprocess.Popen(argv, stdin=slave, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=full)
    os.close(slave)
    os.write(master, b"acme-api-prod\n1234567\noctocat\n")
    seen = ""
    while not seen.endswith("[y/N] "):  # the preflight is done and waits at the confirmation
        seen += proc.stdout.read(1) or pytest.fail(seen)
    working.write_text(working.read_text() + "# edited meanwhile\n")
    os.write(master, b"y\n")
    out, _ = proc.communicate(timeout=60)
    os.close(master)
    assert proc.returncode == 1
    assert out.splitlines()[-1] == (f"refused: the policy working copy {working} changed during setup; "
                                    "nothing staged or switched")
    assert working.read_text().endswith("# edited meanwhile\n")
    assert fake.load()["policy"]["secret"] == env["V1"] and not (project / ".compozy").exists()


def test_s6_5_served_repo_with_another_promotion_is_refused(fake, env, project):
    working = fl.policy_paths(env)[0]
    working.write_text(V1_POLICY + "\n" + fl.policy_block("acme/acme-api", "develop", "release", ["octocat"]))
    _, v, _ = fl.stage_policy(env)
    inject(fake, policy={"secret": v, "running": v})
    proc = setup(project, env, answers="acme-api-prod\n1234567\n", tty=False)
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1] == (
        "refused: the active policy has delivery_base develop, promotion develop -> release for acme/acme-api; "
        "factory.toml gives base develop, promotion develop -> main; edit the working copy by hand, then run "
        "bin/stage-policy")


def test_s6_6_eof_at_the_approvers_prompt_refuses_without_traceback(fake, env, project):
    proc = setup(project, env, answers="acme-api-prod\n1234567\n\x04")
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1].endswith(": refused: approvers: no answer (end of input)")
    assert "Traceback" not in proc.stderr


def test_br_11_unsupported_adapter_refused_in_preflight_with_zero_mutations(fake, env, project, repo):
    shutil.rmtree(repo / "conformance", ignore_errors=True)  # this checkout ships no conformance records
    before = snapshot(project, policy_dir(env))
    proc = setup(project, env, repo_root=repo)
    assert proc.returncode == 1
    assert proc.stdout.splitlines()[-1].startswith("refused: adapter signals/newrelic is not supported "
                                                   "(conformance record missing); pass --allow-unsupported-adapter")
    assert mutations(fake) == [] and gh_calls(env) == [] and snapshot(project, policy_dir(env)) == before


def test_from_never_overwrites_an_existing_factory_toml(fake, env, project):
    toml = project / ".compozy/factory/factory.toml"
    toml.parent.mkdir(parents=True)
    toml.write_text("[language]\nposts = \"en\"\n")
    proc = setup(project, env, tty=False)
    assert proc.returncode == 1
    assert proc.stdout.strip().endswith("differs from " + str(REPO / "factory.example.toml")
                                        + "; setup never rewrites it (change it in a PR)")
    assert toml.read_text() == "[language]\nposts = \"en\"\n" and fake.calls == []


def assert_refused_in_preflight(fake, env, project, proc, before, last):
    """Refused before the confirmation: policy still V1, no mutation, no gh call, nothing written."""
    assert proc.returncode == 1, proc.stdout
    assert proc.stdout.splitlines()[-1] == last
    assert "apply all of the above" not in proc.stdout
    assert fake.load()["policy"]["secret"] == env["V1"] and mutations(fake) == []
    assert not [c for c in gh_calls(env) if c[:2] != ["label", "list"]]
    assert snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"])) == before


def test_s6_8_review_md_differing_on_origin_base_refuses_in_preflight(fake, env, project, tmp_path):
    other = tmp_path / "other"
    git(env, "clone", "-q", env["ORIGIN"], str(other))
    (other / "REVIEW.md").write_text("# Someone else's review rules\n")
    git(env, "-C", str(other), "add", "REVIEW.md")
    git(env, "-C", str(other), "commit", "-q", "-m", "review rules")
    git(env, "-C", str(other), "push", "-q", "origin", "HEAD:develop")
    git(env, "-C", str(project), "fetch", "-q", "origin")  # origin/develop has it; the checkout is behind
    before = snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"]))
    proc = setup(project, env)
    assert_refused_in_preflight(fake, env, project, proc, before, (
        "refused: REVIEW.md exists on origin/develop but not in the checkout; update the checkout (git pull), "
        "then re-run"))


def test_s6_8_setup_branch_with_other_files_on_origin_refuses_in_preflight(fake, env, project):
    git(env, "-C", str(project), "push", "-q", "origin", "HEAD:refs/heads/factory/setup")
    before = snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"]))
    proc = setup(project, env)
    assert_refused_in_preflight(fake, env, project, proc, before, (
        "refused: branch factory/setup already exists on origin with other files (its PR is pending: merge it, "
        "or delete the branch to start over)"))


def test_s6_9_checkout_off_base_or_with_staged_changes_refuses_in_preflight(fake, env, project):
    git(env, "-C", str(project), "checkout", "-q", "-b", "feature")
    before = snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"]))
    proc = setup(project, env)
    assert "preflight  checkout on branch feature" in proc.stdout
    assert_refused_in_preflight(fake, env, project, proc, before, (
        "refused: the checkout is on feature, not the delivery base develop; check out develop (setup stages the "
        "files there), then re-run"))
    git(env, "-C", str(project), "checkout", "-q", "develop")
    (project / "notes.txt").write_text("work in progress\n")
    git(env, "-C", str(project), "add", "notes.txt")
    before = snapshot(project, policy_dir(env), fl.Path(env["COMPOZY_HOME"]))
    proc = setup(project, env)
    assert_refused_in_preflight(fake, env, project, proc, before,
                                "refused: the checkout's index has staged changes; commit or unstage them, then re-run")


def test_s6_8_rerun_after_a_failed_pr_create_reuses_the_identical_branch(fake, env, project):
    gh_state = json.loads(open(env["FAKE_GH_STATE"]).read())
    gh_state["fail"] = ["pr create"]
    open(env["FAKE_GH_STATE"], "w").write(json.dumps(gh_state))
    first = setup(project, env)
    assert first.returncode == 1 and "branch     factory/setup from origin/develop, pushed" in first.stdout
    assert not (project / ".compozy").exists()  # the checkout is written only after the PR
    gh_state = json.loads(open(env["FAKE_GH_STATE"]).read())
    gh_state["fail"] = []
    open(env["FAKE_GH_STATE"], "w").write(json.dumps(gh_state))
    again = setup(project, env, answers="acme-api-prod\n1234567\ny\n")
    assert again.returncode == 0, again.stdout + again.stderr
    assert "preflight  branch factory/setup already on origin with these files; open or reuse its PR" in again.stdout
    assert "pr         #241 opened into develop" in again.stdout and (project / "REVIEW.md").is_file()
