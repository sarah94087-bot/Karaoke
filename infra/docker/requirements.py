"""Print one dependency group from pyproject.toml, for `pip install -r -`.

The image installs from here rather than from a requirements file checked in
next to the Dockerfile, because a second copy of the dependency list is a second
thing to forget to update. Kept as a file rather than a `python -c` one-liner in
the Dockerfile: a multi-line one-liner joins with a leading space and Python
rejects it as an unexpected indent.

    python requirements.py api > requirements-api.txt
"""

import sys
import tomllib
from pathlib import Path


def main() -> int:
    group = sys.argv[1] if len(sys.argv) > 1 else "api"
    pyproject = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    groups = pyproject["project"]["optional-dependencies"]
    if group not in groups:
        print(f"no such dependency group: {group}", file=sys.stderr)
        return 1
    print("\n".join(groups[group]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
