#!/usr/bin/env python3
"""Deterministic guards for the factory (Python 3 stdlib only). Exit 0 ok, 1 refused; one line.

    factory_guard.py reviewer <workspace>                                  # BR-16 trusted reviewer
    factory_guard.py premerge <repo> <pr> <expected-base> <expected-head>  # BR-20 pre-merge re-read
    factory_guard.py adapter <role> <provider> <workspace>                 # BR-4/BR-21 adapter skill

Also the shared core of bin/factorylib.py and of the provider-neutral heartbeat sources: tree digests,
installed.json and bindings.json under $COMPOZY_HOME/factory/.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REVIEWER = "factory-reviewer"
ROLES = ("signals", "code", "work", "review", "deploy")


class Refused(Exception):
    """A guard refusal; str(exc) is the text after `refused: `."""


def compozy_home():
    return Path(os.environ.get("COMPOZY_HOME") or Path.home() / ".compozy").expanduser()


def short(digest):
    return (digest or "").removeprefix("sha256:")[:12]


def file_sha256(path):
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


RECORD = re.compile(rf"conformance/({'|'.join(ROLES)})-[^/]+\.(json|trace\.jsonl)")


def tree_files(root):
    """Files an adapter ships, relative path -> path. Only the conformance record and its trace are left
    out (they cannot digest themselves); compiled Python is refused, since it would run undigested (F-3)."""
    root = Path(root)
    files = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if "__pycache__" in p.parts or p.suffix in (".pyc", ".pyo"):
            raise Refused(f"adapter {root} contains compiled Python ({rel}); remove it")
        if p.is_file() and not RECORD.fullmatch(rel):
            files[rel] = p
    return files


def tree_digest(root):
    """BR-11: sha256 over the sorted lines `<relative path>\\0<sha256 of bytes>\\n` of every shipped file."""
    lines = "".join(f"{rel}\0{hashlib.sha256(p.read_bytes()).hexdigest()}\n"
                    for rel, p in sorted(tree_files(root).items()))
    return "sha256:" + hashlib.sha256(lines.encode()).hexdigest()


def read_json(path, default):
    path = Path(path)
    return json.loads(path.read_text()) if path.is_file() else default


def write_json(path, data):
    """Atomic replace, so a crash never leaves a half-written record."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def installed_record(home=None):
    return read_json((home or compozy_home()) / "factory" / "installed.json", {})


def bindings(home=None):
    return read_json((home or compozy_home()) / "factory" / "bindings.json", {"bindings": []})["bindings"]


def find_binding(root, role, provider, home=None):
    root = str(Path(root).resolve())
    return next((b for b in bindings(home) if (b["root"], b["role"], b["provider"]) == (root, role, provider)), None)


def custom_configured(conf, role, provider):
    """The custom adapter path factory.toml sets for (role, provider), or None for the bundled one."""
    section = conf.get("roles", {}).get(role, {})
    if role == "signals":
        return (section.get("adapters") or {}).get(provider)
    return section.get("adapter")


def adapter_path(root, value):
    """A configured adapter path (relative to the workspace root, or absolute) -> its directory."""
    path = Path(value).expanduser()
    path = path if path.is_absolute() else Path(root) / path
    return (path.parent if path.name == "SKILL.md" else path).resolve()


def named_skill(text, name):
    """An adapter SKILL.md as installed under the user skill `name`: only the frontmatter `name:` differs."""
    head, sep, body = text[4:].partition("\n---")
    lines = [line for line in head.split("\n") if not line.startswith("name:")]
    return "---\n" + "\n".join([f"name: {name}", *lines]) + sep + body


def check_skill(name, installed, home=None):
    """F-2: the user skill agents load must be the verified tree's SKILL.md (renamed for a custom adapter)."""
    skill = (home or compozy_home()) / "skills" / name / "SKILL.md"
    want = named_skill((Path(installed) / "SKILL.md").read_text(), name)
    if not skill.is_file() or skill.read_text() != want:
        raise Refused(f"skill {skill} differs from the installed adapter's SKILL.md; reinstall the adapter")


def resolve(root, role, provider, conf, home=None):
    """Return (skill name, installed adapter dir) after checking the installed tree, or raise Refused."""
    home = home or compozy_home()
    if custom_configured(conf, role, provider):
        binding = find_binding(root, role, provider, home)
        if not binding:
            raise Refused(f"custom adapter {role}/{provider} has no binding for {Path(root).resolve()}; "
                          f"run bin/install-adapter {role} <path> --workspace <ws>")
        source = Path(binding["source"])
        configured = adapter_path(root, custom_configured(conf, role, provider))
        if configured != source:
            raise Refused(f"custom adapter {role}/{provider} is configured at {configured} but bound to {source}; "
                          f"run bin/install-adapter {role} {configured} --workspace <ws>")
        now = tree_digest(source) if source.is_dir() else "missing"
        if now != binding["tree"]:
            raise Refused(f"custom adapter {role}/{provider} changed (tree {short(now)} ≠ installed "
                          f"{short(binding['tree'])}); run bin/install-adapter")
        installed = Path(binding["installed"])
        if not installed.is_dir() or tree_digest(installed) != binding["tree"]:
            raise Refused(f"installed copy of custom adapter {role}/{provider} changed; run bin/install-adapter")
        return binding["skill"], installed
    name = f"{role}-{provider}"
    installed = home / "factory" / "adapters" / name
    record = installed_record(home).get("adapters", {}).get(name)
    if not record or not installed.is_dir():
        raise Refused(f"adapter {role}/{provider} not installed; run bin/install")
    now = tree_digest(installed)
    if now != record["tree"]:
        raise Refused(f"adapter {role}/{provider} tree {short(now)} ≠ installed {short(record['tree'])}; run bin/install")
    return f"factory-{name}", installed


def compozy(*args):
    """Run the CompozyOS CLI and return parsed JSON (or None for empty output); raises on failure."""
    proc = subprocess.run(["compozy", *args, "-o", "json"], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"compozy {' '.join(args)}: {(proc.stderr or proc.stdout).strip()[:500]}")
    return json.loads(proc.stdout) if proc.stdout.strip() else None


def workspace_root(workspace):
    """A path argument is used as is; a name or ID is resolved through the daemon."""
    path = Path(workspace).expanduser()
    if path.is_dir():
        return path.resolve()
    info = compozy("workspace", "info", workspace)
    root = info.get("root_dir") or info.get("workspace", {}).get("root_dir")
    if not root:
        raise RuntimeError(f"workspace {workspace}: no root_dir in compozy workspace info")
    return Path(root)


def project_conf(root):
    path = Path(root) / ".compozy" / "factory" / "factory.toml"
    if not path.is_file():
        raise Refused(f"{path} missing")
    return tomllib.loads(path.read_text())


def reviewer(workspace, home=None):
    """BR-16: factory-reviewer resolves to the installed user-level definition, with no project shadow."""
    home = home or compozy_home()
    root = workspace_root(workspace)
    shadows = [root / ".compozy" / "agents" / REVIEWER] + sorted((root / ".compozy" / "profiles").glob(f"*/agents/{REVIEWER}"))
    shadow = next((p for p in shadows if p.exists()), None)
    if shadow:
        raise Refused(f"{REVIEWER} is shadowed by {shadow} (project definitions are never trusted)")
    record = installed_record(home).get("reviewer")
    if not record:
        raise Refused(f"{REVIEWER} has no installed digest; run bin/install")
    info = compozy("agent", "info", REVIEWER, "--workspace", str(workspace))
    origin, layer, digest = info.get("origin"), info.get("layer"), info.get("definition_digest")
    if origin == "global" and layer != "user":
        raise Refused(f"{REVIEWER} layer {layer} (expected user)")
    if origin != "global" or digest != record["definition_digest"]:
        raise Refused(f"{REVIEWER} is not the user-level definition (origin {origin}, digest {short(digest)}; "
                      f"installed {short(record['definition_digest'])})")
    agent_md = home / "agents" / REVIEWER / "AGENT.md"
    sha = file_sha256(agent_md) if agent_md.is_file() else "missing"
    if sha != record["file_sha256"]:
        raise Refused(f"{REVIEWER} file {agent_md} changed after install ({short(sha)} ≠ {short(record['file_sha256'])}); run bin/install")
    return f"{REVIEWER} origin global layer user file {short(sha)} digest {short(digest)} (installed)"


def gh_json(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}  # keyring login
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, timeout=60, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])}: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout)


def premerge(repo, pr, base, head):
    """BR-20: re-read base and head right before the merge; any change from the gated values refuses."""
    now = gh_json("pr", "view", str(pr), "--repo", repo, "--json", "baseRefName,headRefOid")
    if now["baseRefName"] != base:
        raise Refused(f"#{pr} base changed {base} -> {now['baseRefName']}")
    if now["headRefOid"] != head:
        raise Refused(f"#{pr} head changed {head} -> {now['headRefOid']}")
    return f"#{pr} base {base} head {head}"


def adapter(role, provider, workspace, home=None):
    """BR-4/BR-21: the adapter's skill name and path, only when the daemon resolves it from the user root."""
    home = home or compozy_home()
    if role not in ROLES:
        raise Refused(f"unknown role {role}")
    root = workspace_root(workspace)
    name, installed = resolve(root, role, provider, project_conf(root), home)
    check_skill(name, installed, home)
    want = home / "skills" / name
    info = compozy("skill", "info", name, "--workspace", str(workspace))
    path = Path(info.get("path") or "")
    if info.get("source") != "user" or (path != want and path != want / "SKILL.md"):
        raise Refused(f"skill {name} resolves to {info.get('source')} {path}, not the installed user skill {want}")
    return f"skill {name} path {want / 'SKILL.md'}"


def main(argv):
    commands = {"reviewer": (reviewer, 1), "premerge": (premerge, 4), "adapter": (adapter, 3)}
    if not argv or argv[0] not in commands or len(argv) - 1 != commands[argv[0]][1]:
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    func, _ = commands[argv[0]]
    try:
        print(f"ok: {func(*argv[1:])}")
        return 0
    except Refused as exc:
        print(f"refused: {exc}")
    except (RuntimeError, OSError, ValueError, KeyError, subprocess.TimeoutExpired) as exc:
        print(f"refused: {argv[0]} check failed ({exc})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
