"""bin/runbook-r0 rollback command (S5 prep P-11): a published install is pinned to its archive digest."""
import importlib.machinery
import importlib.util

from conftest import REPO

loader = importlib.machinery.SourceFileLoader("runbook_r0", str(REPO / "bin" / "runbook-r0"))
spec = importlib.util.spec_from_loader("runbook_r0", loader)
r0 = importlib.util.module_from_spec(spec)
loader.exec_module(r0)

ARCHIVE = "5440b940cb736e95c5fe667954f549b01c063468a0c8ffea5d3e5f872ba9b7db"  # github-app-review-v0.2.2.tar.gz
CHECKSUM = "18ee3bef70b5cc840b256321599cc2cfb4e288cdf3cce74e0973af11f9fed8c1"   # its extracted package
PUBLISHED = {"version": "0.2.2", "installed_from": "github", "slug": "acme/compozy-github-app",
             "source_url": "https://github.com/acme/compozy-github-app", "checksum_sha256": CHECKSUM,
             "archive_digest_sha256": ARCHIVE}


def test_published_install_reinstalls_the_tag_pinned_to_the_archive_digest():
    asked = []

    def releases(slug, tag):
        asked.append((slug, tag))
        return ARCHIVE

    command, problem = r0.rollback("github-app-review", PUBLISHED, digest_of=releases)
    assert problem is None
    assert command == (f"compozy extension install github:acme/compozy-github-app@v0.2.2 --expected-digest {ARCHIVE} "
                       "--allow-unverified")
    assert asked == [("acme/compozy-github-app", "v0.2.2")]  # the pin is checked against the published asset


def test_published_install_refused_when_the_release_asset_differs_or_the_slug_is_missing():
    _, problem = r0.rollback("github-app-review", PUBLISHED, digest_of=lambda slug, tag: "0" * 64)
    assert problem.startswith("extension github-app-review: release v0.2.2 of acme/compozy-github-app publishes ")
    assert "pass --source github-app-review=github:<owner>/<repo>@<tag>" in problem
    _, problem = r0.rollback("newrelic", {**PUBLISHED, "slug": None, "installed_from": "marketplace"},
                             digest_of=lambda slug, tag: ARCHIVE)
    assert problem.startswith("extension newrelic installed from marketplace without a GitHub slug: pass --source")
    command, problem = r0.rollback("newrelic", {**PUBLISHED, "slug": None},
                                   source="github:acme/compozy-github-app@v0.2.2", digest_of=lambda slug, tag: ARCHIVE)
    assert problem is None and command.startswith("compozy extension install github:acme/compozy-github-app@v0.2.2 ")


def test_local_path_install_reinstalls_with_replace_pinned_to_the_package_checksum():
    local = {"version": "0.2.2", "installed_from": "local_path", "source_url": "/opt/pkg", "checksum_sha256": CHECKSUM}
    command, problem = r0.rollback("github-app-review", local, digest_of=lambda *a: 1 / 0)
    assert problem is None
    assert command == f"compozy extension install /opt/pkg --replace --expected-digest {CHECKSUM} --allow-unverified"
