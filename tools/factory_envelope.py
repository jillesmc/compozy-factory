#!/usr/bin/env python3
"""Provider-neutral data in issue bodies: the finding envelope and the facts block (Python 3 stdlib only).

    factory_envelope.py wrap    < envelope fields JSON   -> envelope JSON with the factory fingerprint
    factory_envelope.py read    < issue body             -> envelope JSON (exit 1 when absent)
    factory_envelope.py facts   < facts JSON             -> the facts block to put in an issue body
    factory_envelope.py route   < issue body             -> the adapter skill that runs signals.verify_gone

Both are hidden blocks: `<!-- factory.finding/v1 {json} -->` and `<!-- factory.facts/v1 {json} -->`.
The factory owns the fingerprint: sha256(lower(service)|lower(error_class)|lower(location)), first 12
hex characters, so a New Relic finding keeps today's factory_intake.fingerprint(app, f).
"""
import hashlib
import json
import re
import sys

FINDING, FACTS = "factory.finding/v1", "factory.facts/v1"
KINDS = ("bugfix", "feature")


def fingerprint(inputs):
    key = "|".join(str(inputs[k]).strip().lower() for k in ("service", "error_class", "location"))
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def envelope(fields):
    """Complete the fields a signals adapter's `normalize` returns into a factory.finding/v1 envelope."""
    for key in ("provider", "provider_finding_id", "fingerprint_inputs", "evidence", "verifier"):
        if key not in fields:
            raise ValueError(f"envelope needs {key}")
    if not fields["verifier"].get("provider"):
        raise ValueError("envelope needs verifier.provider")
    return {"schema": FINDING, **{k: fields[k] for k in fields if k not in ("schema", "fingerprint")},
            "fingerprint": fingerprint(fields["fingerprint_inputs"])}


def block(schema, data):
    # `>` never appears outside JSON strings, so escaping it keeps `-->` out of the comment.
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True).replace(">", "\\u003e")
    return f"<!-- {schema}\n{payload}\n-->"


def read_block(schema, body):
    match = re.search(rf"<!-- {re.escape(schema)}\n(.*?)\n-->", body or "", re.S)
    return json.loads(match.group(1)) if match else None


def facts(data):
    """Validate a factory.facts/v1 block: kind, attached_doc, origin (signals provider or manual), touches_ci."""
    if data.get("kind") not in KINDS:
        raise ValueError("facts kind must be bugfix or feature")
    for key in ("attached_doc", "touches_ci"):
        if not isinstance(data.get(key), bool):
            raise ValueError(f"facts {key} must be true or false")
    if not isinstance(data.get("origin"), str) or not data["origin"]:
        raise ValueError("facts origin must be a signals provider or manual")
    return {k: data[k] for k in ("kind", "attached_doc", "origin", "touches_ci")}


def verifier_skill(env):
    """signals.verify_gone always goes to the envelope's verifier provider, not to today's configuration."""
    return f"factory-signals-{env['verifier']['provider']}"


def main(argv):
    if len(argv) != 1 or argv[0] not in ("wrap", "read", "facts", "route"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    try:
        if argv[0] == "wrap":
            env = envelope(json.loads(raw))
            print(json.dumps(env, ensure_ascii=False))
            print(block(FINDING, env))
        elif argv[0] == "facts":
            print(block(FACTS, facts(json.loads(raw))))
        else:
            env = read_block(FINDING, raw)
            if env is None:
                print(f"refused: no {FINDING} block in the issue body")
                return 1
            print(verifier_skill(env) if argv[0] == "route" else json.dumps(env, ensure_ascii=False))
    except (ValueError, KeyError, TypeError) as exc:
        print(f"refused: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
