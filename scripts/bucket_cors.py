"""Apply the bucket's CORS rule from the environment (T-3.2).

    .venv\\Scripts\\python.exe scripts\\bucket_cors.py          # show
    .venv\\Scripts\\python.exe scripts\\bucket_cors.py --apply  # set

The browser talks to the object store directly - it PUTs the upload there and
fetches the stems from there - so the bucket has to say which origins may do
that. A presigned URL is not enough on its own: it is perfectly valid and the
request is still cross-origin.

The origins are `KARUKI_CORS_ORIGINS`, the same list the API allows for itself,
because two lists would drift and the failure looks identical either way. Run
this again after a deployment adds its own origin.
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from packages.providers.storage_s3 import S3Config, S3Storage  # noqa: E402


def load_env() -> None:
    """Read `.env` the way the compose file does, without adding a dependency."""
    path = ROOT / ".env"
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            name, _, value = line.partition("=")
            os.environ.setdefault(name.strip(), value.strip())


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="write the rule, not just show it")
    args = parser.parse_args()

    load_env()
    origins = [
        origin.strip()
        for origin in os.getenv(
            "KARUKI_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        ).split(",")
        if origin.strip()
    ]
    storage = S3Storage(
        S3Config(
            endpoint=os.environ["KARUKI_S3_ENDPOINT"],
            bucket=os.environ["KARUKI_S3_BUCKET"],
            region=os.environ["KARUKI_S3_REGION"],
            access_key_id=os.environ["KARUKI_S3_KEY_ID"],
            secret_access_key=os.environ["KARUKI_S3_SECRET"],
        )
    )

    print(f"bucket  {storage.config.bucket} at {storage.config.endpoint}")
    print(f"now     {storage.get_cors() or 'no CORS rule'}")
    if not args.apply:
        print("(nothing changed; pass --apply to write the rule above)")
        return 0

    storage.set_cors(origins)
    print(f"applied {origins}")
    print(f"reads back as {storage.get_cors()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
