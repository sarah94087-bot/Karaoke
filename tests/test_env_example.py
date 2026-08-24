"""T-3.13, chapter 14's checklist item: `.env.example` carries every name.

The file is the only inventory of what this system needs to run, because the
values live in the platform and nowhere else (chapter 14). An inventory that
drifts is worse than none: it is read once, at the moment somebody is setting
up a deployment, and what is missing from it is missing from the deployment -
which is how `KARUKI_CORS_ORIGINS`, `MODAL_TOKEN_ID` and `GROQ_API_KEY` each
cost this project a broken deploy in T-3.10.

So the checklist item is a test rather than a habit. It reads the source for
every environment variable the code actually looks at, and fails on any that
`.env.example` does not mention.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
SOURCES = ("apps", "packages", "scripts", "migrations", "infra")
SKIP_DIRS = {"node_modules", ".next", "__pycache__", ".venv"}

READS = re.compile(
    r"""os\.getenv\(\s*["']([A-Z][A-Z_0-9]+)["']"""
    r"""|os\.environ\[\s*["']([A-Z][A-Z_0-9]+)["']"""
    r"""|os\.environ\.get\(\s*["']([A-Z][A-Z_0-9]+)["']"""
    r"""|process\.env\.([A-Z][A-Z_0-9]+)"""
)

# Names the *platform* provides and nobody configures for this project. Each
# one is here for a reason, not to make the test pass.
NOT_OURS = {
    "PATH",
    "HOST",  # Modal's container, in apps/gpu
    "PORT",  # Render provides it; chapter 14's first named mistake
    "NODE_ENV",  # Next sets it from the build
    "HF_HUB_OFFLINE",  # set *by* apps/gpu on the GPU image, not read from a deploy
    "TRANSFORMERS_OFFLINE",
    "TORCH_HOME",  # same: the GPU image sets it, and reads it back to report it
}


def env_names_in_source() -> set[str]:
    found: set[str] = set()
    for top in SOURCES:
        for path in (ROOT / top).rglob("*"):
            if path.is_dir() or path.suffix not in {".py", ".ts", ".tsx", ".mts", ".js"}:
                continue
            if SKIP_DIRS & set(path.parts):
                continue
            for match in READS.finditer(path.read_text(encoding="utf-8", errors="ignore")):
                found.add(next(group for group in match.groups() if group))
    return found - NOT_OURS


def test_every_variable_the_code_reads_is_in_env_example():
    documented = EXAMPLE.read_text(encoding="utf-8")
    missing = sorted(name for name in env_names_in_source() if name not in documented)

    assert not missing, f".env.example does not mention: {', '.join(missing)}"


def test_env_example_carries_names_and_never_values():
    """Chapter 14: the repository holds the names, the platform holds the
    values. A single pasted secret here is one in the git history for ever."""
    suspicious = []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        name, _, value = line.partition("=")
        # Local defaults are allowed and useful - a URL to a container on this
        # machine is not a secret. Anything that looks like a key is not.
        if re.search(r"(KEY|SECRET|TOKEN|DSN|PASSWORD)$", name.strip()) and value.strip():
            suspicious.append(name.strip())

    assert not suspicious, f"values in .env.example: {', '.join(suspicious)}"
