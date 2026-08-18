"""Every check the project runs, in one command.

    python scripts/check.py           run lint, format check, types, tests
    python scripts/check.py --fix     also apply lint fixes and reformat

Chosen over a Makefile because the project is developed on Windows, where make
is not present by default. CI (T-1.1, and the CI/CD section of the spec) runs
exactly this file, so a green local run means a green pipeline.
"""

import argparse
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(name: str, cmd: list[str]) -> tuple[str, bool, float]:
    print(f"\033[1m→ {name}\033[0m")
    t0 = time.time()
    result = subprocess.run([sys.executable, "-m", *cmd], cwd=ROOT)
    elapsed = time.time() - t0
    ok = result.returncode == 0
    print(
        "  {}  ({:.1f}s)\n".format("\033[32mok\033[0m" if ok else "\033[31mFAILED\033[0m", elapsed)
    )
    return name, ok, elapsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--fix", action="store_true", help="apply lint fixes and reformat instead of only reporting"
    )
    args = parser.parse_args()

    checks: list[tuple[str, list[str]]] = []
    if args.fix:
        checks.append(("lint (fixing)", ["ruff", "check", ".", "--fix"]))
        checks.append(("format", ["ruff", "format", "."]))
    else:
        checks.append(("lint", ["ruff", "check", "."]))
        checks.append(("format", ["ruff", "format", "--check", "."]))
    checks.append(("types", ["mypy"]))
    checks.append(("tests", ["pytest"]))

    results = [run(name, cmd) for name, cmd in checks]

    print("\033[1m" + "─" * 46 + "\033[0m")
    for name, ok, elapsed in results:
        status = "ok    " if ok else "FAILED"
        print(f"  {name:<16} {status}  {elapsed:5.1f}s")
    failed = [n for n, ok, _ in results if not ok]
    print("\033[1m" + "─" * 46 + "\033[0m")
    if failed:
        print(f"\033[31m{len(failed)} check(s) failed: {', '.join(failed)}\033[0m")
        return 1
    print("\033[32mall checks passed\033[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
