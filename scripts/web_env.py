"""Write `apps/web/.env.local` from the repository's `.env` (T-3.6).

    .venv\\Scripts\\python.exe scripts\\web_env.py

Next reads env files from its own directory, so the web app cannot see the
`.env` at the repository root. The alternative to this script is asking somebody
to paste the same two values into two files and keep them in step, which is the
kind of duplication that is only ever discovered by a deployment failing.

Only the values the *browser* is allowed to have are copied. The Supabase secret
key, the B2 credentials and the database password stay where they are: anything
named `NEXT_PUBLIC_` is compiled into the bundle and served to everyone.
"""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# source name in .env -> name the browser bundle sees
PUBLIC = {
    "SUPABASE_URL": "NEXT_PUBLIC_SUPABASE_URL",
    "SUPABASE_ANON_KEY": "NEXT_PUBLIC_SUPABASE_ANON_KEY",
    "KARUKI_API_BASE": "NEXT_PUBLIC_API_BASE",
}

HEADER = """# Written by scripts/web_env.py from the repository's .env - do not edit by hand.
# Only browser-safe values are here; see that script for why.
"""


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            name, _, value = line.partition("=")
            values[name.strip()] = value.strip()
    return values


def main() -> int:
    source = read_env(ROOT / ".env")
    lines = [HEADER]
    missing = []
    for name, public in PUBLIC.items():
        value = os.getenv(name) or source.get(name, "")
        if not value:
            missing.append(name)
            continue
        lines.append(f"{public}={value}\n")

    target = ROOT / "apps" / "web" / ".env.local"
    with open(target, "w", encoding="utf-8", newline="\n") as fh:
        fh.writelines(lines)

    print(f"wrote {target.relative_to(ROOT)}")
    for name, public in PUBLIC.items():
        if name not in missing:
            print(f"  {public} <- {name}")
    if missing:
        print(f"  not set in .env, so not copied: {', '.join(missing)}")
    # KARUKI_API_BASE is genuinely optional - api.ts has a working default for
    # local development - so a missing one is not a failure.
    return 1 if "SUPABASE_URL" in missing or "SUPABASE_ANON_KEY" in missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
