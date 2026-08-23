"""Remove the audio of songs nobody has played for six months (T-3.9).

    .venv\\Scripts\\python.exe scripts\\reap.py             # say what would go
    .venv\\Scripts\\python.exe scripts\\reap.py --apply     # actually remove it

**A dry run is the default, and that is not politeness.** This deletes audio
that cannot be recovered without uploading it again, on a schedule, with nobody
watching. A command whose default is destructive is a command that will one day
be run with the wrong `--days` by somebody who meant to look first.

Chapter 9 keeps the metadata: the title, the lyrics somebody corrected by hand,
the measured key and tempo, and the settings they left the song in all survive.
What goes is the fifteen megabytes of stems.

Scheduled from the platform's cron once T-3.10 has a deployment - daily is
plenty for a six-month rule, and running it twice in a day is harmless because
a song with no audio left is not in the list any more.
"""

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from apps.api.config import settings  # noqa: E402
from apps.api.main import build_storage  # noqa: E402
from packages.core import retention  # noqa: E402
from packages.core.db import (  # noqa: E402
    create_engine,
    session_factory,
    use_a_loop_psycopg_can_run_on,
)


def megabytes(size: int) -> str:
    return f"{size / (1024 * 1024):.1f}MB"


async def run(days: int, apply: bool) -> int:
    if not settings.database_url:
        print("DATABASE_URL is not set")
        return 1

    engine = create_engine(settings.database_url)
    sessions = session_factory(engine)
    storage = build_storage()
    try:
        async with sessions() as session:
            candidates = await retention.reapable(session, days=days)
            if not candidates:
                print(f"nothing has been idle for {days} days")
                return 0

            total = sum(candidate.bytes for candidate in candidates)
            verb = "removing" if apply else "would remove"
            print(f"{verb} the audio of {len(candidates)} song(s), {megabytes(total)}:")
            for candidate in candidates:
                played = (
                    candidate.last_played_at.date().isoformat()
                    if candidate.last_played_at
                    else f"never (added {candidate.created_at.date().isoformat()})"
                )
                print(
                    f"  {candidate.title[:40]:<40} {megabytes(candidate.bytes):>8}"
                    f"  last played: {played}"
                )

            if not apply:
                print("\nnothing was changed. pass --apply to remove it.")
                return 0

            freed = 0
            for candidate in candidates:
                freed += await retention.archive(session, storage, candidate.song_id)
            await session.commit()
            print(f"\nfreed {megabytes(freed)}. the songs are still in the library, archived.")
            return 0
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        default=retention.UNPLAYED_DAYS,
        help=f"how long is too long (default {retention.UNPLAYED_DAYS}, chapter 9's six months)",
    )
    parser.add_argument(
        "--apply", action="store_true", help="remove the audio, rather than list it"
    )
    args = parser.parse_args()

    logging.basicConfig(level=os.getenv("KARUKI_LOG_LEVEL", "INFO"), format="%(message)s")
    use_a_loop_psycopg_can_run_on()
    return asyncio.run(run(args.days, args.apply))


if __name__ == "__main__":
    raise SystemExit(main())
