"""R2-105: the toolchain each language is graded with is pinned in the repo, served
to the platform, and diffed against the built image in CI (scripts/lang-smoke.sh).

The probe itself runs real binaries, so the drift check is exercised only under
ASSESS_REQUIRE_TOOLCHAINS=1 — inside the image, in tests/test_lang_smoke.py. Here:
the parsing, the normalisation and the route.
"""

from fastapi.testclient import TestClient

from assessment_agent import toolchains
from assessment_agent.api import app
from assessment_agent.languages import LANGUAGES


def test_the_pin_names_every_language_exactly_once():
    pinned = toolchains.pinned()
    assert set(pinned) == set(LANGUAGES)
    for language, entry in pinned.items():
        tool, _, version = entry.partition(" ")
        assert tool == LANGUAGES[language].version[0], language
        assert toolchains.version_tuple(entry), f"{language}: no version number in {entry!r}"


def test_version_strings_are_normalised_to_tool_and_number():
    # One probe per language; each toolchain prints its version differently.
    cases = {
        ("gcc", "14.2.0\n", ""): "gcc 14.2.0",
        ("rustc", "rustc 1.85.1 (4eb161250 2025-03-15) (built from a source tarball)\n", ""): (
            "rustc 1.85.1"
        ),
        ("go", "go version go1.24.4 linux/arm64\n", ""): "go 1.24.4",
        ("java", "", 'openjdk version "21.0.8" 2025-07-15\nOpenJDK Runtime ...\n'): "java 21.0.8",
        ("node", "v20.19.2\n", ""): "node 20.19.2",
        ("ruby", "ruby 3.3.8 (2025-04-09 revision b200bad6cd) +PRISM [aarch64-linux]\n", ""): (
            "ruby 3.3.8"
        ),
        ("python3", "Python 3.13.5\n", ""): "python3 3.13.5",
        ("java", "", "Unable to locate a Java Runtime.\n"): "java unknown",
    }
    for (tool, out, err), want in cases.items():
        assert toolchains.normalise(tool, out, err) == want


def test_version_tuple_reads_the_number_after_the_tool():
    assert toolchains.version_tuple("gcc 14.2.0") == (14, 2, 0)
    assert toolchains.version_tuple("go 1.24") == (1, 24)
    assert toolchains.version_tuple("java unknown") == ()
    assert toolchains.version_tuple("missing: java") == ()


def test_drift_lists_only_the_languages_that_differ():
    pinned = {"c": "gcc 14.2.0", "go": "go 1.24.4", "java": "java 21.0.8"}
    live = {"c": "gcc 14.2.0", "go": "go 1.24.5", "java": "missing: java"}
    lines = toolchains.drift(live, pinned)
    assert len(lines) == 2
    assert any("go" in line and "1.24.4" in line and "1.24.5" in line for line in lines)
    assert any("java" in line and "missing" in line for line in lines)
    assert toolchains.drift(pinned, pinned) == []


def test_toolchains_route_serves_the_pin_without_auth(monkeypatch):
    # The platform renders this on the candidate's start screen, before any
    # authenticated call — like /health, it must work with nothing configured.
    monkeypatch.delenv("ASSESS_API_TOKEN", raising=False)
    monkeypatch.delenv("ASSESS_AUTH_DISABLED", raising=False)
    resp = TestClient(app).get("/toolchains")
    assert resp.status_code == 200
    assert resp.json() == {"toolchains": toolchains.pinned()}
