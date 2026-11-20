#!/usr/bin/env python3
"""Does a promotion trigger the release pipeline? `[[roles.deploy.paths]]` trigger sets (Python 3 stdlib only).

    factory_deploy.py match <workspace-root> <changed_files> < files (one path per line)

prints exactly one line:
    absent                no [[roles.deploy.paths]] in factory.toml: every train waits for its deploy
    match: <set> <file>   trigger set <set> (1-based) has <file> in its includes and not in its excludes
    incomplete: <n>/<m>   no file listed matches, but only <n> of the <m> changed files were listed (counts as a match)
    no-match              every changed file is listed and none triggers a set
    refused: <reason>     factory.toml unreadable or the sets malformed (exit 1)

Each set is a CodePipeline V2 `file_paths` block (`includes`, optional `excludes`); a deploy is expected when any
set has one changed file matching one of its includes and none of its excludes. Globs follow CodePipeline
(https://docs.aws.amazon.com/codepipeline/latest/userguide/syntax-glob.html): `*` and `?` stay inside one path
segment, `**` crosses segments, `{a,b}` alternatives (not nested), `[abc]`, `[a-z]` and `[!abc]` one character
(not `/`; `*`, `?` and `\\` match themselves inside), `\\` escapes the next character. `**/` also matches no
directory, so `**/*.md` matches the root `README.md`. A malformed glob (unbalanced `{}` or `[]`, a trailing `\\`)
is refused, never read as a literal.
"""
import re
import sys
import tomllib
from pathlib import Path

TOML = ".compozy/factory/factory.toml"
SHAPE = ("[roles.deploy] paths must be one or more [[roles.deploy.paths]] sets: includes = a non-empty list of "
         "globs, excludes = an optional list of globs (non-empty strings)")


def _bracket(pattern, i):
    """(regex, next index) of the bracket expression opening at pattern[i] == '['."""
    j, negate = i + 1, False
    if pattern[j:j + 1] == "!":
        j, negate = j + 1, True
    end = pattern.find("]", j + 1 if pattern[j:j + 1] == "]" else j)
    if end < 0:
        raise ValueError(f"glob {pattern!r}: missing ]")
    body = pattern[j:end]
    if not body or "/" in body:
        raise ValueError(f"glob {pattern!r}: empty bracket or / inside brackets")
    chars = "".join("-" if c == "-" and 0 < k < len(body) - 1 else re.escape(c) for k, c in enumerate(body))
    return f"(?!/)[{'^' if negate else ''}{chars}]", end + 1


def glob_regex(pattern):
    """Compiled regex (use fullmatch) of one CodePipeline glob; ValueError when malformed."""
    out, i, group = [], 0, False
    while i < len(pattern):
        c = pattern[i]
        if c == "\\":
            if i + 1 == len(pattern):
                raise ValueError(f"glob {pattern!r}: trailing backslash")
            out.append(re.escape(pattern[i + 1]))
            i += 2
            continue
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
            continue
        if pattern.startswith("**", i):
            out.append(".*")
            i += 2
            continue
        if c == "[":
            text, i = _bracket(pattern, i)
            out.append(text)
            continue
        if c == "{":
            if group:
                raise ValueError(f"glob {pattern!r}: nested {{ (groups cannot be nested)")
            group = True
            out.append("(?:")
        elif c == "}":
            if not group:
                raise ValueError(f"glob {pattern!r}: }} without {{")
            group = False
            out.append(")")
        elif c == "]":
            raise ValueError(f"glob {pattern!r}: ] without [")
        else:
            out.append({"*": "[^/]*", "?": "[^/]", ",": "|" if group else ","}.get(c, re.escape(c)))
        i += 1
    if group:
        raise ValueError(f"glob {pattern!r}: missing }}")
    try:
        return re.compile("".join(out))
    except re.error as exc:  # e.g. a reversed range [z-a]
        raise ValueError(f"glob {pattern!r}: {exc}") from None


def check_paths(paths):
    """None when `paths` (the [roles.deploy] value) is absent or well formed; else the refusal reason."""
    if paths is None:
        return None

    def globs(value, empty):
        return isinstance(value, list) and (empty or value) and all(isinstance(g, str) and g.strip() for g in value)
    if not (isinstance(paths, list) and paths and all(
            isinstance(s, dict) and set(s) <= {"includes", "excludes"} and globs(s.get("includes"), False)
            and globs(s.get("excludes", []), True) for s in paths)):
        return SHAPE
    for s in paths:
        for g in s["includes"] + s.get("excludes", []):
            try:
                glob_regex(g)
            except ValueError as exc:
                return f"[roles.deploy] paths: {exc}"
    return None


def split_globs(text):
    """Split an operator's comma-separated glob list on the commas outside `{}`, `[]` and escapes."""
    parts, cur, depth, i = [], "", 0, 0
    while i < len(text):
        c = text[i]
        if c == "\\" and i + 1 < len(text):
            cur, i = cur + text[i:i + 2], i + 2
            continue
        depth += {"{": 1, "[": 1, "}": -1, "]": -1}.get(c, 0)
        if c == "," and depth <= 0:
            parts.append(cur)
            cur = ""
        else:
            cur += c
        i += 1
    return [p.strip() for p in parts + [cur] if p.strip()]


def decide(files, changed, sets):
    """The one output line for the promotion's listed `files` out of `changed` changed files."""
    if not sets:
        return "absent"
    files = [f.strip().removeprefix("./") for f in files if f.strip()]
    for n, s in enumerate(sets, 1):
        includes = [glob_regex(g) for g in s["includes"]]
        excludes = [glob_regex(g) for g in s.get("excludes", [])]
        for f in files:
            if any(r.fullmatch(f) for r in includes) and not any(r.fullmatch(f) for r in excludes):
                return f"match: {n} {f}"
    if len(files) < changed:
        return f"incomplete: {len(files)}/{changed}"
    return "no-match"


def main(argv):
    if len(argv) != 4 or argv[1] != "match" or not argv[3].isdigit():
        print("refused: usage: factory_deploy.py match <workspace-root> <changed_files> < files")
        return 1
    try:
        conf = tomllib.loads((Path(argv[2]) / TOML).read_text())
    except (OSError, tomllib.TOMLDecodeError) as exc:
        print(f"refused: {TOML} unreadable ({exc})")
        return 1
    deploy = conf.get("roles", {}).get("deploy", {})
    paths = deploy.get("paths") if isinstance(deploy, dict) else None
    problem = check_paths(paths)
    if problem:
        print(f"refused: {problem}")
        return 1
    print(decide(sys.stdin.read().splitlines(), int(argv[3]), paths))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
