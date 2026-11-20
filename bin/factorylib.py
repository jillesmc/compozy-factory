"""Shared helpers for the compozy-factory operator scripts (Python 3 stdlib only)."""
import fnmatch
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "tools"))
from factory_guard import (ROLES, Refused, adapter_path, bindings, check_skill, cli_error, compozy,  # noqa: E402,F401
                           compozy_home, key_format, named_skill, reviewer,
                           custom_configured, file_sha256, find_binding, installed_record, resolve,
                           short, tree_digest, tree_files, workspace_root, write_json)
from factory_deploy import check_paths, split_globs  # noqa: E402  (the one trigger-set matcher)
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


def _check_value(root, where, key, spec, value, overlay):
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
        if value not in overlay and not path.is_file():
            refuse(f"factory.toml {where} {key}: {value} not found")
        try:
            data = tomllib.loads(overlay[value] if value in overlay else path.read_text())
        except tomllib.TOMLDecodeError as exc:
            refuse(f"factory.toml {where} {key}: {value} invalid TOML ({exc})")
        for name, typ in spec["file_keys"].items():
            got = data.get(name)
            if not isinstance(got, TYPES[typ]) or isinstance(got, bool) or got in ("", 0) or (typ == "integer" and got < 0):
                refuse(f"factory.toml {where} {key}: {value} needs {name} ({typ})")


def _check_roles(root, conf, overlay):
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
        neutral = {"paths"} if role == "deploy" else set()  # provider-neutral role keys
        stray = sorted(set(roles[role]) - {"provider", "adapter", "adapters", *neutral, *listed})
        if stray:
            refuse(f"unknown key [roles.{role}] {stray[0]}")
    problem = check_paths(roles["deploy"].get("paths"))  # the release pipeline's trigger sets; absent: always wait
    if problem:
        refuse(problem)
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
            _check_value(root, where, key, spec, sub[key], overlay)
    for (role, provider), (_, meta, _) in found.items():  # BR-17 cross-role compatibility
        needs = meta.get("requires_roles") or {}
        if not (isinstance(needs, dict) and all(k in ROLES and isinstance(v, str) for k, v in needs.items())):
            refuse(f"adapter {role}/{provider} requires_roles must map roles ({', '.join(ROLES)}) to one provider")
        for other, want in needs.items():
            got = roles[other]["provider"]
            if want not in (got if isinstance(got, list) else [got]):
                refuse(f'[roles.{role}] {provider} requires [roles.{other}] provider "{want}" (got "{got}")')
    return found


def load_factory_toml(root, overlay=None):
    """Return (path, config) or refuse naming the file or the first missing or invalid key (BR-1..BR-4, BR-7, BR-17).
    overlay: {repository-relative path: text} read instead of files not written yet (bin/setup's preflight)."""
    overlay = overlay or {}
    rel = ".compozy/factory/factory.toml"
    path = root / rel
    if rel not in overlay and not path.is_file():
        refuse(f"{path} missing")
    try:
        conf = tomllib.loads(overlay[rel] if rel in overlay else path.read_text())
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
    _check_roles(root, conf, overlay)
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


def verify_adapters(root, conf, allow_unsupported):
    """bin/attach and bin/setup: every configured (role, provider) resolves to its installed, verified tree (custom
    ones through a valid binding) and has a passing conformance record (BR-11). Returns the warnings for unsupported
    adapters when allowed; raises Refused otherwise."""
    warnings = []
    for (role, provider), (src, _, custom) in adapters(root, conf).items():
        name, installed = resolve(root, role, provider, conf)
        check_skill(name, installed)
        ok, reason = conformance(role, provider, record_path(role, provider, src, custom), installed)
        if ok:
            continue
        if not allow_unsupported:
            raise Refused(f"adapter {role}/{provider} is not supported ({reason}); pass --allow-unsupported-adapter")
        warnings.append(f"adapter {role}/{provider} is not supported ({reason})")
    return warnings


def key_names(key_format, key, title, slug):
    """Apply a work adapter's key_format: {key} is the work key, {id} the key without a leading `#`."""
    if not re.fullmatch(key_format["regex"], key):
        raise ValueError(f"{key} does not match {key_format['regex']}")
    values = {"key": key, "id": key.lstrip("#")}
    return {"title": key_format["title_prefix"].format(**values) + title,
            "branch": key_format["branch_prefix"].format(**values) + slug,
            "commit_scope": key_format["commit_scope"].format(**values)}


BOUNDARY = "Operator: no action needed. Reply with one word: ok."


def boundary(agent, ws, instance):
    """Send the new main session its one boundary prompt (compozy/compozy#1815 workaround); False on failure."""
    main = instance.get("main_session_id")
    if not main:
        listed = compozy("agent", "instance", "list", "--workspace", ws, "--agent", agent)
        main = next((i.get("main_session_id") for i in listed.get("instances", [])
                     if i.get("agent_name") == agent and i.get("state") != "archived"), None)
    try:
        if not main:
            raise RuntimeError("no main session")
        key = f"boundary-{instance.get('id', main)}"  # the CLI takes a retry identity only as this pair
        compozy("session", "prompt", main, BOUNDARY, "--message-id", key, "--idempotency-key", key)
    except RuntimeError as exc:
        print(f"refused: boundary {agent} failed ({exc})")
        return False
    print(f"boundary {agent} ok")
    return True


def team(conf):
    return conf.get("team", {}).get("agents") or AGENTS


# ---------------------------------------------------------------- project files as a reviewed PR

def refuse_capacity(listed, new):
    """S7: refuse when creating `new` always-on instances would pass the daemon's limit. `listed` is an
    `agent instance list` result; its active_count is daemon-wide (every workspace)."""
    active, limit = listed.get("active_count"), listed.get("max_active_instances")
    if new and isinstance(active, int) and isinstance(limit, int) and active + new > limit:
        refuse(f"always-on capacity: {active} active + {new} new > {limit} (agents.always_on.max_active_instances); "
               "raise it, then re-run")


def run(*args, cwd=None, check=True):
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}  # keyring login for gh
    proc = subprocess.run(list(args), cwd=cwd, capture_output=True, text=True, env=env)
    if check and proc.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}: {cli_error(proc)}")
    return proc


def _ignore_hits(root, queries, *view):
    """(rel, source file:line, pattern) for each {path: rel} query an ignore rule excludes; a negation
    (`!pattern`) as the last match means not ignored (F7-1). `view` adds --git-dir/--work-tree for another tree."""
    proc = run("git", *view, "check-ignore", "-v", "--no-index", "--", *queries, cwd=str(root), check=False)
    if proc.returncode not in (0, 1):
        raise RuntimeError(f"git check-ignore: {cli_error(proc)}")
    for hit in proc.stdout.splitlines():
        source, path = hit.split("\t", 1)
        where, pattern = re.match(r"(.*:\d+):(.*)", source).groups()
        if not pattern.startswith("!"):
            yield queries[path], where, pattern


def refuse_ignored(root, base, rels, checkout=True):
    """Read-only, no fetch: refuse when `git add` would refuse one of `rels` because an ignore rule excludes it, in
    the PR worktree from origin/<base> (its committed .gitignore files on each path, plus .git/info/exclude and
    core.excludesFile; F7-4) and, with `checkout`, in the checkout too (setup stages the files there). For a file
    origin/<base> already tracks only its directory counts: ignore rules skip tracked files, not ignored dirs."""
    queries = {}
    for rel in rels:
        tracked = run("git", "-C", str(root), "cat-file", "-e", f"origin/{base}:{rel}", check=False).returncode == 0
        parent = str(Path(rel).parent)
        if not tracked:
            queries[rel] = rel
        elif parent != ".":
            queries[parent + "/"] = rel
    if not queries:
        return
    hits = list(_ignore_hits(root, queries)) if checkout else []
    git_dir = run("git", "-C", str(root), "rev-parse", "--absolute-git-dir").stdout.strip()
    real = str(Path(root).resolve()) + os.sep
    with tempfile.TemporaryDirectory() as view:  # the base tree's .gitignore files only, in an empty work tree
        for d in sorted({str(d) for rel in rels for d in Path(rel).parents}):
            ignore = ".gitignore" if d == "." else f"{d}/.gitignore"
            shown = run("git", "-C", str(root), "show", f"origin/{base}:{ignore}", check=False)
            if shown.returncode == 0:
                (Path(view) / ignore).parent.mkdir(parents=True, exist_ok=True)
                (Path(view) / ignore).write_text(shown.stdout)
        for rel, where, pattern in _ignore_hits(view, queries, f"--git-dir={git_dir}", f"--work-tree={view}"):
            if not os.path.isabs(where):
                where = f"origin/{base}:{where}"
            elif where.startswith(real):
                where = where[len(real):]
            hits.append((rel, where, pattern))
    for rel, where, pattern in hits:
        refuse(f"{rel} is ignored by {where} ({pattern}) — narrow that rule, then re-run")


def _remote_branch_files(root, branch, rels):
    """{rel: text} of the branch on origin ("" for a missing file), or None when the branch does not exist."""
    if not run("git", "-C", str(root), "ls-remote", "--heads", "origin", branch).stdout.strip():
        return None
    run("git", "-C", str(root), "fetch", "--quiet", "origin", branch)
    return {rel: run("git", "-C", str(root), "show", f"FETCH_HEAD:{rel}", check=False).stdout for rel in rels}


def _push_branch(root, base, branch, files, title, before):
    """Commit `files` on `branch` from origin/<base> in a temporary worktree (the checkout is untouched) and push."""
    tmp = Path(tempfile.mkdtemp(prefix="factory-pr-"))
    wt = tmp / "wt"
    run("git", "-C", str(root), "worktree", "add", "--quiet", "--detach", str(wt), f"origin/{base}")
    try:
        if before:
            before(wt)
        for rel, text in files.items():
            (wt / rel).parent.mkdir(parents=True, exist_ok=True)
            (wt / rel).write_text(text)
        load_factory_toml(wt)  # the PR carries a file the factory accepts, or nothing is pushed
        run("git", "add", *files, cwd=wt)
        run("git", "commit", "--quiet", "-m", title, cwd=wt)
        print(f"branch     {branch} from origin/{base}, ", end="")
        run("git", "push", "--quiet", "origin", f"HEAD:refs/heads/{branch}", cwd=wt)
        print("pushed")
    finally:
        run("git", "-C", str(root), "worktree", "remove", "--force", str(wt), check=False)
        if tmp.exists() and not any(tmp.iterdir()):
            tmp.rmdir()


def publish(root, base, repo, branch, files, title, body, note, before=None):
    """Push `files` ({rel: text}) on `branch` from origin/<base> (or accept an identical branch an interrupted run
    left, H-5) and open or reuse its PR into <base>. `before(worktree)` may refuse before anything is written."""
    run("git", "-C", str(root), "fetch", "--quiet", "origin", base)
    existing = _remote_branch_files(root, branch, files)
    if existing is None:
        _push_branch(root, base, branch, files, title, before)
    elif existing == files:
        print(f"branch     {branch} already on origin with these files (an earlier run pushed it)")
    else:
        refuse(f"branch {branch} already exists on origin with other files (its PR is pending: "
               "merge it, or delete the branch to start over)")
    listed = json.loads(run("gh", "pr", "list", "--repo", repo, "--head", branch, "--state", "open",
                            "--json", "number").stdout or "[]")
    if listed:
        print(f"pr         #{listed[0]['number']} already open into {base}")
        return listed[0]["number"]
    url = run("gh", "pr", "create", "--repo", repo, "--base", base, "--head", branch,
              "--title", title, "--body", body).stdout.strip()
    number = url.rstrip("/").rsplit("/", 1)[-1]
    print(f"pr         #{number} opened into {base} ({note})")
    return number


# ---------------------------------------------------------------- github-app-review policy (F1, BR-15)
# A Python port of compozy-github-app src/policy.ts (0.3.0, docs/policy.md): the same strict TOML subset
# and schema, so bin/stage-policy stages exactly what the extension honors. tomllib is not used: it accepts
# TOML the extension refuses (e.g. `[repos . "a/b"]`, multi-line arrays, escapes).

POLICY_FILE_ENV, POLICY_DIGEST_ENV = "GITHUB_APP_POLICY_FILE", "GITHUB_APP_POLICY_DIGEST"
_REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_BRANCH = re.compile(r"[A-Za-z0-9._/-]+")
_LOGIN = re.compile(r"[A-Za-z0-9-]+")
_KEY = re.compile(r"[A-Za-z0-9_-]+")
# JS `.` stops at line terminators; JS `in` on `{}` also sees Object.prototype names (refused as duplicates).
_HEADER = re.compile("\\[[ \t]*repos\\.(\"[^\"]*\"|'[^']*')[ \t]*\\][ \t]*(?:#[^\n\r  ]*)?")
_PROTO = {"constructor", "__proto__", "hasOwnProperty", "isPrototypeOf", "propertyIsEnumerable", "toLocaleString",
          "toString", "valueOf", "__defineGetter__", "__defineSetter__", "__lookupGetter__", "__lookupSetter__"}


def policy_paths(env=None):
    """(working copy, versions dir) exactly as the extension resolves them."""
    env = os.environ if env is None else env
    xdg, home = (env.get("XDG_CONFIG_HOME") or "").strip(), (env.get("HOME") or "").strip()
    config = Path(xdg) if xdg and os.path.isabs(xdg) else Path(home if home and os.path.isabs(home)
                                                                else os.path.expanduser("~")) / ".config"
    file = (env.get(POLICY_FILE_ENV) or "").strip()
    if file and not os.path.isabs(file):
        raise ValueError(f"{POLICY_FILE_ENV} must be an absolute path")
    working = Path(file) if file else config / "compozy-github-app" / "policy.toml"
    return working, working.parent / "policies"


def _policy_value(line, i, fail):
    def ch():
        return line[i] if i < len(line) else ""

    def space():
        nonlocal i
        while ch() in (" ", "\t") and ch():
            i += 1

    def text():
        nonlocal i
        quote = ch()
        if quote not in ('"', "'") or not quote:
            fail("expected a quoted string")
        end = line.find(quote, i + 1)
        if end < 0:
            fail("unterminated string")
        value = line[i + 1:end]
        if quote == '"' and "\\" in value:
            fail("escapes are not supported")
        i = end + 1
        return value

    if ch() == "[":
        value, i = [], i + 1
        space()
        while ch() != "]":
            value.append(text())
            space()
            if ch() == ",":
                i += 1
            elif ch() != "]":
                fail("expected , or ] in array")
            space()
        i += 1
    elif ch() == "{":
        value, i = {}, i + 1
        space()
        while ch() != "}":
            match = _KEY.match(line, i)
            if not match:
                fail("expected a key in inline table")
            key = match.group(0)
            if key in value or key in _PROTO:
                fail(f"duplicate key {key}")
            i += len(key)
            space()
            if ch() != "=":
                fail("expected = in inline table")
            i += 1
            space()
            value[key] = text()
            space()
            if ch() == ",":
                i += 1
                space()
                if ch() == "}":
                    fail("trailing comma in inline table")
            elif ch() != "}":
                fail("expected , or } in inline table")
        i += 1
    else:
        value = text()
    space()
    if i < len(line) and line[i] != "#":
        fail("unexpected text after value")
    return value


def parse_policy(data, path):
    """Validate policy bytes; return {lowercase owner/name: {repo, delivery_base, promotion, approvers}}.
    Raises ValueError('<path>[:<line>]: <reason>') on anything the extension refuses (config_invalid)."""
    try:
        source = data.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError(f"{path}: not valid UTF-8") from None
    if source.startswith("﻿"):
        raise ValueError(f"{path}: byte order mark (BOM) not allowed")
    raw, current = {}, None
    for index, full in enumerate(source.split("\n")):
        line = full[:-1] if full.endswith("\r") else full

        def fail(message, index=index):
            raise ValueError(f"{path}:{index + 1}: {message}")

        if re.search("[\x00-\x08\x0b-\x1f\x7f]", line):
            fail("control character")
        trimmed = re.sub(r"^[ \t]+|[ \t]+$", "", line)
        if trimmed == "" or trimmed.startswith("#"):
            continue
        header = _HEADER.fullmatch(trimmed)
        if header:
            repo = header.group(1)[1:-1]
            if not _REPO.fullmatch(repo):
                fail(f'repos."{repo}": expected owner/name')
            if repo.lower() in raw:
                fail(f'repos."{repo}": duplicate repository')
            current = {}
            raw[repo.lower()] = (repo, index + 1, current)
            continue
        if trimmed.startswith("["):
            fail('only [repos."owner/name"] tables are allowed')
        if current is None:
            fail('key outside a [repos."owner/name"] table')
        assignment = re.match(r"([A-Za-z0-9_-]+)[ \t]*=[ \t]*", trimmed) or fail("expected key = value")
        key = assignment.group(1)
        if key in current or key in _PROTO:
            fail(f"duplicate key {key}")
        current[key] = _policy_value(trimmed, assignment.end(), fail)
    repos = {}
    for lower, (repo, number, fields) in raw.items():
        def fail(message, repo=repo, number=number):
            raise ValueError(f'{path}:{number}: repos."{repo}": {message}')

        unknown = next((k for k in fields if k not in ("delivery_base", "promotion", "approvers")), None)
        if unknown:
            fail(f"unknown key {unknown}")

        def branch(value, name):
            return value if isinstance(value, str) and _BRANCH.fullmatch(value) else fail(
                f"{name} must be a branch name ([A-Za-z0-9._/-]+)")
        base, promotion = branch(fields.get("delivery_base"), "delivery_base"), None
        if "promotion" in fields:
            p = fields["promotion"]
            if not isinstance(p, dict) or sorted(p) != ["base", "head"]:
                fail('promotion must be { head = "…", base = "…" }')
            promotion = {"head": branch(p["head"], "promotion.head"), "base": branch(p["base"], "promotion.base")}
            if promotion["head"] != base:
                fail(f"promotion.head must equal delivery_base ({base})")
            if promotion["base"] == base:
                fail("promotion.base must differ from delivery_base")
        approvers = fields.get("approvers", [])
        if not isinstance(approvers, list) or any(not _LOGIN.fullmatch(a) for a in approvers):
            fail("approvers must be an array of GitHub logins")
        if promotion and not approvers:
            fail("approvers are required when promotion is set")
        repos[lower] = {"repo": repo, "delivery_base": base, "promotion": promotion, "approvers": approvers}
    return repos


def activate_line(digest):
    return (f"printf {digest} | compozy extension secrets set github-app-review "
            f"--env {POLICY_DIGEST_ENV} --value-stdin")


def policy_block(repo, base, release, approvers):
    return (f'[repos."{repo}"]\ndelivery_base = "{base}"\npromotion = {{ head = "{base}", base = "{release}" }}\n'
            f"approvers = [{', '.join(json.dumps(a) for a in approvers)}]\n")


def plan_policy(repo, block):
    """(working path, new working-copy bytes or None when it already holds this block); refuses a conflict."""
    working, _ = policy_paths()
    current = working.read_bytes() if working.is_file() else b""
    want = parse_policy(block.encode(), "block")[repo.lower()]
    have = parse_policy(current, working).get(repo.lower()) if current else None
    if have == want:
        return working, None
    if have:
        refuse(f'policy working copy {working} already has [repos."{have["repo"]}"] with other values; '
               "edit it by hand, then run bin/stage-policy")
    sep = b"" if not current or current.endswith(b"\n\n") else b"\n" if current.endswith(b"\n") else b"\n\n"
    data = current + sep + block.encode()
    parse_policy(data, working)
    return working, data


def ask_approvers(repo):
    try:
        answer = input(f"approvers  promotion approvers for {repo} (comma-separated GitHub logins): ")
    except EOFError:
        refuse("approvers: no answer (end of input)")
    logins = [a.strip().lstrip("@") for a in answer.split(",") if a.strip()]
    if not logins or any(not re.fullmatch(r"[A-Za-z0-9-]+", a) for a in logins):
        refuse("approvers must be one or more GitHub logins separated by commas")
    return logins


NO_DEPLOY_PATHS = ("deploy paths absent: every train waits for a Deployment (add [roles.deploy] paths in a PR to "
                   "close trains that trigger no pipeline at their merge)")


NO_WORK_KEY = "no work key in the PR title: some repos require one (pass --work-key <KEY>)"


def work_key(conf, key, tty, root):
    """The PR's work key: --work-key, else asked on a terminal (empty: none). It must match the work adapter's
    `key_format.regex`, read from the resolved installed adapter (custom or overridden adapters define it, BR-5),
    else from the bundled adapter; a key with whitespace is always refused (F7-2)."""
    if key is None and tty:
        try:
            key = input("work key   for the PR title (e.g. PROJ-123 or #123; empty: none): ")
        except EOFError:
            key = None
    key = (key or "").strip()
    if not key:
        return None
    if re.search(r"\s", key):
        refuse(f"work key {key!r} contains whitespace; fix --work-key, then re-run")
    provider = conf.get("roles", {}).get("work", {}).get("provider")
    try:
        found = key_format(resolve(root, "work", provider, conf)[1])
    except Refused:  # not installed yet (migrate-config runs before bin/install): the bundled adapter decides
        found = None
    bundled = REPO_ROOT / "skills" / f"factory-work-{provider}"
    regex = (found or {}).get("regex") or ((key_format(bundled) or {}).get("regex") if bundled.is_dir() else None)
    if regex and not re.search(regex, key):
        refuse(f"work key {key!r} does not match the work/{provider} adapter's key_format {regex}; fix --work-key, "
               "then re-run")
    return key


def pr_title(title, key):
    """`chore: <KEY> <text>` when a work key is given (the org's PR title checks), else the title as is."""
    return title.replace("chore: ", f"chore: {key} ", 1) if key else title


def ask_deploy_paths():
    """[roles.deploy] paths from the operator's terminal, one trigger set at a time (the release pipeline's
    `file_paths` globs, comma-separated outside `{}` and `[]`); an empty includes (or end of input) ends the list.
    None when no set."""
    def globs(prompt):
        try:
            return split_globs(input(prompt))
        except EOFError:
            return []
    hint = "comma-separated globs; a comma inside {} or [] stays in its glob"
    sets = []
    while includes := globs(f"deploy     trigger set {len(sets) + 1} includes ({hint}; empty: done): "):
        excludes = globs(f"deploy     trigger set {len(sets) + 1} excludes ({hint}; empty: none): ")
        sets.append({"includes": includes, **({"excludes": excludes} if excludes else {})})
    return sets or None


def deploy_paths_lines(sets):
    """The preflight lines of [roles.deploy] paths (or the note that they are absent)."""
    if not sets:
        return [NO_DEPLOY_PATHS]
    return [f"trigger set {n}: includes {', '.join(s['includes'])}"
            + (f"; excludes {', '.join(s['excludes'])}" if s.get("excludes") else "") for n, s in enumerate(sets, 1)]


def write_working_copy(working, data):
    working.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = working.with_name(working.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, working)


def stage_policy(env=None):
    """Byte-copy the schema-checked working copy to policies/<sha256>.toml (dir 0700, file 0444, never
    overwritten). Returns (status staged|unchanged, digest, version path); raises ValueError."""
    working, versions = policy_paths(env)
    if not working.is_file():
        raise ValueError(f"{working} does not exist")
    data = working.read_bytes()
    parse_policy(data, working)
    digest = hashlib.sha256(data).hexdigest()
    path = versions / f"{digest}.toml"
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError(f"{path} exists with other bytes; the operator removes it")
        return "unchanged", digest, path
    versions.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(fd, "wb") as out:
            out.write(data)
        os.link(tmp, path)  # never replaces an existing version (unlike rename)
    except OSError as exc:
        raise ValueError(f"{path} could not be written ({exc.strerror})") from None
    finally:
        tmp.unlink(missing_ok=True)  # like the extension: a stale or failed temp file is removed, nothing staged
    return "staged", digest, path
