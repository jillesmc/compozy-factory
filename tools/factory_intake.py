#!/usr/bin/env python3
"""Turn one New Relic finding (sent by the factory-sentinel agent) into a CompozyOS task + mirrored GitHub issue.

Run from the workspace root. Reads `.compozy/factory/factory.toml` ([repo] github, [language] posts).

Usage (finding JSON on stdin, the finding object the factory-sentinel builds):
    factory_intake.py check  --app <app_name>   -> {"duplicate": bool, "fingerprint", "issue_url", "task_dir"}
    factory_intake.py create --app <app_name>   -> {"task_slug", "task_dir", "issue_number", "issue_url"}

Dedupe key: app + error_class + transaction. A finding is a duplicate when an OPEN issue labelled
`factory` (or the legacy `fabrica`) or a not-completed task under .compozy/tasks/ already carries its fingerprint.
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path.cwd()  # the workspace root; prompts run tools from there
TASKS = ROOT / ".compozy" / "tasks"
LABEL = "factory"
COMPLETED = {"completed", "complete", "done", "finished"}
COMPLEXITY = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
REPO = ""        # [repo] github, set by main()
LANG = "pt-BR"   # [language] posts, set by main()
# Posted text follows [language] posts; pt-BR keeps the original wording, anything else gets English.
TEXT = {
    "pt-BR": {"intro": "Achado do New Relic (factory-sentinel) em", "field": "Campo", "value": "Valor",
              "severity": "Severidade", "count": "Ocorrências", "window": "Janela", "transaction": "Transação",
              "area": "Área suspeita", "proposal": "Proposta", "sample": "Amostra e NRQL",
              "label": "Achado automático da factory", "comment": "🏭 **Task criada** em `{task}`. Próximo marco: QA ou PR."},
    "en": {"intro": "New Relic finding (factory-sentinel) in", "field": "Field", "value": "Value",
           "severity": "Severity", "count": "Occurrences", "window": "Window", "transaction": "Transaction",
           "area": "Suspected area", "proposal": "Proposal", "sample": "Sample and NRQL",
           "label": "Automatic factory finding", "comment": "🏭 **Task created** at `{task}`. Next milestone: QA or PR."},
}


def text(key):
    return TEXT.get(LANG, TEXT["en"])[key]


def factory_value(section, key):
    path = ROOT / ".compozy" / "factory" / "factory.toml"
    if not path.is_file():
        sys.exit(f"refused: {path} missing")
    value = tomllib.loads(path.read_text()).get(section, {}).get(key)
    if value in (None, "", []):
        sys.exit(f"refused: factory.toml missing [{section}] {key}")
    return value


def fingerprint(app, f):
    key = "|".join(s.strip().lower() for s in (app, f["error_class"], f["transaction"]))
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def slug_for(f, fp):
    words = re.sub(r"[^a-z0-9]+", "-", f["title"].lower()).strip("-")[:40].rstrip("-")
    return f"fab-{fp[:6]}-{words}"


def gh(*args, stdin=None):
    # The shell GH_TOKEN is fine-grained and gets 403 on issue writes; the keyring login is classic (repo scope).
    env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GITHUB_TOKEN")}
    r = subprocess.run(["gh", *args], input=stdin, capture_output=True, text=True, cwd=ROOT, env=env)
    if r.returncode:
        sys.exit(f"gh {' '.join(args[:3])} failed: {r.stderr.strip()}")
    return r.stdout


def open_issue(fp):
    issues = json.loads(gh("issue", "list", "--repo", REPO, "--label", LABEL, "--state", "open", "--limit", "200",
                           "--json", "number,url,body"))
    return next((i for i in issues if any(f"{p}-fingerprint: {fp}" in (i["body"] or "") for p in ("factory", "fabrica"))), None)


def open_task(fp):
    for path in TASKS.glob("*/task_*.md"):
        head = path.read_text().split("\n---", 1)[0]
        status = re.search(r"^status:\s*(\S+)", head, re.M)
        if f"fingerprint: \"{fp}\"" in head and (status is None or status.group(1).lower() not in COMPLETED):
            return path.parent
    return None


def yq(s):
    return '"' + s.replace("\\", "\\\\").replace('"', '\\"') + '"'


def render_task(f, app, fp, slug, issue_url):
    title = f"Task 1: {f['title']}"
    return f"""---
status: pending
title: {yq(title)}
type: bugfix
complexity: {COMPLEXITY.get(f['severity'], 'medium')}
severity: {yq(f['severity'])}
fingerprint: {yq(fp)}
issue: {yq(issue_url)}
app: {yq(app)}
error_class: {yq(f['error_class'])}
transaction: {yq(f['transaction'])}
---

# {title}

## Overview

New Relic flagged this in `{app}` (severity {f['severity']}, {f.get('count', '?')} occurrence(s),
{f.get('first_seen', '?')} → {f.get('last_seen', '?')}). Mirrored issue: {issue_url}

## Shippable Outcome

- Outcome: {f.get('proposed_task', 'see the evidence below')}
- Verify in this task: a regression test that reproduces the failure path and passes after the fix.
- Integration verification: after deploy, the evidence NRQL below returns no new occurrences.

## Evidence

- Error class: `{f['error_class']}`
- Transaction: `{f['transaction']}`
- Suspected area: {f.get('suspected_area', 'unknown')}

Sample:

```text
{f.get('sample', '')}
```

NRQL:

```sql
{f['evidence_nrql']}
```

## Subtasks

- [ ] 1.1 Reproduce the failure path in a test.
- [ ] 1.2 Fix the root cause in the suspected area.
- [ ] 1.3 Run the affected package's test suite.
"""


def render_issue(f, app, fp, task_rel):
    return f"""{text('intro')} **{app}**.

| {text('field')} | {text('value')} |
|---|---|
| {text('severity')} | {f['severity']} |
| {text('count')} | {f.get('count', '?')} |
| {text('window')} | {f.get('first_seen', '?')} → {f.get('last_seen', '?')} |
| Error class | `{f['error_class']}` |
| {text('transaction')} | `{f['transaction']}` |
| {text('area')} | {f.get('suspected_area', '?')} |

**{text('proposal')}:** {f.get('proposed_task', '')}

<details><summary>{text('sample')}</summary>

```text
{f.get('sample', '')}
```

```sql
{f['evidence_nrql']}
```
</details>

Task CompozyOS: `{task_rel}`

<!-- factory-fingerprint: {fp} -->
"""


def check(app, f):
    fp = fingerprint(app, f)
    issue, task = open_issue(fp), open_task(fp)
    return {"duplicate": bool(issue or task), "fingerprint": fp,
            "issue_url": issue["url"] if issue else "", "task_dir": str(task.relative_to(ROOT)) if task else ""}


def create(app, f):
    found = check(app, f)
    if found["duplicate"]:
        sys.exit(f"duplicate finding, refusing to create: {json.dumps(found)}")
    fp = found["fingerprint"]
    slug = slug_for(f, fp)
    task_dir = TASKS / slug
    task_rel = f".compozy/tasks/{slug}/task_01.md"
    gh("label", "create", LABEL, "--repo", REPO, "--color", "5319E7", "--description", text("label"), "--force")
    # ponytail: issue first, then local files; if the local write fails the open issue still dedupes, fix by hand.
    issue = json.loads(gh("api", "-X", "POST", f"repos/{REPO}/issues", "--input", "-",
                          stdin=json.dumps({"title": f"[factory] {f['title']}", "body": render_issue(f, app, fp, task_rel),
                                            "labels": [LABEL, "bug"]})))
    task_dir.mkdir(parents=True)
    (task_dir / "task_01.md").write_text(render_task(f, app, fp, slug, issue["html_url"]))
    (task_dir / "_tasks.md").write_text(
        f'---\nschema_version: "compozy.tasks/v2"\nworkflow: {slug}\ngraph:\n  nodes:\n'
        f"    - id: task_01\n      file: task_01.md\n  edges: []\n---\n\n# {slug}\n\n"
        f"| Task | Title | Status |\n|---|---|---|\n| task_01 | {f['title']} | pending |\n")
    gh("api", "-X", "POST", f"repos/{REPO}/issues/{issue['number']}/comments", "-f",
       "body=" + text("comment").format(task=task_rel))
    return {"task_slug": slug, "task_dir": str(task_dir.relative_to(ROOT)),
            "issue_number": issue["number"], "issue_url": issue["html_url"]}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "create"])
    p.add_argument("--app", required=True)
    a = p.parse_args()
    global REPO, LANG
    REPO, LANG = factory_value("repo", "github"), factory_value("language", "posts")
    f = json.load(sys.stdin)
    missing = [k for k in ("title", "severity", "error_class", "transaction", "evidence_nrql") if not f.get(k)]
    if missing or not a.app.strip():
        sys.exit(f"finding is missing required fields: {missing or ['--app']}")
    print(json.dumps((check if a.cmd == "check" else create)(a.app, f)))


if __name__ == "__main__":
    main()
