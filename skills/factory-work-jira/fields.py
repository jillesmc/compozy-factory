#!/usr/bin/env python3
"""Jira field payload for a work item from its facts (factory.facts/v1) and [roles.work.jira].

Usage (from the workspace root):
    fields.py --facts '{"kind": "bugfix", "attached_doc": false, "origin": "newrelic", "touches_ci": false}'
              [--doc-yes <option id>] [--value-type <[roles.work.jira.tipo_de_valor] key>]
              [--current '<the item's current fields JSON>']
Prints the `fields` JSON for `twg jira workitem update --fields-json`. Nothing is derived from the key.
With --current (work.fields_repair) only the required fields that are empty now are in the payload:
a populated field is never overwritten.
"""
import argparse
import json
import sys
import tomllib
from pathlib import Path

class WorkUnavailable(Exception):
    """The current item could not be read reliably; repair nothing (fail closed, F-7)."""


KEYS = ("documentacao_anexa", "utilizacao_ia", "tipo_atividade", "qual_tipo_de_valor", "plataformas_afetadas")


def assets(workspace, keys):
    """Assets objects in the only form Jira keeps (objectId = the number after the `-` of the key)."""
    keys = keys if isinstance(keys, list) else [keys]
    return [{"workspaceId": workspace, "id": f"{workspace}:{k.rsplit('-', 1)[-1]}", "objectId": k.rsplit("-", 1)[-1]}
            for k in keys]


def empty(value):
    return value in (None, "", [], {})


def fields(jira, facts, doc_yes=None, value_type="padrao", current=None):
    """The payload for every required field, or with `current` only for those empty in it (F-1)."""
    if facts.get("kind") not in ("bugfix", "feature"):
        raise ValueError("facts kind must be bugfix or feature")
    ids, ws, plataformas = jira["fields"], jira["assets_workspace"], jira["plataformas"]
    if current is not None:  # a read with --fields <ids> returns every requested id, null when empty
        missing = [ids[k] for k in KEYS if not isinstance(current, dict) or ids[k] not in current]
        if missing:
            raise WorkUnavailable(f"current item fields lack {', '.join(missing)}; not repairing")

    def documentacao():
        if facts.get("attached_doc") and not doc_yes:
            raise ValueError("attached_doc is true: pass --doc-yes <option id>")
        return {"id": doc_yes if facts.get("attached_doc") else jira["defaults"]["documentacao_anexa"]}

    def platforms():
        keys = list(plataformas["padrao"] if isinstance(plataformas["padrao"], list) else [plataformas["padrao"]])
        if facts.get("origin") == "newrelic" and plataformas.get("achado_newrelic"):
            keys.append(plataformas["achado_newrelic"])
        if facts.get("touches_ci") and plataformas.get("ci"):
            keys.append(plataformas["ci"])
        return assets(ws, keys)

    build = {"documentacao_anexa": documentacao,
             "utilizacao_ia": lambda: {"id": jira["defaults"]["utilizacao_ia"]},
             "tipo_atividade": lambda: {"id": jira["tipo_atividade"][facts["kind"]]},
             "qual_tipo_de_valor": lambda: assets(ws, jira["tipo_de_valor"][value_type]),
             "plataformas_afetadas": platforms}
    return {ids[key]: build[key]() for key in KEYS if current is None or empty(current.get(ids[key]))}


def main():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--facts", required=True)
    p.add_argument("--doc-yes")
    p.add_argument("--value-type", default="padrao")
    p.add_argument("--current", help="the item's current fields (work.fields_repair): only empty ones are sent")
    a = p.parse_args()
    conf = tomllib.loads(Path(".compozy/factory/factory.toml").read_text())
    try:
        current = json.loads(a.current) if a.current is not None else None
    except json.JSONDecodeError as exc:
        raise WorkUnavailable(f"current item fields are not JSON ({exc})") from exc
    if isinstance(current, dict) and isinstance(current.get("fields"), dict):
        current = current["fields"]
    print(json.dumps(fields(conf["roles"]["work"]["jira"], json.loads(a.facts), a.doc_yes, a.value_type, current)))


if __name__ == "__main__":
    try:
        main()
    except WorkUnavailable as exc:
        sys.exit(f"work_unavailable: {exc}")
    except (ValueError, KeyError, OSError) as exc:
        sys.exit(f"refused: {exc}")
