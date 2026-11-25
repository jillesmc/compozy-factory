#!/usr/bin/env python3
"""Provider-neutral data in issue bodies: the finding envelope and the facts block (Python 3 stdlib only).

    factory_envelope.py wrap    < envelope fields JSON   -> envelope JSON with the factory fingerprint
    factory_envelope.py read    < issue body             -> envelope JSON (exit 1 when absent)
    factory_envelope.py facts   < facts JSON             -> the facts block to put in an issue body
    factory_envelope.py route   < issue body             -> the adapter skill that runs signals.verify_gone
    factory_envelope.py signature < {"error", "step"?}   -> {error_class, location, like} of a scope item

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


# Volatile parts of an error text, replaced in this order (see signature()).
VOLATILE = [
    (re.compile(r""""[^"]*(?:"|$)|`[^`]*(?:`|$)|(?<!\w)'[^']*(?:'|$)"""), "<q>"),  # quoted SQL, names, paths
    (re.compile(r"\b\w+://\S+|\S+@\S+"), "<url>"),  # URLs, DSNs with a user and password, e-mails
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?\b|\b[\w-]+(?:\.[\w-]+)+:\d+\b|\b[\w-]+(?:\.[\w-]+){2,}\b"),
     "<host>"),  # IPv4[:port], name:port, names with 2+ dots
    (re.compile(r"(?<=SQLSTATE )[0-9A-Z]{5}\b|(?<=\()(?=[0-9A-Z]{5}\))[0-9A-Z]*\d[0-9A-Z]*(?=\))"), "<sqlstate>"),
    (re.compile(r"\b0x[0-9a-fA-F]+\b|\b\d+\b"), "<n>"),  # hex and decimal numbers
]


def signature(error, step=None):
    """A cross-consumer error signature: error_class (the first line with volatile parts replaced, whitespace
    collapsed, trailing `:`/space dropped, at most 200 characters), location (step, else the `<word> failed:`
    prefix, else `unknown`) and like (error_class with each placeholder as `%`, ending in `%`, quoted for an
    NRQL string: `<attr> LIKE '<like>'`)."""
    text = str(error or "").split("\n", 1)[0]
    for pattern, repl in VOLATILE:
        text = pattern.sub(repl, text)
    text = re.sub(r"\s+", " ", text).strip().rstrip(": ")[:200]
    prefix = re.match(r"([\w-]+) failed:", text)
    location = step if isinstance(step, str) and step.strip() else prefix.group(1) if prefix else "unknown"
    like = re.sub(r"%+", "%", re.sub(r"<(?:q|url|host|sqlstate|n)>", "%", text) + "%")
    return {"error_class": text, "location": location,
            "like": like.replace("\\", "\\\\").replace("'", "\\'")}


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
    if len(argv) != 1 or argv[0] not in ("wrap", "read", "facts", "route", "signature"):
        print(__doc__.split("\n\n")[1], file=sys.stderr)
        return 2
    raw = sys.stdin.read()
    try:
        if argv[0] == "wrap":
            env = envelope(json.loads(raw))
            print(json.dumps(env, ensure_ascii=False))
            print(block(FINDING, env))
        elif argv[0] == "signature":
            item = json.loads(raw)
            print(json.dumps(signature(item.get("error"), item.get("step")), ensure_ascii=False))
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
