#!/usr/bin/env python3
"""Check-in source: open issues labeled `factory:deliver` by an authorized author.

One item per issue whose LAST `factory:deliver` label event was made by a login in
.compozy/factory/factory.toml [authorization].authors. Keyed by issue number + label
event id, so an unchanged set skips the model and a re-label wakes it again.

Read-only: GitHub REST GETs through the operator's keyring `gh` login.
Contract: CompozyOS check-in source v1 (JSON on stdin, {"items": [...]} on stdout), run by the
provider-neutral factory-dispatcher/factory-auditor source after its tree-digest check.
"""
import json
import os
import subprocess
import sys
import tomllib
from pathlib import Path

LABEL = "factory:deliver"


def factory_value(section, key):
    """Read one factory.toml value (`section` may be dotted); sources run with the workspace root as cwd."""
    path = Path(".compozy/factory/factory.toml")
    if not path.is_file():
        raise RuntimeError(f"refused: {path} missing")
    value = tomllib.loads(path.read_text())
    for part in section.split("."):
        value = value.get(part, {}) if isinstance(value, dict) else {}
    value = value.get(key) if isinstance(value, dict) else None
    if value in (None, "", []):
        raise RuntimeError(f"refused: factory.toml missing [{section}] {key}")
    return value


def gh(path):
    env = {k: v for k, v in os.environ.items() if k != "GH_TOKEN"}
    proc = subprocess.run(["gh", "api", "--paginate", "--slurp", path],
                          capture_output=True, text=True, timeout=25, env=env)
    if proc.returncode != 0:
        raise RuntimeError(f"gh api {path}: {proc.stderr.strip()[:300]}")
    return [e for page in json.loads(proc.stdout or "[]") for e in page]


def main():
    raw = sys.stdin.read()
    request = json.loads(raw) if raw.strip() else {}
    authors = set(factory_value("authorization", "authors"))
    repo = factory_value("roles.code.github", "repo")
    max_items = int((request.get("limits") or {}).get("max_items") or 20)

    issues = [i for i in gh(f"repos/{repo}/issues?state=open&labels={LABEL}&per_page=100")
              if "pull_request" not in i]
    items = []
    for issue in sorted(issues, key=lambda i: i["number"]):
        events = [e for e in gh(f"repos/{repo}/issues/{issue['number']}/events?per_page=100")
                  if e.get("event") == "labeled" and (e.get("label") or {}).get("name") == LABEL]
        if not events or (events[-1].get("actor") or {}).get("login") not in authors:
            continue
        labels = [l["name"] for l in issue.get("labels", [])]
        items.append({
            "key": f"issue-{issue['number']}@{events[-1]['id']}",
            "title": (issue.get("title") or f"#{issue['number']}")[:200],
            "url": issue.get("html_url", ""),
            "attributes": {
                "number": issue["number"],
                "labeled_by": events[-1]["actor"]["login"],
                "labeled_at": events[-1].get("created_at", ""),
                "in_progress": "factory:in-progress" in labels,
                "blocked": "factory:blocked" in labels,
            },
        })
    result = {"items": items[:max_items]}
    if len(items) > max_items:
        result["more"] = True
    json.dump(result, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"deliver: {exc}", file=sys.stderr)
        sys.exit(1)
