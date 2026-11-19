"""Shared helpers for the compozy-factory operator scripts (Python 3 stdlib only)."""
import fnmatch
import hashlib
import json
import re
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
from factory_guard import (ROLES, Refused, adapter_path, bindings, check_skill, compozy,  # noqa: E402,F401
                           compozy_home, named_skill, reviewer,
                           custom_configured, file_sha256, find_binding, installed_record, resolve,
                           short, tree_digest, tree_files, workspace_root, write_json)
AGENTS = ["factory-sentinel", "factory-scribe", "factory-dispatcher",
          "factory-pr-watcher", "factory-gatekeeper", "factory-auditor"]
ON_DEMAND = ["factory-reviewer"]  # started per task by another agent; bin/attach never creates an instance
# Keys outside [roles.*] every attached project must define, in the order they are reported.
REQUIRED = [("language", "posts"), ("authorization", "authors")]
OLD_SECTIONS = ("repo", "review", "ci", "deploy", "newrelic", "jira")  # BR-7: the pre-roles grammar
# BR-18: repository paths whose change needs the operator (reviewer major finding, setup preflight mark).
PROTECTED_PATHS = ["REVIEW.md", ".compozy/factory/factory.toml", ".compozy/newrelic.toml", ".compozy/agents/**",
                   ".compozy/profiles/*/agents/**", ".compozy/skills/**", ".compozy/profiles/*/skills/**"]
TYPES = {"string": str, "integer": int, "boolean": bool, "list": list, "table": dict}
POSTS = ("pt-BR", "en")  # languages tools/factory_intake.py has text for


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


def is_protected(path):
    """True when a repository-relative path matches PROTECTED_PATHS (`*` may span directories: safe side)."""
    path = path.removeprefix("./")
    return any(fnmatch.fnmatchcase(path, pattern) for pattern in PROTECTED_PATHS)


def adapter_meta(text):
    """Parse an adapter SKILL.md frontmatter: `key: <JSON or bare string>`, or `key:` + `  sub: <JSON>` lines."""
    if not text.startswith("---\n"):
        raise ValueError("SKILL.md has no frontmatter")
    meta, block = {}, None
    for line in text[4:].split("\n---", 1)[0].splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, raw = line.strip().partition(":")
        target = meta if not line.startswith(" ") else block
        if target is None:
            raise ValueError(f"SKILL.md frontmatter: indented line outside a block: {line!r}")
        raw = raw.strip()
        if not raw and target is meta:
            block = meta[key] = {}
            continue
        try:
            target[key] = json.loads(raw)
        except json.JSONDecodeError:
            target[key] = raw
    return meta


def bundled_adapters():
    """(role, provider) -> adapter dir shipped under skills/."""
    found = {}
    for skill in sorted((REPO_ROOT / "skills").glob("factory-*/SKILL.md")):
        meta = adapter_meta(skill.read_text())
        found[(meta["role"], meta["provider"])] = skill.parent
    return found


def adapters(root, conf):
    """(role, provider) -> (adapter dir, frontmatter, custom: bool) for every configured provider."""
    bundled, found = bundled_adapters(), {}
    for role in ROLES:
        provider = conf["roles"][role]["provider"]
        for p in provider if isinstance(provider, list) else [provider]:
            custom = custom_configured(conf, role, p)
            if custom:
                path = adapter_path(root, custom)
                if not (path / "SKILL.md").is_file():
                    refuse(f"[roles.{role}] adapter {custom}: SKILL.md not found")
                meta = adapter_meta((path / "SKILL.md").read_text())
                if (meta.get("role"), meta.get("provider")) != (role, p):
                    refuse(f"[roles.{role}] adapter {custom} declares {meta.get('role')}/{meta.get('provider')}, "
                           f"expected {role}/{p}")
                found[(role, p)] = (path, meta, True)
            elif (role, p) in bundled:
                found[(role, p)] = (bundled[(role, p)], adapter_meta((bundled[(role, p)] / "SKILL.md").read_text()), False)
            else:
                refuse(f"no adapter for {role}/{p}")
    return found


def _check_value(root, where, key, spec, value):
    kind = TYPES[spec["type"]]
    if not isinstance(value, kind) or (kind is int and isinstance(value, bool)):
        refuse(f"factory.toml {where} {key} must be a {spec['type']}")
    if "values" in spec and value not in spec["values"]:
        refuse(f"{where} {key} must be " + (json.dumps(spec["values"][0]) if len(spec["values"]) == 1
                                             else "one of: " + ", ".join(map(json.dumps, spec["values"]))))
    if "pattern" in spec and not re.fullmatch(spec["pattern"], value):
        refuse(f'factory.toml {where} {key} "{value}" must match {spec["pattern"]}')
    if "file_keys" in spec:  # a per-project file the provider's extension owns (ADR-004)
        path = Path(root) / value
        if not path.is_file():
            refuse(f"factory.toml {where} {key}: {value} not found")
        try:
            data = tomllib.loads(path.read_text())
        except tomllib.TOMLDecodeError as exc:
            refuse(f"factory.toml {where} {key}: {value} invalid TOML ({exc})")
        for name, typ in spec["file_keys"].items():
            got = data.get(name)
            if not isinstance(got, TYPES[typ]) or isinstance(got, bool) or got in ("", 0) or (typ == "integer" and got < 0):
                refuse(f"factory.toml {where} {key}: {value} needs {name} ({typ})")


def _check_roles(root, conf):
    roles = conf.get("roles")
    if not isinstance(roles, dict):
        refuse("factory.toml missing [roles.signals] provider")
    unknown = sorted(set(roles) - set(ROLES))
    if unknown:
        refuse(f"unknown role [roles.{unknown[0]}]")
    for role in ROLES:
        section = roles.get(role)
        provider = section.get("provider") if isinstance(section, dict) else None
        if provider in (None, "", []):
            refuse(f"factory.toml missing [roles.{role}] provider")
        if role == "signals":
            if not (isinstance(provider, list) and all(isinstance(p, str) and p for p in provider)
                    and len(set(provider)) == len(provider)):
                refuse("[roles.signals] provider must be a non-empty list of distinct strings")
            if "adapter" in section:
                refuse("[roles.signals] takes adapters = { <provider> = \"<path>\" }, not adapter")
            extra = sorted(set(section.get("adapters") or {}) - set(provider))
            if extra:
                refuse(f"[roles.signals] adapters names {extra[0]}, which is not a configured provider")
        elif not isinstance(provider, str):
            refuse(f"[roles.{role}] provider must be one string")
        elif "adapters" in section:
            refuse(f"[roles.{role}] takes adapter = \"<path>\", not adapters")
    found = adapters(root, conf)
    for role in ROLES:
        listed = {p for (r, p) in found if r == role}
        stray = sorted(set(roles[role]) - {"provider", "adapter", "adapters", *listed})
        if stray:
            refuse(f"unknown key [roles.{role}] {stray[0]}")
    for (role, provider), (_, meta, _) in found.items():
        where, sub = f"[roles.{role}.{provider}]", roles[role].get(provider)
        schema = meta.get("schema") or {}
        if not isinstance(sub, dict):
            first = next((k for k, s in schema.items() if s.get("required")), None)
            refuse(f"factory.toml missing {where}" + (f" {first}" if first else ""))
        for key in sub:
            if key not in schema:
                refuse(f"unknown key {where} {key}")
        for key, spec in schema.items():
            if sub.get(key) in (None, "", [], {}):
                if spec.get("required"):
                    refuse(f"factory.toml missing {where} {key}")
                continue
            _check_value(root, where, key, spec, sub[key])
    for (role, provider), (_, meta, _) in found.items():  # BR-17 cross-role compatibility
        needs = meta.get("requires_roles") or {}
        if not (isinstance(needs, dict) and all(k in ROLES and isinstance(v, str) for k, v in needs.items())):
            refuse(f"adapter {role}/{provider} requires_roles must map roles ({', '.join(ROLES)}) to one provider")
        for other, want in needs.items():
            got = roles[other]["provider"]
            if want not in (got if isinstance(got, list) else [got]):
                refuse(f'[roles.{role}] {provider} requires [roles.{other}] provider "{want}" (got "{got}")')
    return found


def load_factory_toml(root):
    """Return (path, config) or refuse naming the file or the first missing or invalid key (BR-1..BR-4, BR-7, BR-17)."""
    path = root / ".compozy" / "factory" / "factory.toml"
    if not path.is_file():
        refuse(f"{path} missing")
    try:
        conf = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as exc:
        refuse(f"{path} invalid TOML ({exc})")
    if any(section in conf for section in OLD_SECTIONS):
        refuse("factory.toml uses the old grammar; run bin/migrate-config <workspace>")
    for section, key in REQUIRED:
        if conf.get(section, {}).get(key) in (None, ""):
            refuse(f"{path} missing [{section}] {key}")
    lists = [("authorization", "authors")] + ([("team", "agents")] if "agents" in conf.get("team", {}) else [])
    for section, key in lists:
        value = conf[section][key]
        if not (isinstance(value, list) and value and all(isinstance(v, str) and v.strip() for v in value)):
            refuse(f"{path} [{section}] {key} must be a non-empty list of strings")
    posts = conf["language"]["posts"]
    if posts not in POSTS:
        refuse(f'{path} [language] posts "{posts}" not supported by factory_intake.py ({", ".join(POSTS)})')
    _check_roles(root, conf)
    return path, conf


def contract_operations(role):
    """Operation names of contracts/<role>.md, from the first column of its table."""
    text = (REPO_ROOT / "contracts" / f"{role}.md").read_text()
    return re.findall(rf"^\| `?({role}\.\w+)`? \|", text, re.M)


def record_path(role, provider, adapter_dir, custom):
    """A bundled adapter's record lives in conformance/; a custom one carries its own."""
    return (adapter_dir if custom else REPO_ROOT) / "conformance" / f"{role}-{provider}.json"


def conformance(role, provider, record, installed_dir):
    """BR-11: (supported, reason). The record must match the installed tree and pass every contract operation."""
    if not Path(record).is_file():
        return False, "conformance record missing"
    data = json.loads(Path(record).read_text())
    if (data.get("role"), data.get("provider")) != (role, provider):
        return False, "conformance record is for another adapter"
    if not installed_dir or data.get("adapter_digest") != tree_digest(installed_dir):
        return False, "conformance record stale (adapter digest differs from the installed tree)"
    ops = data.get("operations") or {}
    missing = [op for op in contract_operations(role) if ops.get(op) != "pass"]
    if missing:
        return False, f"conformance record lacks a pass for {missing[0]}"
    if data.get("outcome") != "pass":
        return False, "conformance outcome is not pass"
    return True, "supported"


def key_names(key_format, key, title, slug):
    """Apply a work adapter's key_format: {key} is the work key, {id} the key without a leading `#`."""
    if not re.fullmatch(key_format["regex"], key):
        raise ValueError(f"{key} does not match {key_format['regex']}")
    values = {"key": key, "id": key.lstrip("#")}
    return {"title": key_format["title_prefix"].format(**values) + title,
            "branch": key_format["branch_prefix"].format(**values) + slug,
            "commit_scope": key_format["commit_scope"].format(**values)}


def team(conf):
    return conf.get("team", {}).get("agents") or AGENTS
