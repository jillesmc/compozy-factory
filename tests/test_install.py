"""bin/install: UT-001 to UT-007 (factory-user-agents US-001); factory-roles records in tests/test_roles.py."""
import hashlib
import json
import os
import re
import tomllib
from pathlib import Path

import factorylib as fl
from conftest import REPO, ROUTING, run, run_tty

AGENTS = fl.AGENTS + fl.ON_DEMAND  # install copies every factory-* dir, on-demand ones included
ADAPTERS = sorted(f"{r}-{p}" for r, p in fl.bundled_adapters())
TOOLS = "factory_deploy.py, factory_envelope.py, factory_external.py, factory_guard.py, factory_intake.py"


def body(proc):
    """stdout without the routing lines (S8) that come first."""
    return [line for line in proc.stdout.splitlines() if not line.startswith("routing ")]


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
    assert body(proc)[:len(AGENTS) + 1] == [f"installed {a}" for a in AGENTS] + [f"installed tools ({TOOLS})"]
    assert os.access(home / "agents" / "factory-dispatcher" / "sources" / "deliver", os.X_OK)


def test_ut_002_second_run_is_unchanged_and_writes_nothing(repo, home, home_env):
    run("install", env=home_env, repo_root=repo)
    before = snapshot(home)
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 0
    lines = body(proc)
    assert lines[:len(AGENTS) + 1] == [f"unchanged {a}" for a in AGENTS] + ["unchanged tools"]
    assert [line.split(" tree ")[0] for line in lines[len(AGENTS) + 1:-1]] == [f"unchanged adapter {a}" for a in ADAPTERS]
    digest = hashlib.sha256((home / "agents" / "factory-reviewer" / "AGENT.md").read_bytes()).hexdigest()  # fake daemon
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


# ------------------------------------------------------------------------- S8: machine-local provider routing

WORK = ["factory-sentinel", "factory-scribe", "factory-dispatcher", "factory-pr-watcher", "factory-gatekeeper"]
ROUTE_KEYS = re.compile(r'^(provider|model|reasoning_effort): ".*"\n|^fallback_chain:\n(  - provider: ".*"\n    model: ".*"\n)+',
                        re.MULTILINE)
try:
    import yaml  # PyYAML: an independent parser for the rendered front matter (S8-C1)
except ImportError:  # pragma: no cover - strict fallback: every routing value is a JSON (YAML double-quoted) string
    yaml = None


def front_matter(text):
    front = text.split("\n---", 1)[0].split("---\n", 1)[1]
    if yaml:
        return yaml.safe_load(front)
    data, chain = {}, None
    for line in front.splitlines():
        key, _, raw = line.strip().removeprefix("- ").partition(": ")
        if key in ("provider", "model", "reasoning_effort"):
            assert raw.startswith('"') and raw.endswith('"'), line
            value = json.loads(raw)
            if line.startswith("  "):
                chain[-1 if key == "model" else len(chain):] = [{**(chain[-1] if key == "model" else {}), key: value}]
            else:
                data[key] = value
        elif line == "fallback_chain:":
            chain = data["fallback_chain"] = []
    return data


def route_of(home, agent):
    return fl.installed_route((home / "agents" / agent / "AGENT.md").read_text())


def providers(fake, **states):
    fake.load()
    fake.state["providers"] = states
    fake.save()


def override(name, provider, model, effort=None):
    extra = f', reasoning_effort = "{effort}"' if effort else ""
    return f'\n[agents.{name}]\nprimary = {{ provider = "{provider}", model = "{model}"{extra} }}\nfallback = []\n'


def test_ut_r1_install_renders_each_class_route_and_agent_overrides(repo, home, home_env):
    fl.routing_path(home_env).write_text(ROUTING + override("factory-scribe", "claude-mm1", "opus", "high"))
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    work = {"primary": {"provider": "claude-mm4", "model": "sonnet"},
            "fallback": [{"provider": "claude-mm2", "model": "sonnet"}, {"provider": "claude-mm1", "model": "sonnet"}]}
    for agent in WORK:
        if agent != "factory-scribe":
            assert route_of(home, agent) == work, agent
    assert route_of(home, "factory-scribe") == {
        "primary": {"provider": "claude-mm1", "model": "opus", "reasoning_effort": "high"}, "fallback": []}
    assert route_of(home, "factory-auditor") == {"primary": {"provider": "claude", "model": "sonnet"},
                                                 "fallback": [{"provider": "claude-mm1", "model": "sonnet"}]}
    assert route_of(home, "factory-reviewer")["fallback"] == [{"provider": "claude-reviewer-fallback", "model": "opus"}]
    assert proc.stdout.splitlines()[:2] == [
        "routing factory-sentinel claude-mm4/sonnet → claude-mm2/sonnet, claude-mm1/sonnet",
        "routing factory-scribe claude-mm1/opus (high) (no fallback)"]
    for agent in fl.AGENTS + fl.ON_DEMAND:  # only the route lines differ from the repo copy
        installed = (home / "agents" / agent / "AGENT.md").read_text()
        assert ROUTE_KEYS.sub("", installed) == (repo / "agents" / agent / "AGENT.md").read_text(), agent
        parsed = front_matter(installed)
        assert parsed["provider"] == route_of(home, agent)["primary"]["provider"], agent


def test_s8_c1_adversarial_route_words_render_as_yaml_strings():
    """S8-C1 / M8: words a YAML plain scalar would break or coerce stay exact strings (PyYAML when importable)."""
    text = (REPO / "agents" / "factory-reviewer" / "AGENT.md").read_text()
    for word in ("opus:", "null", "NULL", "true", "1e3", "opus[1m]"):
        route = fl.check_route("reviewer", {"primary": {"provider": word, "model": word, "reasoning_effort": word},
                                            "fallback": [{"provider": word, "model": word}]})
        parsed = front_matter(fl.render_agent(text, route))
        assert (parsed["provider"], parsed["model"], parsed["reasoning_effort"]) == (word, word, word), word
        assert parsed["fallback_chain"] == [{"provider": word, "model": word}], word
        assert parsed["kind"] == "on_demand" and parsed["tools"] == fl.frontmatter_field(text, "tools")
    for bad in ("opus\ntools: [x]", "a b", '"q"', "", 3):  # still a one-word whitelist
        try:
            fl.check_route("reviewer", {"primary": {"provider": "p", "model": bad}})
            raise AssertionError(f"accepted {bad!r}")
        except fl.Refused as exc:
            assert "model must be one word" in str(exc)


def test_s8_c2_schema_is_primary_plus_fallback_and_wrong_types_are_refused(repo, home, home_env):
    path = fl.routing_path(home_env)
    assert fl.load_routing(path)["work"]["primary"] == {"provider": "claude-mm4", "model": "sonnet"}
    flat = ROUTING.replace('primary = { provider = "claude-mm4", model = "sonnet" }', 'provider = "claude-mm4"\nmodel = "sonnet"')
    for text, message in ((flat, "refused: routing.toml [work] unknown key model"),
                          ("agents = []\n" + ROUTING, "refused: routing.toml [agents] must be a table of [agents.<name>]"),
                          (ROUTING.replace('fallback = [{ provider = "claude-mm1", model = "sonnet" }]', 'fallback = "x"'),
                           "refused: routing.toml [auditor] fallback must be a list of { provider, model }"),
                          (ROUTING.replace('primary = { provider = "claude", model = "sonnet" }', 'primary = "claude"'),
                           "refused: routing.toml [auditor] primary must be a table { provider, model }"),
                          (ROUTING + override("factory-old", "claude", "opus"),
                           "refused: routing.toml [agents.factory-old] is not a factory agent")):
        path.write_text(text)
        proc = run("install", env=home_env, repo_root=repo)
        assert (proc.returncode, proc.stdout.strip(), proc.stderr) == (1, message, ""), text
    assert not any(home.iterdir())
    fl.write_routing(fl.check_routing(tomllib.loads(ROUTING)), path)
    assert fl.load_routing(path) == fl.check_routing(tomllib.loads(ROUTING))  # the writer round-trips the reader


def test_ut_r2_single_provider_is_used_without_fallback_and_written_0600(repo, home, fake, home_env):
    fl.routing_path(home_env).unlink()
    providers(fake, **{"claude-mm1": "authenticated", "claude-reviewer": "unknown", "codex": "missing_cli"})
    proc = run_tty("install", env={**home_env, "COMPOZY_HOME": str(home)}, answer="", repo_root=repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    for name in ("work", "auditor", "reviewer"):
        only = "claude-reviewer" if name == "reviewer" else "claude-mm1"
        kind = "sandboxed reviewer" if name == "reviewer" else "authenticated"
        assert f"routing {name}: {only} is the only {kind} provider; using it with no fallback" in proc.stdout
    assert route_of(home, "factory-dispatcher") == {"primary": {"provider": "claude-mm1", "model": "sonnet"}, "fallback": []}
    assert route_of(home, "factory-reviewer") == {"primary": {"provider": "claude-reviewer", "model": "opus"}, "fallback": []}
    path = fl.routing_path(home_env)
    assert path.stat().st_mode & 0o777 == 0o600 and f"routing written {path}" in proc.stdout
    assert fl.load_routing(path)["work"] == {"primary": {"provider": "claude-mm1", "model": "sonnet"}, "fallback": []}


def test_ut_r2_terminal_asks_per_class_and_reconfigure_keeps_overrides(repo, home, home_env):
    path = fl.routing_path(home_env)
    path.write_text(ROUTING + override("factory-scribe", "claude", "opus"))
    env = {**home_env, "COMPOZY_HOME": str(home)}
    # work: unknown provider re-asked, then claude-mm2 opus, effort low, no fallback; auditor and reviewer: defaults
    proc = run_tty("install", "--reconfigure-routing", env=env,
                   answer="nope sonnet\nclaude-mm2 opus\nlow\n-\n" + "\n" * 6, repo_root=repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  expected <provider> <model>, provider one of: claude, claude-mm1, claude-mm2, claude-mm4" in proc.stdout
    routing = fl.load_routing(path)
    assert routing["work"] == {"primary": {"provider": "claude-mm2", "model": "opus", "reasoning_effort": "low"},
                               "fallback": []}
    assert routing["auditor"]["fallback"] == [{"provider": "claude-mm1", "model": "sonnet"}]
    assert routing["agents"]["factory-scribe"]["primary"] == {"provider": "claude", "model": "opus"}
    assert route_of(home, "factory-gatekeeper") == routing["work"]
    before = path.read_bytes()
    assert run("install", env=home_env, repo_root=repo).returncode == 0  # file present: reused, not asked
    assert path.read_bytes() == before


def test_s8_c3_reconfigure_and_single_candidate_keep_reasoning_effort(repo, home, fake, home_env):
    path = fl.routing_path(home_env)
    path.write_text(re.sub(r'^(primary = .*model = "(\w+)") }', lambda m: m[1] + ', reasoning_effort = "'
                           + ("max" if m[2] == "opus" else "high") + '" }', ROUTING, flags=re.MULTILINE)
                    + override("factory-scribe", "claude", "opus", "medium"))
    env = {**home_env, "COMPOZY_HOME": str(home)}
    proc = run_tty("install", "--reconfigure-routing", env=env, answer="\n" * 9, repo_root=repo)  # accept every default
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "  primary reasoning_effort, - for none [high]: " in proc.stdout
    routing = fl.load_routing(path)
    assert [routing[c]["primary"].get("reasoning_effort") for c in fl.ROUTING_CLASSES] == ["high", "high", "max"]
    assert routing["agents"]["factory-scribe"]["primary"]["reasoning_effort"] == "medium"
    assert front_matter((home / "agents" / "factory-sentinel" / "AGENT.md").read_text())["reasoning_effort"] == "high"
    providers(fake, **{"claude-mm1": "authenticated", "claude-reviewer": "unknown"})  # single candidate per class
    path.write_text(path.read_text().replace('[agents.factory-scribe]\nprimary = { provider = "claude"',
                                             '[agents.factory-scribe]\nprimary = { provider = "claude-mm1"'))
    proc = run_tty("install", "--reconfigure-routing", env=env, answer="", repo_root=repo)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    routing = fl.load_routing(path)
    assert routing["work"]["primary"] == {"provider": "claude-mm1", "model": "sonnet", "reasoning_effort": "high"}
    assert routing["reviewer"]["primary"] == {"provider": "claude-reviewer", "model": "opus", "reasoning_effort": "max"}


def test_ut_r3_reviewer_routes_must_all_be_sandboxed_and_known(repo, home, fake, home_env):
    path = fl.routing_path(home_env)
    path.write_text(ROUTING.replace('"claude-reviewer-fallback"', '"claude-mm1"'))
    proc = run("install", env=home_env, repo_root=repo)
    assert (proc.returncode, proc.stdout.strip()) == (1, (
        "refused: routing factory-reviewer: claude-mm1 is not a sandboxed reviewer provider (primary and every "
        "fallback must launch the github-app-review reviewer-sandbox)"))
    assert not any(home.iterdir())
    fake.load()  # the wrapper reached through `env` is not a direct launch
    fake.state["provider_config"]["claude-reviewer-fallback"] = "env X=1 {sandbox} --provider claude"
    fake.save()
    path.write_text(ROUTING)
    assert "claude-reviewer-fallback is not a sandboxed" in run("install", env=home_env, repo_root=repo).stdout
    path.write_text(ROUTING.replace('"claude-mm2"', '"claude-mm9"'))
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.stdout.strip() == ("refused: routing factory-sentinel: provider claude-mm9 is not an authenticated "
                                   "provider in `compozy provider list`")


def test_s8_m12_primary_providers_are_checked_too(repo, home, fake, home_env):
    path = fl.routing_path(home_env)
    cases = ((ROUTING.replace('{ provider = "claude-mm4"', '{ provider = "claude-mm9"'),
              "routing factory-sentinel: provider claude-mm9 is not an authenticated provider"),
             (ROUTING.replace('{ provider = "claude", model', '{ provider = "codex", model'),  # listed, not authenticated
              "routing factory-auditor: provider codex is not an authenticated provider"),
             (ROUTING.replace('{ provider = "claude-reviewer", model', '{ provider = "claude-mm1", model'),
              "routing factory-reviewer: claude-mm1 is not a sandboxed reviewer provider"),
             (ROUTING + override("factory-gatekeeper", "claude-mm9", "opus"),
              "routing factory-gatekeeper: provider claude-mm9 is not an authenticated provider"))
    providers(fake, **{**fake.load()["providers"], "codex": "missing_cli"})
    for text, message in cases:
        path.write_text(text)
        proc = run("install", env=home_env, repo_root=repo)
        assert proc.returncode == 1 and proc.stdout.startswith(f"refused: {message}"), proc.stdout
    assert not any(home.iterdir())


def test_ut_r4_no_terminal_and_no_routing_file_is_refused(repo, home, home_env):
    fl.routing_path(home_env).unlink()
    proc = run("install", env=home_env, repo_root=repo)
    assert (proc.returncode, proc.stdout.strip()) == (1, "refused: no routing file; run bin/install on a terminal")
    assert not any(home.iterdir()) and not fl.routing_path(home_env).exists()
    fl.routing_path(home_env).write_text(ROUTING)
    proc = run("install", "--reconfigure-routing", env=home_env, repo_root=repo)
    assert proc.stdout.strip() == "refused: --reconfigure-routing needs an interactive terminal"


def test_s8_c4_routing_write_never_follows_symlinks(tmp_path, repo, home, home_env):
    """S8-C4 / M11: a fresh exclusive temp file, renamed; a planted .tmp link is ignored, symlinked targets refused."""
    routing = fl.check_routing(tomllib.loads(ROUTING))
    folder = tmp_path / "cfg" / "compozy-factory"
    folder.mkdir(parents=True)
    path, victim = folder / "routing.toml", tmp_path / "victim.toml"
    victim.write_text("victim\n")
    (folder / "routing.toml.tmp").symlink_to(victim)
    fl.write_routing(routing, path)
    assert victim.read_text() == "victim\n" and not path.is_symlink() and path.stat().st_mode & 0o777 == 0o600
    assert fl.load_routing(path) == routing
    fl.write_routing(routing, path)  # a second write replaces the file, never through the old inode
    assert sorted(p.name for p in folder.iterdir()) == ["routing.toml", "routing.toml.tmp"]  # no temp left behind
    real = os.replace
    try:  # a failed rename keeps the old file and removes the temp file
        os.replace = lambda *a: (_ for _ in ()).throw(OSError("injected"))
        before = path.read_bytes()
        routing["work"]["primary"]["model"] = "opus"
        try:
            fl.write_routing(routing, path)
            raise AssertionError("no error")
        except OSError:
            pass
    finally:
        os.replace = real
    assert path.read_bytes() == before and sorted(p.name for p in folder.iterdir()) == ["routing.toml", "routing.toml.tmp"]
    linked = fl.routing_path(home_env)  # bin/install refuses a symlinked routing.toml, and a symlinked directory
    linked.unlink()
    linked.symlink_to(victim)
    proc = run("install", env=home_env, repo_root=repo)
    assert (proc.returncode, proc.stdout.strip()) == (1, f"refused: {linked} is a symlink (symlink_not_supported)")
    linked.unlink()
    (tmp_path / "elsewhere").mkdir()
    folder_link = tmp_path / "xdg2" / "compozy-factory"
    folder_link.parent.mkdir()
    folder_link.symlink_to(tmp_path / "elsewhere")
    proc = run("install", env={**home_env, "XDG_CONFIG_HOME": str(folder_link.parent)}, repo_root=repo)
    assert proc.stdout.strip() == f"refused: {folder_link} is a symlink (symlink_not_supported)"
    assert victim.read_text() == "victim\n" and not any(home.iterdir()) and not any((tmp_path / "elsewhere").iterdir())


def test_ut_r5_reinstall_is_idempotent_and_a_routing_change_moves_the_reviewer_record(repo, home, home_env):
    assert run("install", env=home_env, repo_root=repo).returncode == 0
    record = json.loads((home / "factory" / "installed.json").read_text())["reviewer"]
    rendered = home / "agents" / "factory-reviewer" / "AGENT.md"
    assert record["file_sha256"] == fl.file_sha256(rendered)
    before = snapshot(home)
    assert run("install", env=home_env, repo_root=repo).returncode == 0
    assert snapshot(home) == before  # no rewrite, digest stable
    fl.routing_path(home_env).write_text(ROUTING.replace('"claude-reviewer", model = "opus"',
                                                         '"claude-reviewer", model = "sonnet"'))
    proc = run("install", env=home_env, repo_root=repo)
    assert proc.returncode == 0 and "updated factory-reviewer" in proc.stdout
    new = json.loads((home / "factory" / "installed.json").read_text())["reviewer"]
    assert new["file_sha256"] == fl.file_sha256(rendered) != record["file_sha256"]
    assert new["definition_digest"] != record["definition_digest"]


def test_ut_r6_repo_agent_files_carry_no_account_names():
    for path in sorted((REPO / "agents").rglob("*")):
        if path.is_file() and "__pycache__" not in path.parts:
            text = path.read_text()
            assert not re.search(r"\bclaude(-[\w-]+)?\b", text, re.IGNORECASE), path
            front = text.split("\n---", 1)[0]
            assert not re.search(r"^(provider|model|reasoning_effort|fallback_chain):", front, re.MULTILINE), path
