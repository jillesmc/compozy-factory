#!/usr/bin/env python3
"""External merge of a protected PR (BR-18 `merged_external`): the dispatcher's only writer of its phases.

Run from the workspace root by the factory-dispatcher. State lives in the PR's work-log entry
(`.compozy/work-log.md`, the line with `pr: #<pr>`) as `external: <phase> <merge-sha>`, `cleanup: done`
and `continue: r<n>`. Phases: external_merge_verified → continue_dispatched → post_merge_done (terminal).
Every command prints the next action, one line, and is safe to repeat:

    factory_external.py merged_external <pr> <sha> --from <operator|session> --pr-get '<code.pr_get JSON>'
    factory_external.py cleaned         <pr> <sha>     # after the cleanup action
    factory_external.py retry           <pr> <sha>     # the 15-min timer wake
    factory_external.py continuing      <pr> <sha> --from <sender agent>
    factory_external.py post_merge_done <pr> <sha> --from <sender agent>

Actions: `cleanup: #<pr> session <id|none> worktree <name|none>` · `send: continue: #<pr> <sha> key <key>`
(to factory-gatekeeper) · `wait: #<pr> post_merge_done` · `done: #<pr> <sha> post_merge_done`.
Refusals (exit 1): `refused: merged_external is operator-only`, `refused: #<pr> not merged into <base>`,
`refused: #<pr> merge sha <given> ≠ <actual>`, … Python 3 stdlib only.
"""
import argparse
import json
import os
import re
import sys
import tomllib
from pathlib import Path

LOG = Path(".compozy/work-log.md")
PHASES = ("external_merge_verified", "continue_dispatched", "post_merge_done")
GATEKEEPER = "factory-gatekeeper"


class Refused(Exception):
    pass


def code_base():
    conf = tomllib.loads(Path(".compozy/factory/factory.toml").read_text())
    code = conf.get("roles", {}).get("code", {})
    base = code.get(code.get("provider"), {}).get("base")
    if not base:
        raise Refused("factory.toml missing [roles.code.<provider>] base")
    return base


# Fields of an entry: the trailing ` | name: value` columns the dispatcher and this script append. The
# title column holds untrusted issue text (H-2): it ends at the first column that opens the dispatcher's
# fields (`source:`, `session:` or `evidence:`), and a reserved name inside it refuses the entry.
OPENING = ("source", "session", "evidence")
RESERVED = re.compile(r"(?:^|[^a-z_])(source|session|worktree|pr|watcher|evidence|external|cleanup|continue): ")


def fields(line):
    """(head columns up to and with the title, {name: value} of the trailing fields); raises Refused."""
    parts = line.split(" | ")
    start = next((i for i in range(3, len(parts)) if parts[i].split(": ", 1)[0] in OPENING), len(parts))
    head, tail = parts[:start], parts[start:]
    if RESERVED.search(" | ".join(head[2:])):
        raise Refused(f"work-log entry {head[0][6:].strip()} has a field name in its title; fix that line by hand")
    found = {}
    for part in tail:
        name, sep, value = part.partition(": ")
        if not sep or not re.fullmatch(r"[a-z_]+", name) or name in found:
            raise Refused(f"work-log entry {head[0][6:].strip()} has a malformed or repeated field {name!r}")
        found[name] = value
    order = list(found)
    if "pr" in found and any(n in found and order.index(n) > order.index("pr") for n in ("session", "worktree")):
        raise Refused(f"work-log entry {head[0][6:].strip()} has pr: before session:/worktree:; fix that line by hand")
    return head, found


def find(lines, pr):
    for i, line in enumerate(lines):
        if line.startswith("- [") and f"pr: #{pr}" in line and fields(line)[1].get("pr") == f"#{pr}":
            return i
    raise Refused(f"#{pr} has no work-log entry (pr: #{pr})")


def set_field(line, name, value):
    head, found = fields(line)
    found.pop(name, None)
    found[name] = value
    return " | ".join(head + [f"{k}: {v}" for k, v in found.items()])


def save(lines):
    tmp = LOG.with_name(LOG.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n")
    os.replace(tmp, LOG)  # atomic: a crash leaves the previous phase, never half a line


def same_sha(given, recorded):
    return len(given) >= 7 and recorded.startswith(given.lower())


def merge_sha(pr_get):
    commit = pr_get.get("merge_commit") or pr_get.get("mergeCommit") or ""
    return (commit.get("oid") or commit.get("sha") or "") if isinstance(commit, dict) else commit


def next_action(line, pr):
    """The action the entry's state asks for; records each continue attempt before it is sent."""
    _, f = fields(line)
    phase, sha = f["external"].split(" ")
    if phase == "post_merge_done":
        return line, f"done: #{pr} {sha} post_merge_done"
    if f.get("cleanup") != "done":
        return line, f"cleanup: #{pr} session {f.get('session', 'none')} worktree {f.get('worktree', 'none')}"
    if phase == "continue_dispatched":
        return line, f"wait: #{pr} post_merge_done"
    attempt = int(f.get("continue", "r0")[1:]) + 1
    line = set_field(line, "continue", f"r{attempt}")
    return line, f"send: continue: #{pr} {sha} key continue-{pr}-{sha[:12]}-r{attempt}"


def step(command, pr, sha, sender=None, pr_get=None):
    if command == "merged_external" and sender != "operator":
        raise Refused("merged_external is operator-only")
    if command in ("continuing", "post_merge_done") and sender != GATEKEEPER:
        raise Refused(f"{command} is accepted only from {GATEKEEPER}")
    lines = LOG.read_text().splitlines() if LOG.is_file() else []
    i = find(lines, pr)
    line = lines[i]
    _, f = fields(line)
    if "external" not in f:
        if command != "merged_external":
            raise Refused(f"#{pr} has no external merge in progress")
        if pr_get is None:
            raise Refused("merged_external needs --pr-get (the code.pr_get result)")
        base, actual = code_base(), merge_sha(pr_get)
        if pr_get.get("state", "").lower() != "merged" or pr_get.get("base") != base or not actual:
            raise Refused(f"#{pr} not merged into {base}")
        if not same_sha(sha, actual.lower()):
            raise Refused(f"#{pr} merge sha {sha} ≠ {actual}")
        line = set_field(line, "external", f"external_merge_verified {actual.lower()}")
    else:
        phase, recorded = f["external"].split(" ")
        if not same_sha(sha, recorded):
            raise Refused(f"#{pr} merge sha {sha} ≠ {recorded}")
        rank = PHASES.index(phase)
        if command == "cleaned":
            line = set_field(line, "cleanup", "done")
        elif command == "continuing" and rank < 1:
            line = set_field(line, "external", f"continue_dispatched {recorded}")
        elif command == "post_merge_done" and rank < 2:
            line = set_field(line, "external", f"post_merge_done {recorded}")
            line = set_field(set_field(line, "cleanup", "done"), "evidence", f"merge {recorded[:12]} (external)")
            line = line.replace("- [~]", "- [x]", 1).replace("- [ ]", "- [x]", 1)
    line, action = next_action(line, pr)
    if line != lines[i]:
        lines[i] = line
        save(lines)
    return action


def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("command", choices=["merged_external", "cleaned", "retry", "continuing", "post_merge_done"])
    parser.add_argument("pr", type=lambda v: int(v.lstrip("#")))
    parser.add_argument("sha")
    parser.add_argument("--from", dest="sender")
    parser.add_argument("--pr-get")
    args = parser.parse_args(argv)
    try:
        pr_get = json.loads(args.pr_get) if args.pr_get else None
        print(step(args.command, args.pr, args.sha, args.sender, pr_get))
        return 0
    except Refused as exc:
        print(f"refused: {exc}")
    except (OSError, ValueError, KeyError, tomllib.TOMLDecodeError) as exc:
        print(f"refused: factory_external check failed ({exc})")
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
