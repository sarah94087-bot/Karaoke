"""Settings, read once from the environment at import time.

Deliberately hand-rolled rather than pydantic-settings: the API has a handful of
knobs, and every dependency added here is one more thing to keep working on a
free PaaS tier.
"""

import os
from dataclasses import dataclass, field

API_PREFIX = "/api/v1"


def _csv(name: str, default: str) -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    """Everything the service reads from the environment."""

    # Shown in /system/health and in the OpenAPI document. Bumped per release so
    # that a keep-alive ping is also a cheap "which build is live?" check.
    version: str = field(default_factory=lambda: os.getenv("KARUKI_VERSION", "0.1.0"))
    environment: str = field(default_factory=lambda: os.getenv("KARUKI_ENV", "local"))
    # Chapter 10: production is a single environment, local is Docker Compose.
    # The web app is the only browser client, so the list stays short.
    cors_origins: list[str] = field(
        default_factory=lambda: _csv(
            "KARUKI_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
        )
    )

    # Set by docker compose; unset when running uvicorn straight off the venv.
    # Nothing reads it yet - the models and migrations arrive in T-1.4 - but the
    # compose file wires it now so "one command brings up API + DB" means the
    # API can actually see the DB.
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))

    # D-12 is deferred to phase 3, so phase 1 stores files on disk behind
    # packages/providers/storage.py. In the container this is a volume; in
    # production it becomes an object store and this setting goes away.
    storage_root: str = field(
        default_factory=lambda: os.getenv("KARUKI_STORAGE_ROOT", "var/storage")
    )
    # A rejection the user can act on, rather than a request that dies halfway
    # through. Eight minutes of 320kbps mp3 is about 19MB, so this is generous
    # while still refusing an upload that was never going to be a song.
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.getenv("KARUKI_MAX_UPLOAD_BYTES", str(80 * 1024 * 1024)))
    )

    # "local" runs Demucs on this machine's CPU: slow, free, always available,
    # and what chapter 10 specifies for the local environment. "modal" is the
    # serverless GPU from T-0.3 and spends real money out of a $1/month credit,
    # so it is never the default - choosing it has to be a decision.
    separation_backend: str = field(
        default_factory=lambda: os.getenv("KARUKI_SEPARATION_BACKEND", "local")
    )

    # The open synchronised-lyrics database (D-08, T-2.2). On by default,
    # unlike the separation backend: this is a free read of a public database
    # with no account behind it, and skipping it means paying a transcription
    # for a song somebody already timed by hand. "none" turns it off.
    lyrics_catalogue: str = field(
        default_factory=lambda: os.getenv("KARUKI_LYRICS_CATALOGUE", "lrclib")
    )

    # D-16 (which managed auth provider) is undecided, so phase 1 attributes
    # every job to one local user. Chapter 6's Bearer token replaces this; the
    # column it fills is already there and already has no foreign key.
    dev_user_id: str = field(
        default_factory=lambda: os.getenv(
            "KARUKI_DEV_USER_ID", "00000000-0000-0000-0000-000000000001"
        )
    )

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


settings = Settings()
