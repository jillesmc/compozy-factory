"""Shared helpers for the compozy-factory operator scripts (Python 3 stdlib only)."""
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS = ["factory-sentinel", "factory-scribe", "factory-dispatcher",
          "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]
ON_DEMAND = ["factory-reviewer"]  # started per task by another agent; bin/attach never creates an instance
# BR-5: keys every attached project must define, in the order they are reported.
REQUIRED = [("language", "posts"), ("authorization", "authors"),
            ("repo", "github"), ("repo", "base"), ("repo", "release"), ("repo", "merge"),
            ("ci", "runner"), ("newrelic", "account_id"), ("newrelic", "app_name"),
            ("jira", "site"), ("jira", "project"), ("jira", "fields"), ("jira", "defaults"),
            ("review", "extension"), ("review", "repo"), ("review", "agent")]
POSTS = ("pt-BR", "en")  # languages tools/factory_intake.py has text for


def compozy_home():
    return Path(os.environ.get("COMPOZY_HOME") or Path.home() / ".compozy").expanduser()


def refuse(message):
    print(f"refused: {message}")
    sys.exit(1)


def script_hash(path):
    """Same value the daemon demands for `heartbeat sources approve --sha256`."""
    return "sha256:" + hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _scalar(raw):
    raw = raw.strip()
    if raw.startswith('"'):
        return json.loads(raw)
    if raw.startswith("'"):
        return raw[1:-1].replace("''", "'")
    if raw in ("true", "false"):
        return raw == "true"
    return raw


def heartbeat_actions(text):
    """Parse the `actions:` list of a HEARTBEAT.md frontmatter (flat scalar maps only)."""
    front = text.split("\n---", 1)[0]
    actions, inside = [], False
    for line in front.splitlines():
        if line.startswith("check_ins:"):
            # ponytail: check_ins blocks are not hashed here; no factory agent declares one.
            raise ValueError("check_ins authority blocks are not supported by bin/install")
        if not line.startswith(" ") and line.strip():
            inside = line.startswith("actions:")
            continue
        if not inside or not line.strip():
            continue
        item = line.strip()
        if item.startswith("- "):
            actions.append({})
            item = item[2:]
        key, _, value = item.partition(":")
        actions[-1][key.strip()] = _scalar(value)
    return actions


def authority_digest(heartbeat_text):
    """Mirror of CompozyOS heartbeat.AuthorityDigest for blocks without check_ins or MCP read-only tools."""
    actions = heartbeat_actions(heartbeat_text)
    if not actions:
        return ""
    ordered = [{"id": a.get("id", ""), "title": a.get("title", ""),
                "instructions": a.get("instructions", ""), "auto_accept": bool(a.get("auto_accept", False))}
               for a in sorted(actions, key=lambda a: a.get("id", ""))]
    data = json.dumps({"check_ins": {}, "actions": ordered}, separators=(",", ":"), ensure_ascii=False)
    # Go's encoding/json escapes HTML characters and line/paragraph separators.
    for char, escaped in (("<", "\\u003c"), (">", "\\u003e"), ("&", "\\u0026"),
                          (" ", "\\u2028"), (" ", "\\u2029")):
        data = data.replace(char, escaped)
    return "sha256:" + hashlib.sha256(data.encode()).hexdigest()


def frontmatter_field(text, key):
    """Read one top-level scalar or `- item` list from AGENT.md frontmatter."""
    front = text.split("\n---", 1)[0].split("---\n", 1)[-1]
    lines = front.splitlines()
    for i, line in enumerate(lines):
        if line.startswith(f"{key}:"):
            value = line.split(":", 1)[1]
            if value.strip():
                return _scalar(value)
            items = []
            for nxt in lines[i + 1:]:
                if not nxt.startswith("  - "):
                    break
                items.append(_scalar(nxt[4:]))
            return items
    return None


def approve_source_command(agent, script, digest):
    # Approvals are keyed by definition dir; @user targets the global definition from any cwd.
    return f"compozy agent heartbeat sources approve {agent} {script} --sha256 {digest} --workspace @user --yes"


def approve_authority_command(agent, digest):
    return f"compozy agent heartbeat authority approve {agent} --sha256 {digest} --workspace @user --yes"


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


def load_factory_toml(root):
    """Return (path, config) or refuse naming the file or the first missing BR-5 key."""
    path = root / ".compozy" / "factory" / "factory.toml"
    if not path.is_file():
        refuse(f"{path} missing")
    try:
        conf = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        refuse(f"{path} invalid TOML ({exc})")
    for section, key in REQUIRED:
        if conf.get(section, {}).get(key) in (None, ""):
            refuse(f"{path} missing [{section}] {key}")
    lists = [("authorization", "authors")] + ([("team", "agents")] if "agents" in conf.get("team", {}) else [])
    for section, key in lists:
        value = conf[section][key]
        if not (isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value)):
            refuse(f"{path} [{section}] {key} must be a non-empty list of strings")
    for key in ("fields", "defaults"):
        if not isinstance(conf["jira"][key], dict):
            refuse(f"{path} [jira] {key} must be a table")
        if not conf["jira"][key]:
            refuse(f"{path} missing [jira] {key}")
    account = conf["newrelic"]["account_id"]
    if isinstance(account, bool) or not isinstance(account, int) or account <= 0:
        refuse(f"{path} [newrelic] account_id must be a positive integer")
    if conf["repo"]["merge"] != "merge":
        refuse(f'{path} [repo] merge must be "merge"')
    review = conf["review"]
    for key in ("extension", "repo", "agent"):
        if not isinstance(review[key], str) or not review[key].strip():
            refuse(f"{path} [review] {key} must be a non-empty string")
    if review["extension"] != "github-app-review":  # every agent calls ext__github_app_review__* tools
        refuse(f'{path} [review] extension must be "github-app-review"')
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", review["repo"]):
        refuse(f'{path} [review] repo "{review["repo"]}" must be owner/name')
    if review["repo"] != conf["repo"]["github"]:
        refuse(f'{path} [review] repo must equal [repo] github "{conf["repo"]["github"]}"')
    if review["agent"] not in ON_DEMAND:
        refuse(f'{path} [review] agent must be one of: {", ".join(ON_DEMAND)}')
    posts = conf["language"]["posts"]
    if posts not in POSTS:
        refuse(f'{path} [language] posts "{posts}" not supported by factory_intake.py ({", ".join(POSTS)})')
    return path, conf


def team(conf):
    return conf.get("team", {}).get("agents") or AGENTS
