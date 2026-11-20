"""tools/factory_deploy.py: the one trigger-set matcher the gatekeeper runs at Deploy step 3 (fix6, E2E-003 #241)."""
import subprocess
import sys

import pytest

import factorylib as fl
from conftest import REPO

sys.path.insert(0, str(REPO / "tools"))
import factory_deploy as fd  # noqa: E402

TOOL = REPO / "tools" / "factory_deploy.py"
# The release pipelines' Terraform `file_paths` blocks, copied verbatim (sampled 2026-11-20).
MONOREPO = [{"includes": ["acme-portal/**", "packages/metrics-store/migrations/**"],
             "excludes": ["acme-portal/infrastructure/**", "acme-portal/web/**", "**/*.md", "**/*.rst"]},
            {"includes": ["acme-portal/web/**"]}]
WORKER = [{"includes": ["**"],
           "excludes": [".github/**", "*.md", "docs/**", "CODEOWNERS", "sonar-project.properties"]}]
PROMOTION_241 = [".compozy/factory/factory.toml", ".compozy/profiles/default/agents/README.md"]


@pytest.mark.parametrize("glob, path, want", [
    ("*.md", "README.md", True), ("*.md", "docs/a.md", False),
    ("**/*.md", "docs/a.md", True), ("**/*.md", "README.md", True), ("**/*.md", "a/b/c.md", True),
    ("infrastructure/**", "infrastructure/x/y.tf", True), ("infrastructure/**", "app/infrastructure/x", False),
    (".github/**", ".github/workflows/ci.yml", True), (".github/**", "github/x", False),
    ("**", "anything/at/all.txt", True), ("**", ".env", True),
    ("src/*/x", "src/a/x", True), ("src/*/x", "src/a/b/x", False), ("src/**/x.py", "src/x.py", True),
    ("foo.?", "foo.c", True), ("foo.?", "foo.cc", False), ("a?b", "a/b", False),
    ("*.{java,class}", "Main.class", True), ("*.{java,class}", "Main.kt", False),
    ("src/**/*.{py,js}", "src/a/b.js", True), ("src/**/*.{py,js}", "src/a/b.ts", False),
    ("{docs/**,*.md}", "docs/x/y", True), ("a,b", "a,b", True),
    ("src/[ab].py", "src/a.py", True), ("src/[ab].py", "src/c.py", False),
    ("src/[!ab].py", "src/c.py", True), ("src/[!ab].py", "src/a.py", False),
    ("v[0-9].txt", "v7.txt", True), ("v[0-9].txt", "vx.txt", False), ("[-a]", "-", True),
    ("[*?\\]", "?", True), ("[*?\\]", "\\", True), ("[*?\\]", "x", False),
    (r"src/\*.py", "src/*.py", True), (r"src/\*.py", "src/a.py", False), (r"\{a\}", "{a}", True),
])
def test_codepipeline_glob_syntax(glob, path, want):
    assert bool(fd.glob_regex(glob).fullmatch(path)) is want, (glob, path)


@pytest.mark.parametrize("glob, why", [
    ("*.{py,js", "missing }"), ("*.py}", "} without {"), ("{a,{b,c}}", "nested {"), ("src/[ab.py", "missing ]"),
    ("src/ab].py", "] without ["), ("src/\\", "trailing backslash"), ("[a/b]", "/ inside brackets"),
    ("[z-a]", "bad character range"),
])
def test_malformed_globs_are_refused_never_literal(glob, why):
    with pytest.raises(ValueError, match=why.replace("{", r"\{").replace("[", r"\[")):
        fd.glob_regex(glob)
    assert fd.check_paths([{"includes": [glob]}]).startswith("[roles.deploy] paths: glob ")


def test_the_real_pipelines():
    assert fd.decide(PROMOTION_241, 2, MONOREPO) == "no-match", "#241 changed only factory config: no deploy"
    assert fd.decide(PROMOTION_241, 2, WORKER) == "match: 1 .compozy/factory/factory.toml", "the worker deploys on **"
    assert fd.decide(["acme-portal/README.md", "acme-portal/infrastructure/main.tf", "docs/x.rst"], 3,
                     MONOREPO) == "no-match"
    assert fd.decide(["acme-portal/README.md", "acme-portal/src/app.ts"], 2, MONOREPO) == \
        "match: 1 acme-portal/src/app.ts"
    assert fd.decide(["acme-portal/web/index.ts"], 1, MONOREPO) == "match: 2 acme-portal/web/index.ts", \
        "excluded by set 1, included by set 2"
    assert fd.decide(["./packages/metrics-store/migrations/001.sql"], 1, MONOREPO) == \
        "match: 1 packages/metrics-store/migrations/001.sql"
    assert fd.decide(["README.md", ".github/workflows/ci.yml", "docs/a/b.md", "CODEOWNERS",
                      "sonar-project.properties"], 5, WORKER) == "no-match"
    assert fd.decide(["src/README.md"], 1, WORKER) == "match: 1 src/README.md", "`*.md` excludes root files only"


def test_incomplete_list_counts_as_a_match_and_absent_sets_always_wait():
    assert fd.decide(PROMOTION_241, 3001, MONOREPO) == "incomplete: 2/3001"
    assert fd.decide(PROMOTION_241, 2, None) == "absent" and fd.decide([], 0, []) == "absent"
    assert fd.decide([], 0, MONOREPO) == "no-match"


def test_split_globs_keeps_commas_inside_groups_and_brackets():
    assert fd.split_globs("src/**/*.{py,js}, Dockerfile") == ["src/**/*.{py,js}", "Dockerfile"]
    assert fd.split_globs(r"a\,b, [,x], c") == [r"a\,b", "[,x]", "c"]
    assert fd.split_globs(" , ") == []


def match(tmp_path, toml, files, changed):
    (tmp_path / ".compozy" / "factory").mkdir(parents=True, exist_ok=True)
    (tmp_path / ".compozy" / "factory" / "factory.toml").write_text(toml)
    return subprocess.run([sys.executable, str(TOOL), "match", str(tmp_path), str(changed)],
                          input="\n".join(files) + "\n", capture_output=True, text=True)


def test_cli_prints_one_line(tmp_path):
    sets = ('[roles.deploy]\nprovider = "github-deployments"\n\n[[roles.deploy.paths]]\n'
            'includes = ["acme-portal/**"]\nexcludes = ["**/*.md"]\n')
    for files, changed, line in ((PROMOTION_241, 2, "no-match"), (PROMOTION_241, 3, "incomplete: 2/3"),
                                 (["acme-portal/a.ts"], 1, "match: 1 acme-portal/a.ts")):
        proc = match(tmp_path, sets, files, changed)
        assert (proc.returncode, proc.stdout) == (0, line + "\n")
    proc = match(tmp_path, '[roles.deploy]\nprovider = "github-deployments"\n', PROMOTION_241, 2)
    assert (proc.returncode, proc.stdout) == (0, "absent\n")
    proc = match(tmp_path, '[[roles.deploy.paths]]\nincludes = ["src/{a,b"]\n', ["src/a"], 1)
    assert proc.returncode == 1 and proc.stdout == "refused: [roles.deploy] paths: glob 'src/{a,b': missing }\n"
    proc = subprocess.run([sys.executable, str(TOOL), "match", str(tmp_path)], capture_output=True, text=True)
    assert proc.returncode == 1 and proc.stdout.startswith("refused: usage:")


def test_factorylib_reuses_the_tool():
    assert fl.check_paths is fd.check_paths and fl.split_globs is fd.split_globs
    assert "def glob_regex" not in (REPO / "bin" / "factorylib.py").read_text()
