"""bin/migrate-config and bin/stage-policy (factory-roles S4: UT-009..UT-011, UT-030, UT-031, UT-062, IT-001).

git is real (a bare local `origin`, an isolated git config so no global hook runs); `gh` is a fake that
logs its arguments; the policy home is a temp dir (GITHUB_APP_POLICY_FILE). Nothing touches the network.
"""
import hashlib
import json
import os
import shutil
import subprocess
import tomllib

import pytest

import factorylib as fl
from conftest import REPO, run, run_tty

OLD = REPO / "tests" / "fixtures" / "old-factory.toml"
FAKE_GH = r"""#!/bin/sh
printf "%s\n" "$*" >> "$FAKE_GH_LOG"
case "$1 $2" in
"pr list") cat "$FAKE_GH_LOG.open" 2>/dev/null || echo '[]' ;;
"pr create")
  if [ -e "$FAKE_GH_LOG.fail" ]; then rm "$FAKE_GH_LOG.fail"; echo "HTTP 502" >&2; exit 1; fi
  echo '[{"number": 240}]' > "$FAKE_GH_LOG.open"; echo https://github.com/acme/acme-api/pull/240 ;;
esac
"""
TOML, NR = ".compozy/factory/factory.toml", ".compozy/newrelic.toml"


def git(*args, cwd, env):
    return subprocess.run(["git", *args], cwd=cwd, env=env, capture_output=True, text=True, check=True).stdout


@pytest.fixture
def proj(tmp_path):
    """A project checkout whose origin/develop carries the old factory.toml; returns (root, env)."""
    gitconfig = tmp_path / "gitconfig"
    gitconfig.write_text("[user]\n\tname = Test\n\temail = test@example.invalid\n[init]\n\tdefaultBranch = develop\n")
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gh").write_text(FAKE_GH)
    (tmp_path / "bin" / "gh").chmod(0o755)
    env = {**{k: v for k, v in os.environ.items() if not k.startswith(("GIT_", "COMPOZY_"))},
           "GIT_CONFIG_GLOBAL": str(gitconfig), "GIT_CONFIG_NOSYSTEM": "1", "HOME": str(tmp_path / "home"),
           "PATH": f"{tmp_path / 'bin'}{os.pathsep}{os.environ['PATH']}", "FAKE_GH_LOG": str(tmp_path / "gh.log"),
           "GITHUB_APP_POLICY_FILE": str(tmp_path / "cfg" / "policy.toml")}
    (tmp_path / "gh.log").write_text("")
    origin, root = tmp_path / "origin.git", tmp_path / "acme-api"
    git("init", "--quiet", "--bare", str(origin), cwd=tmp_path, env=env)
    git("init", "--quiet", str(root), cwd=tmp_path, env=env)
    (root / ".compozy" / "factory").mkdir(parents=True)
    shutil.copy(OLD, root / TOML)
    git("add", ".", cwd=root, env=env)
    git("commit", "--quiet", "-m", "init", cwd=root, env=env)
    git("remote", "add", "origin", str(origin), cwd=root, env=env)
    git("push", "--quiet", "origin", "HEAD:develop", cwd=root, env=env)
    return root, env


def pushed(root, env, rel):
    return git("show", f"origin/factory/migrate-roles:{rel}", cwd=root, env=env)


def migrate(root, env, answer="octocat, hubot\n\n", work_key=""):  # approvers, deploy paths (none)
    """work_key None: no --work-key, so the terminal is asked for it (the next answer line)."""
    return run_tty("migrate-config", str(root), *(() if work_key is None else ("--work-key", work_key)), env=env,
                   answer=answer)


def gh_calls(env):
    return open(env["FAKE_GH_LOG"]).read().splitlines()


def leaves(value, prefix=()):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from leaves(v, prefix + (k,))
    else:
        yield prefix, value


# ---------------------------------------------------------------- UT-009, UT-011, UT-031: first run on a terminal

def test_fr_ut_009_011_031_converts_losslessly_opens_the_pr_and_stages_the_policy(proj):
    root, env = proj
    before = (root / TOML).read_bytes()
    proc = migrate(root, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    out = proc.stdout
    for line in ("converted  .compozy/factory/factory.toml (6 sections → [roles.*])",
                 "newrelic   .compozy/newrelic.toml (app, account_id from the old [newrelic])",
                 "branch     factory/migrate-roles from origin/develop, pushed",
                 "pr         #240 opened into develop (the reviewer flags the factory.toml change for you)"):
        assert line in out, line
    assert "approvers  promotion approvers for acme/acme-api (comma-separated GitHub logins):" in out
    assert "dropped" not in out, "the fixture's [review] agent is factory-reviewer"
    # UT-011: the policy block asked on the terminal
    block = ('[repos."acme/acme-api"]\ndelivery_base = "develop"\npromotion = { head = "develop", base = "main" }\n'
             'approvers = ["octocat", "hubot"]\n')
    assert "\n".join("           " + line for line in block.splitlines()) in out
    working = root.parent / "cfg" / "policy.toml"
    assert working.read_text() == block
    digest = hashlib.sha256(block.encode()).hexdigest()
    version = working.parent / "policies" / f"{digest}.toml"
    assert version.read_bytes() == block.encode() and version.stat().st_mode & 0o777 == 0o444
    assert f"policy     staged     policies/{digest}.toml (byte copy of the working copy; schema checked)" in out
    assert f"policy     activate   {fl.activate_line(digest)}" in out
    assert "not active" in out.splitlines()[-1]
    # UT-009: every old value is under its mapped key (value set), the new file validates
    old, new = tomllib.loads(OLD.read_text()), tomllib.loads(pushed(root, env, TOML))
    nr = tomllib.loads(pushed(root, env, NR))
    roles = new["roles"]
    assert roles["code"] == {"provider": "github", "github": {
        "repo": old["repo"]["github"], "base": old["repo"]["base"], "release": old["repo"]["release"],
        "merge": old["repo"]["merge"], "ci_runner": old["ci"]["runner"]}}
    assert roles["work"] == {"provider": "jira", "jira": old["jira"]}
    assert roles["deploy"] == {"provider": "github-deployments", "github-deployments": old["deploy"]}
    assert roles["review"] == {"provider": "github-app-review", "github-app-review": {}}
    assert old["review"] == {"extension": "github-app-review", "repo": old["repo"]["github"], "agent": "factory-reviewer"}
    assert all(new[k] == old[k] for k in ("language", "authorization", "team", "runtime"))
    new_values = [v for _, v in leaves(new)] + [v for _, v in leaves(nr)]
    lost = [(path, v) for path, v in leaves(old) if v not in new_values and path[0] != "review"]
    assert lost == []
    assert not any(s in new for s in fl.OLD_SECTIONS)
    # UT-031
    assert pushed(root, env, NR) == 'app = "acme-api-prod"\naccount_id = 1234567\n'
    assert roles["signals"] == {"provider": ["newrelic"], "newrelic": {"config": ".compozy/newrelic.toml"}}
    # the PR, and the operator's checkout untouched
    assert gh_calls(env)[0] == "pr list --repo acme/acme-api --head factory/migrate-roles --state open --json number"
    assert gh_calls(env)[1].startswith("pr create --repo acme/acme-api --base develop --head factory/migrate-roles "
                                       "--title chore: migrate factory.toml to the role grammar --body ")
    assert (root / TOML).read_bytes() == before and not (root / NR).exists()
    assert git("status", "--porcelain", cwd=root, env=env) == ""
    assert len(git("worktree", "list", cwd=root, env=env).splitlines()) == 1, "temporary worktree removed"


def test_fr_it_001_migrated_monorepo_style_file_validates_and_attach_dry_run_accepts_it(proj, fake, installed):
    root, env = proj
    assert migrate(root, env).returncode == 0
    ws = root.parent / "migrated"
    (ws / ".compozy" / "factory").mkdir(parents=True)
    (ws / TOML).write_text(pushed(root, env, TOML))
    (ws / NR).write_text(pushed(root, env, NR))
    _, conf = fl.load_factory_toml(ws)  # UT-001 rules
    assert {r: conf["roles"][r]["provider"] for r in fl.ROLES} == {
        "signals": ["newrelic"], "code": "github", "work": "jira", "review": "github-app-review",
        "deploy": "github-deployments"}
    fake.log_path.write_text("")
    proc = run("attach", str(ws), "--dry-run", "--allow-unsupported-adapter", env=installed)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert sum(line.startswith("would attach ") for line in proc.stdout.splitlines()) == 6
    assert not [c for c in fake.calls if c[:3] == ["agent", "instance", "create"] or c[:2] == ["session", "prompt"]]


# ---------------------------------------------------------------- UT-010, UT-030 and refusals

def test_fr_ut_010_new_grammar_is_unchanged(proj):
    root, env = proj
    shutil.copy(REPO / "factory.example.toml", root / TOML)
    before = (root / TOML).read_bytes()
    proc = run("migrate-config", str(root), env=env, stdin="")
    assert (proc.returncode, proc.stdout) == (0, "unchanged  .compozy/factory/factory.toml\n")
    assert (root / TOML).read_bytes() == before and gh_calls(env) == []


def no_trace(root, env):
    assert gh_calls(env) == []
    assert not (root.parent / "cfg").exists(), "no policy file"
    assert git("ls-remote", "--heads", "origin", cwd=root, env=env).count("refs/heads/") == 1, "no branch"
    assert git("status", "--porcelain", cwd=root, env=env) == "" and not (root / NR).exists()


def test_fr_ut_030_non_interactive_first_run_is_refused_before_any_write(proj):
    root, env = proj
    proc = run("migrate-config", str(root), env=env, stdin="")
    assert (proc.returncode, proc.stdout.strip()) == (
        1, "refused: promotion approvers need an interactive terminal (stdin is not a TTY)")
    no_trace(root, env)


def test_fr_migrate_refuses_what_it_cannot_map_or_a_conflicting_policy(proj):
    root, env = proj
    text = (root / TOML).read_text()
    (root / TOML).write_text(text.replace("[ci]\n", "[ci]\nimage = \"x\"\n"))
    proc = migrate(root, env)
    assert (proc.returncode, proc.stdout.strip()) == (
        1, "refused: old [ci] image has no place in the role grammar; remove or move it, then re-run")
    (root / TOML).write_text(text.replace('app_name = "acme-api-prod"', "app_name = \"it's\""))
    assert "old [newrelic] app_name must be a string" in migrate(root, env).stdout
    (root / TOML).write_text(text)
    for answer in ("\n", "octo cat\n"):
        proc = migrate(root, env, answer)
        assert proc.returncode == 1 and "approvers must be one or more GitHub logins" in proc.stdout
    working = root.parent / "cfg" / "policy.toml"
    working.parent.mkdir()
    working.write_text('[repos."acme/acme-api"]\ndelivery_base = "main"\n')
    proc = migrate(root, env)
    assert proc.returncode == 1 and 'already has [repos."acme/acme-api"] with other values' in proc.stdout
    working.unlink()
    working.parent.rmdir()
    no_trace(root, env)


def test_fr_migrate_keeps_other_repos_and_reuses_the_pending_branch_and_pr(proj):  # H-5
    root, env = proj
    working = root.parent / "cfg" / "policy.toml"
    working.parent.mkdir()
    working.write_text('# operator notes\n[repos."acme/other-api"]\ndelivery_base = "main"\n')
    assert migrate(root, env).returncode == 0
    repos = fl.parse_policy(working.read_bytes(), working)
    assert sorted(r["repo"] for r in repos.values()) == ["acme/acme-api", "acme/other-api"]
    assert working.read_text().startswith('# operator notes\n[repos."acme/other-api"]\ndelivery_base = "main"\n\n')
    proc = migrate(root, env)  # the first PR is still pending: same files, same PR, same policy block
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "branch     factory/migrate-roles already on origin with these files" in proc.stdout
    assert "pr         #240 already open into develop" in proc.stdout
    assert "already has this block for acme/acme-api" in proc.stdout and "unchanged  policies/" in proc.stdout
    assert sum(c.startswith("pr create") for c in gh_calls(env)) == 1


def test_fr_h5_interrupted_run_resumes_with_the_pr_and_the_policy(proj):
    root, env = proj
    open(env["FAKE_GH_LOG"] + ".fail", "w").close()  # the push succeeds, then gh pr create fails
    proc = migrate(root, env)
    assert proc.returncode == 1 and "pushed" in proc.stdout and not (root.parent / "cfg").exists()
    proc = migrate(root, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "already on origin with these files" in proc.stdout and "pr         #240 opened into develop" in proc.stdout
    assert "policy     staged     policies/" in proc.stdout
    # a branch with other files is still refused
    git("push", "--quiet", "--force", "origin", "develop:factory/migrate-roles", cwd=root, env=env)
    proc = migrate(root, env)
    assert proc.returncode == 1 and "already exists on origin with other files" in proc.stdout


def test_fr_h4_a_custom_review_agent_is_reported_dropped(proj):
    root, env = proj
    (root / TOML).write_text((root / TOML).read_text().replace('agent = "factory-reviewer"', 'agent = "my-reviewer"'))
    proc = run("migrate-config", str(root), "--dry-run", env=env, stdin="")
    assert proc.returncode == 0
    assert 'dropped    [review] agent "my-reviewer" (BR-16: always factory-reviewer)' in proc.stdout.splitlines()


def test_fr_migrate_dry_run_writes_nothing(proj):
    root, env = proj
    proc = run("migrate-config", str(root), "--dry-run", env=env, stdin="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.startswith("--- .compozy/factory/factory.toml\n") and "dry-run: nothing written" in proc.stdout
    assert 'app = "acme-api-prod"' in proc.stdout
    no_trace(root, env)


# ---------------------------------------------------------------- UT-062: bin/stage-policy (F1)

BLOCK = ('[repos."acme/acme-api"]\ndelivery_base = "develop"\npromotion = { head = "develop", base = "main" }\n'
         'approvers = ["octocat"]\n')


def stage(tmp_path, text=None, data=None):
    working = tmp_path / "cfg" / "policy.toml"
    if text is not None or data is not None:
        working.parent.mkdir(exist_ok=True)
        working.write_bytes(data if data is not None else text.encode())
    env = {"GITHUB_APP_POLICY_FILE": str(working), "HOME": str(tmp_path / "home")}
    return run("stage-policy", env=env), working


def test_fr_ut_062_stage_policy_byte_copy_refusals_and_unchanged(tmp_path):
    proc, working = stage(tmp_path, BLOCK)
    digest = hashlib.sha256(BLOCK.encode()).hexdigest()
    version = working.parent / "policies" / f"{digest}.toml"
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.splitlines() == [
        f"staged     policies/{digest}.toml (byte copy of the working copy; schema checked)",
        f"activate   printf {digest} | compozy extension secrets set github-app-review "
        "--env GITHUB_APP_POLICY_DIGEST --value-stdin"]
    assert version.read_bytes() == BLOCK.encode()
    assert (version.stat().st_mode & 0o777, version.parent.stat().st_mode & 0o777) == (0o444, 0o700)
    proc, _ = stage(tmp_path)
    assert proc.returncode == 0 and proc.stdout.splitlines()[0] == f"unchanged  policies/{digest}.toml"
    assert sorted(p.name for p in version.parent.iterdir()) == [version.name], "no second version, no temp file"
    # invalid schema → refused, nothing written
    proc, _ = stage(tmp_path, BLOCK.replace('approvers = ["octocat"]\n', ""))
    assert proc.returncode == 1
    assert proc.stdout.strip() == f'refused: {working}:1: repos."acme/acme-api": approvers are required when promotion is set'
    assert sorted(p.name for p in version.parent.iterdir()) == [version.name]
    # an existing version with other bytes is never overwritten
    other = BLOCK.replace("octocat", "hubot")
    taken = version.parent / f"{hashlib.sha256(other.encode()).hexdigest()}.toml"
    taken.write_text("tampered\n")
    proc, _ = stage(tmp_path, other)
    assert proc.returncode == 1 and "exists with other bytes" in proc.stdout and taken.read_text() == "tampered\n"
    taken.chmod(0o644)
    taken.unlink()
    # missing working copy, relative override
    shutil.rmtree(working.parent)
    proc, _ = stage(tmp_path)
    assert (proc.returncode, proc.stdout.strip()) == (1, f"refused: {working} does not exist")
    proc = run("stage-policy", env={"GITHUB_APP_POLICY_FILE": "policy.toml"})
    assert proc.stdout.strip() == "refused: GITHUB_APP_POLICY_FILE must be an absolute path"


def test_fr_ut_062_default_working_copy_follows_the_extension(tmp_path):
    home, xdg = tmp_path / "home", tmp_path / "xdg"
    assert fl.policy_paths({"HOME": str(home)}) == (home / ".config" / "compozy-github-app" / "policy.toml",
                                                    home / ".config" / "compozy-github-app" / "policies")
    assert fl.policy_paths({"HOME": str(home), "XDG_CONFIG_HOME": str(xdg)})[0] == xdg / "compozy-github-app" / "policy.toml"
    assert fl.policy_paths({"HOME": str(home), "XDG_CONFIG_HOME": "rel"})[0].parent.parent == home / ".config"


# The review-s3 "G-4 parity" table: TOML the extension refuses although tomllib accepts most of it.
G4_REFUSED = [
    '[repos ."acme/a"]\ndelivery_base = "x"\n', '[repos. "acme/a"]\ndelivery_base = "x"\n',            # 1
    '[repos]\n', '[repos."a/b".promotion]\nhead = "x"\n', '[[repos."a/b"]]\ndelivery_base = "x"\n',     # 2
    '[repos."a/b"]\npromotion.head = "x"\n', '[repos."a/b"]\n"delivery_base" = "x"\n',                 # 3
    '[repos."a/b"]\ndelivery_base = "x"\npromotion = { "head" = "x", base = "y" }\napprovers = ["a"]\n',  # 4
    '[repos."a/b"]\ndelivery_base = "\\u0078"\n',                                                       # 5
    '[repos."a/b"]\ndelivery_base = """x"""\n', "[repos.\"a/b\"]\ndelivery_base = '''x'''\n",           # 6
    '[repos."a/b"]\ndelivery_base = "x"\napprovers = [\n"a"]\n',                                        # 7
    '[repos."a/b"]\ndelivery_base = "x"\npromotion = { head = "x", base = "y", }\napprovers = ["a"]\n',  # 8
    '[repos."a/b"]\ndelivery_base = 1\n', '[repos."a/b"]\ndelivery_base = "x"\nlive = true\n',          # 9
    '[repos."a/b"]\ndelivery_base = "x"\n[repos."A/b"]\ndelivery_base = "y"\n',                        # 10
    'delivery_base = "x"\n',                                                                            # 11
    '\ufeff[repos."a/b"]\ndelivery_base = "x"\n',                                                       # 12
    '[repos."a/b"]\n\u00a0delivery_base = "x"\n', '[repos."a/b"]\ndelivery_base = "x"\u2028\n',        # G-4 whitespace
    '[repos."a/b"]\rdelivery_base = "x"\n', '[repos."a/b"]\ndelivery_base = "x"\nconstructor = "y"\n',
    '[repos."a/b"]\ndelivery_base = "x"\npromotion = { head = "x", base = "x" }\napprovers = ["a"]\n',
    '[repos."a/b"]\ndelivery_base = "y"\npromotion = { head = "x", base = "z" }\napprovers = ["a"]\n',
    '[repos."a/b"]\ndelivery_base = "x"\napprovers = "a"\n',
]
G4_ACCEPTED = [
    BLOCK, '[ repos."a/b" ]\ndelivery_base = "x"\n', "[repos.'a/b']\ndelivery_base = 'x'\n",
    '[repos."a/b"]\ndelivery_base="x"#c\n', '[repos."a/b"]\ndelivery_base\t=\t"x"\napprovers = [ ]\n',
    '[repos."a/b"]\ndelivery_base = "x"\napprovers = ["a",]\n', '[repos."a/b"]\r\ndelivery_base = "x"\r\n',
    "", "# only a comment\n", '[repos."a/b"]\ndelivery_base = "x"\napprovers = ["a"]\n',
    '[repos."../.."]\ndelivery_base = "x"\n',  # accepted by the extension too (review-s3, pre-existing)
]


def test_fr_ut_062_parser_matches_the_extension_subset():
    for text in G4_REFUSED:
        with pytest.raises(ValueError):
            fl.parse_policy(text.encode(), "p")
    for text in G4_ACCEPTED:
        fl.parse_policy(text.encode(), "p")
    tomllib.loads('[repos . "a/b"]\ndelivery_base = "x"\n')  # why the port does not use tomllib
    with pytest.raises(ValueError, match="not valid UTF-8"):
        fl.parse_policy(b'[repos."a/b"]\ndelivery_base = "\xff"\n', "p")


@pytest.mark.skipif(not os.environ.get("GHAR_SRC") or not shutil.which("bun"),
                    reason="set GHAR_SRC=<compozy-github-app checkout ≥ 0.3.0> with bun on PATH")
def test_fr_ut_062_the_extension_loader_accepts_the_staged_file_and_agrees_on_every_case(tmp_path):
    """Runs the shipped extension's own loadPolicy/parsePolicy (read-only import) against stage-policy's output."""
    proc, working = stage(tmp_path, BLOCK)
    digest = hashlib.sha256(BLOCK.encode()).hexdigest()
    cases = tmp_path / "cases.json"
    cases.write_text(json.dumps(G4_REFUSED + G4_ACCEPTED))
    script = tmp_path / "probe.ts"
    script.write_text(
        f'import {{ loadPolicy, parsePolicy, decode }} from {json.dumps(os.environ["GHAR_SRC"] + "/src/policy.ts")};\n'
        'import { readFileSync } from "node:fs";\n'
        f'const p = loadPolicy({{ GITHUB_APP_POLICY_FILE: {json.dumps(str(working))}, GITHUB_APP_POLICY_DIGEST: "{digest}" }});\n'
        'const cases: string[] = JSON.parse(readFileSync(process.argv[2], "utf8"));\n'
        'const verdicts = cases.map(c => { try { parsePolicy(decode(Buffer.from(c, "utf8"), "p"), "p"); return true; }'
        ' catch { return false; } });\n'
        'console.log(JSON.stringify({ repos: [...p.repos.keys()], verdicts }));\n')
    out = subprocess.run(["bun", str(script), str(cases)], capture_output=True, text=True, cwd=tmp_path)
    assert out.returncode == 0, out.stderr
    result = json.loads(out.stdout)
    assert result["repos"] == ["acme/acme-api"]
    assert result["verdicts"] == [False] * len(G4_REFUSED) + [True] * len(G4_ACCEPTED)



def test_fix6_migrate_asks_for_deploy_trigger_sets(proj):
    root, env = proj
    proc = migrate(root, env, "octocat\nacme-portal/**, packages/*/migrations/**\n**/*.{md,rst}\nweb/**\n\n\n")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "deploy     trigger set 1: includes acme-portal/**, packages/*/migrations/**; excludes **/*.{md,rst}" in proc.stdout
    assert "deploy     trigger set 2: includes web/**" in proc.stdout
    deploy = tomllib.loads(pushed(root, env, TOML))["roles"]["deploy"]
    assert deploy["paths"] == [{"includes": ["acme-portal/**", "packages/*/migrations/**"], "excludes": ["**/*.{md,rst}"]},
                               {"includes": ["web/**"]}]
    assert deploy["provider"] == "github-deployments"


def test_fix6_migrate_notes_absent_deploy_paths(proj):
    root, env = proj
    proc = run("migrate-config", str(root), "--dry-run", env=env, stdin="")
    assert f"{fl.NO_DEPLOY_PATHS} (asked on the real run)" in proc.stdout
    proc = migrate(root, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fl.NO_DEPLOY_PATHS in proc.stdout
    assert "paths" not in tomllib.loads(pushed(root, env, TOML))["roles"]["deploy"]


def test_s7_a_git_ignored_new_file_refuses_before_any_write(proj):
    """S7: a rule on the new newrelic.toml refuses; the tracked factory.toml is exempt from file rules."""
    root, env = proj
    exclude = root / ".git/info/exclude"
    lineno = len(exclude.read_text().splitlines()) + 1
    exclude.write_text(exclude.read_text() + "*.toml\n")
    proc = migrate(root, env)
    assert (proc.returncode, proc.stdout.strip().splitlines()[-1]) == (
        1, f"refused: {NR} is ignored by .git/info/exclude:{lineno} (*.toml) — narrow that rule, then re-run")
    no_trace(root, env)


def test_f7_a_tracked_file_in_an_ignored_directory_refuses(proj):
    """`git add` refuses a tracked file whose directory an ignore rule excludes (`.compozy/`)."""
    root, env = proj
    exclude = root / ".git/info/exclude"
    lineno = len(exclude.read_text().splitlines()) + 1
    exclude.write_text(exclude.read_text() + ".compozy/\n")
    proc = migrate(root, env)
    assert (proc.returncode, proc.stdout.strip().splitlines()[-1]) == (
        1, f"refused: {TOML} is ignored by .git/info/exclude:{lineno} (.compozy/) — narrow that rule, then re-run")
    no_trace(root, env)


def test_s7_work_key_asked_on_the_terminal_goes_into_the_pr_title(proj):
    root, env = proj
    proc = migrate(root, env, "octocat\n\nOTHER-18085\n", work_key=None)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    title = "chore: OTHER-18085 migrate factory.toml to the role grammar"
    assert f"pr         title: {title} (an already open PR keeps its title)" in proc.stdout
    assert fl.NO_WORK_KEY not in proc.stdout
    assert gh_calls(env)[1].startswith(f"pr create --repo acme/acme-api --base develop --head factory/migrate-roles "
                                       f"--title {title} --body ")


def test_s7_migrate_without_a_work_key_notes_it(proj):
    root, env = proj
    proc = migrate(root, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert fl.NO_WORK_KEY in proc.stdout


def test_s7_migrate_refuses_a_malformed_work_key_before_any_write(proj):
    root, env = proj
    proc = migrate(root, env, work_key="#12")
    assert (proc.returncode, proc.stdout.strip().splitlines()[-1]) == (
        1, "refused: work key '#12' does not match the work/jira adapter's key_format ^[A-Z][A-Z0-9]+-\\d+$; "
           "fix --work-key, then re-run")
    no_trace(root, env)


def test_f7_4_migrate_reads_the_base_trees_ignore_rules_not_the_checkouts(proj):
    """migrate-config writes only in the PR worktree from origin/develop: its committed .gitignore decides."""
    root, env = proj
    (root / ".gitignore").write_text(".compozy/newrelic.toml\n")
    git("add", ".gitignore", cwd=root, env=env)
    git("commit", "--quiet", "-m", "ignore", cwd=root, env=env)
    git("push", "--quiet", "origin", "HEAD:develop", cwd=root, env=env)
    (root / ".gitignore").write_text("")  # the checkout dropped the rule (unstaged)
    proc = migrate(root, env)
    assert (proc.returncode, proc.stdout.strip().splitlines()[-1]) == (1, (
        f"refused: {NR} is ignored by origin/develop:.gitignore:1 (.compozy/newrelic.toml) — narrow that rule, "
        "then re-run"))
    assert gh_calls(env) == []


def test_f7_1_migrate_allows_a_negated_rule(proj):
    root, env = proj
    exclude = root / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "*.toml\n!.compozy/newrelic.toml\n")
    proc = migrate(root, env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
