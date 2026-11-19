#!/usr/bin/env python3
"""Turn one signals finding (sent by the factory-sentinel) into a CompozyOS task + mirrored code issue.

Run from the workspace root. Reads `.compozy/factory/factory.toml`: `[language] posts`, `[roles.code]`
(issues are created with the code/github operations code.label_ensure, code.issue_create and
code.issue_comment, so `[roles.code] provider` must be "github") and `[roles.work]`.

Usage (finding JSON on stdin: the finding the factory-sentinel builds, with its `envelope`, the
factory.finding/v1 envelope of `factory_envelope.py wrap`):
    factory_intake.py check   -> {"duplicate": bool, "fingerprint", "issue_url", "task_dir"}
    factory_intake.py create  -> {"task_slug", "task_dir", "issue_number", "issue_url", "work_key"}

The service (e.g. the New Relic app), error class and location come from the envelope's
`fingerprint_inputs` (the signals adapter's normalize); the factory computes the fingerprint
(factory_envelope.fingerprint, equal to the former app + error_class + transaction key). A finding is a
duplicate when an OPEN issue labelled `factory` (or the legacy `fabrica`) or a not-completed task under
.compozy/tasks/ already carries its fingerprint. When `[roles.work] provider` equals the code provider
(BR-5) the issue is the work item: `create` titles it with the work adapter's `key_format` (`#<n>: …`,
read from the installed, digest-checked adapter) and returns that `work_key`; otherwise `work_key` is
empty and the scribe performs work.create.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import factory_envelope as fe  # noqa: E402
import factory_guard as fg  # noqa: E402

ROOT = Path.cwd()  # the workspace root; prompts run tools from there
TASKS = ROOT / ".compozy" / "tasks"
LABEL = "factory"
COMPLETED = {"completed", "complete", "done", "finished"}
COMPLEXITY = {"critical": "high", "high": "high", "medium": "medium", "low": "low"}
REPO = ""        # [roles.code.github] repo, set by main()
LANG = "pt-BR"   # [language] posts, set by main()
# Posted text follows [language] posts; only these languages are supported (others are refused).
TEXT = {
    "pt-BR": {"intro": "Achado de {provider} (factory-sentinel) em", "field": "Campo", "value": "Valor",
              "severity": "Severidade", "count": "Ocorrências", "window": "Janela", "transaction": "Transação",
              "area": "Área suspeita", "proposal": "Proposta", "sample": "Amostra e consulta",
              "label": "Achado automático da factory", "comment": "🏭 **Task criada** em `{task}`. Próximo marco: QA ou PR."},
    "en": {"intro": "{provider} finding (factory-sentinel) in", "field": "Field", "value": "Value",
           "severity": "Severity", "count": "Occurrences", "window": "Window", "transaction": "Transaction",
           "area": "Suspected area", "proposal": "Proposal", "sample": "Sample and query",
           "label": "Automatic factory finding", "comment": "🏭 **Task created** at `{task}`. Next milestone: QA or PR."},
}


def text(key):
    return TEXT[LANG][key]


def load_conf():
    path = ROOT / ".compozy" / "factory" / "factory.toml"
    if not path.is_file():
        sys.exit(f"refused: {path} missing")
    return tomllib.loads(path.read_text())


def need(conf, section, key):
    value = conf
    for part in section.split("."):
        value = value.get(part, {}) if isinstance(value, dict) else {}
    value = value.get(key) if isinstance(value, dict) else None
    if value in (None, "", []):
        sys.exit(f"refused: factory.toml missing [{section}] {key}")
    return value


def facts_of(env):
    return {"kind": "bugfix", "attached_doc": False, "origin": env["provider"], "touches_ci": False}


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


def render_task(f, env, fp, slug, issue_url, work_key):
    app, inputs = env["fingerprint_inputs"]["service"], env["fingerprint_inputs"]
    title = f"Task 1: {f['title']}"
    return f"""---
status: pending
title: {yq(title)}
type: bugfix
complexity: {COMPLEXITY.get(f['severity'], 'medium')}
severity: {yq(f['severity'])}
fingerprint: {yq(fp)}
issue: {yq(issue_url)}
work_key: {yq(work_key)}
signals_provider: {yq(env['provider'])}
service: {yq(app)}
error_class: {yq(inputs['error_class'])}
location: {yq(inputs['location'])}
---

# {title}

## Overview

{env['provider']} flagged this in `{app}` (severity {f['severity']}, {f.get('count', '?')} occurrence(s),
{f.get('first_seen', '?')} → {f.get('last_seen', '?')}). Mirrored issue: {issue_url}

## Shippable Outcome

- Outcome: {f.get('proposed_task', 'see the evidence below')}
- Verify in this task: a regression test that reproduces the failure path and passes after the fix.
- Integration verification: after deploy, the evidence query below returns no new occurrences.

## Evidence

- Error class: `{inputs['error_class']}`
- Location: `{inputs['location']}`
- Suspected area: {f.get('suspected_area', 'unknown')}

Sample:

```text
{f.get('sample', '')}
```

Query:

```sql
{f['evidence_query']}
```

## Subtasks

- [ ] 1.1 Reproduce the failure path in a test.
- [ ] 1.2 Fix the root cause in the suspected area.
- [ ] 1.3 Run the affected package's test suite.
"""


def render_issue(f, env, fp, task_rel):
    app, inputs = env["fingerprint_inputs"]["service"], env["fingerprint_inputs"]
    return f"""{text('intro').format(provider=env['provider'])} **{app}**.

| {text('field')} | {text('value')} |
|---|---|
| {text('severity')} | {f['severity']} |
| {text('count')} | {f.get('count', '?')} |
| {text('window')} | {f.get('first_seen', '?')} → {f.get('last_seen', '?')} |
| Error class | `{inputs['error_class']}` |
| {text('transaction')} | `{inputs['location']}` |
| {text('area')} | {f.get('suspected_area', '?')} |

**{text('proposal')}:** {f.get('proposed_task', '')}

<details><summary>{text('sample')}</summary>

```text
{f.get('sample', '')}
```

```sql
{f['evidence_query']}
```
</details>

Task CompozyOS: `{task_rel}`

<!-- factory-fingerprint: {fp} -->
{fe.block(fe.FINDING, env)}
{fe.block(fe.FACTS, facts_of(env))}
"""


def check(env):
    fp = env["fingerprint"]
    issue, task = open_issue(fp), open_task(fp)
    return {"duplicate": bool(issue or task), "fingerprint": fp,
            "issue_url": issue["url"] if issue else "", "task_dir": str(task.relative_to(ROOT)) if task else ""}


def work_key_format(conf):
    """BR-5: when the issue is the work item, the work adapter's key_format (installed, tree-checked)."""
    roles = conf.get("roles", {})
    provider = roles.get("work", {}).get("provider")
    if provider != roles.get("code", {}).get("provider"):
        return None
    try:
        _, installed = fg.resolve(ROOT, "work", provider, conf)
    except fg.Refused as exc:
        sys.exit(f"refused: {exc}")
    match = re.search(r"^key_format: (.*)$", (installed / "SKILL.md").read_text().split("\n---", 1)[0], re.M)
    if not match:
        sys.exit(f"refused: adapter work/{provider} has no key_format")
    return json.loads(match.group(1))


def create(conf, f, env):
    found = check(env)
    if found["duplicate"]:
        sys.exit(f"duplicate finding, refusing to create: {json.dumps(found)}")
    key_format = work_key_format(conf)
    fp = found["fingerprint"]
    slug = slug_for(f, fp)
    task_dir = TASKS / slug
    task_rel = f".compozy/tasks/{slug}/task_01.md"
    gh("label", "create", LABEL, "--repo", REPO, "--color", "5319E7", "--description", text("label"), "--force")
    # ponytail: issue first, then local files; if the local write fails the open issue still dedupes, fix by hand.
    issue = json.loads(gh("api", "-X", "POST", f"repos/{REPO}/issues", "--input", "-",
                          stdin=json.dumps({"title": f"[factory] {f['title']}", "body": render_issue(f, env, fp, task_rel),
                                            "labels": [LABEL, "bug"]})))
    work_key = ""
    if key_format:
        work_key = f"#{issue['number']}"
        values = {"key": work_key, "id": str(issue["number"])}
        gh("api", "-X", "PATCH", f"repos/{REPO}/issues/{issue['number']}", "-f",
           "title=" + key_format["title_prefix"].format(**values) + f["title"])
    task_dir.mkdir(parents=True)
    (task_dir / "task_01.md").write_text(render_task(f, env, fp, slug, issue["html_url"], work_key))
    (task_dir / "_tasks.md").write_text(
        f'---\nschema_version: "compozy.tasks/v2"\nworkflow: {slug}\ngraph:\n  nodes:\n'
        f"    - id: task_01\n      file: task_01.md\n  edges: []\n---\n\n# {slug}\n\n"
        f"| Task | Title | Status |\n|---|---|---|\n| task_01 | {f['title']} | pending |\n")
    gh("api", "-X", "POST", f"repos/{REPO}/issues/{issue['number']}/comments", "-f",
       "body=" + text("comment").format(task=task_rel))
    return {"task_slug": slug, "task_dir": str(task_dir.relative_to(ROOT)),
            "issue_number": issue["number"], "issue_url": issue["html_url"], "work_key": work_key}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "create"])
    a = p.parse_args()
    global REPO, LANG
    conf = load_conf()
    LANG = need(conf, "language", "posts")
    if LANG not in TEXT:
        sys.exit(f'refused: [language] posts "{LANG}" not supported by factory_intake.py ({", ".join(TEXT)})')
    provider = need(conf, "roles.code", "provider")
    if provider != "github":
        sys.exit(f'refused: factory_intake.py creates issues through code/github only ([roles.code] provider "{provider}")')
    REPO = need(conf, "roles.code.github", "repo")
    f = json.load(sys.stdin)
    missing = [k for k in ("title", "severity", "evidence_query", "envelope") if not f.get(k)]
    if missing:
        sys.exit(f"finding is missing required fields: {missing}")
    try:
        env = fe.envelope(f["envelope"])  # recomputes the fingerprint: never trusted from the input
    except (ValueError, KeyError, TypeError, AttributeError) as exc:
        sys.exit(f"refused: envelope: {exc}")
    print(json.dumps(check(env) if a.cmd == "check" else create(conf, f, env)))


if __name__ == "__main__":
    main()
