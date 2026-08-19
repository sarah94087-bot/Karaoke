"""Guards the layout that chapter 10 of the spec commits to.

This is deliberately a structural test rather than a placeholder. The directory
split is the thing T-1.1 delivers, and it is also the thing that quietly erodes
first: a module lands in the wrong package, packages/providers stops being the
single seam for external services, and by phase 3 swapping a provider is a
rewrite again.
"""

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_apps_exist():
    for app in ("api", "web", "gpu"):
        assert (ROOT / "apps" / app).is_dir(), f"missing apps/{app}"


def test_packages_are_importable():
    from packages import audio, core, lyrics, providers

    for mod in (core, audio, lyrics, providers):
        assert mod.__doc__, f"{mod.__name__} should document what belongs in it"


def test_infra_and_docs_exist():
    assert (ROOT / "infra" / "docker").is_dir()
    assert (ROOT / "infra" / "github").is_dir()
    assert (ROOT / "docs").is_dir()


def test_phase0_findings_are_kept():
    """Phase 0's measurements are referenced by later decisions, so they ship."""
    findings = ROOT / "docs" / "phase0"
    assert (findings / "phase0-findings.md").is_file()
    assert (findings / "quotas.md").is_file()


def test_secrets_are_not_tracked():
    """.env and TLS keys must never be committed."""
    gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    for pattern in (".env", "certs/"):
        assert pattern in gitignore, f"{pattern} must stay gitignored"
    assert not (ROOT / ".env").exists() or ".env" in gitignore
