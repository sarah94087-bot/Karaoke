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

    # D-12 (T-3.1): "local" writes to a directory, "s3" writes to the object
    # store. Local stays the default because chapter 11 requires the whole
    # product to run on a machine with no accounts on it - not, as with the
    # separation backend, because the other one costs money.
    storage_backend: str = field(
        default_factory=lambda: os.getenv("KARUKI_STORAGE_BACKEND", "local")
    )
    # Where the local backend keeps its files. In the container this is a
    # volume; with the s3 backend it is unused.
    storage_root: str = field(
        default_factory=lambda: os.getenv("KARUKI_STORAGE_ROOT", "var/storage")
    )
    # Backblaze B2 speaks S3, so these are the ordinary four. The endpoint and
    # region come from the bucket's page in the B2 console; the key pair is an
    # application key, which is why nothing here mentions B2 by name - Storj or
    # anything else S3-compatible is the same five values.
    s3_endpoint: str = field(default_factory=lambda: os.getenv("KARUKI_S3_ENDPOINT", ""))
    s3_bucket: str = field(default_factory=lambda: os.getenv("KARUKI_S3_BUCKET", ""))
    s3_region: str = field(default_factory=lambda: os.getenv("KARUKI_S3_REGION", ""))
    s3_key_id: str = field(default_factory=lambda: os.getenv("KARUKI_S3_KEY_ID", ""))
    s3_secret: str = field(default_factory=lambda: os.getenv("KARUKI_S3_SECRET", ""))

    # How long a stem link lives. A song is fetched whole (D-14) the moment the
    # player opens, so this only has to outlast a download on a slow connection;
    # an hour is generous for that and short enough that a link copied out of
    # the network tab is not a permanent one.
    signed_url_ttl: int = field(
        default_factory=lambda: int(os.getenv("KARUKI_SIGNED_URL_TTL", "3600"))
    )
    # What the local backend signs with. Unset means a fresh random secret per
    # process, which is safe (links stop working on restart) rather than
    # convenient; a deployment sets it.
    signing_secret: str = field(default_factory=lambda: os.getenv("KARUKI_SIGNING_SECRET", ""))
    # Prepended to local signed URLs. Empty gives a root-relative URL, which is
    # what the web app already resolves and what keeps the address right whether
    # the API was reached on localhost or on 127.0.0.1.
    public_base_url: str = field(default_factory=lambda: os.getenv("KARUKI_PUBLIC_BASE_URL", ""))
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

    # The transcription service (D-27, T-2.3), for the songs the open lyrics
    # database does not have - which phase 0's Hebrew sample suggests is about
    # half of them. Free tier, no card, 2,000 requests a day; a deployment with
    # no GROQ_API_KEY reports transcription as unavailable rather than as failed.
    transcription_backend: str = field(
        default_factory=lambda: os.getenv("KARUKI_TRANSCRIPTION_BACKEND", "groq")
    )

    # D-16 is Supabase (T-3.6). The API verifies its tokens against the
    # project's published keys; the URL is all it needs, and it is the same
    # value the web app has. Empty means no auth at all, which chapter 11
    # allows locally and `create_app` refuses in production.
    supabase_url: str = field(default_factory=lambda: os.getenv("SUPABASE_URL", ""))

    # D-24 (T-3.12). Empty means errors are not reported anywhere, which is the
    # normal state locally and chapter 11's promise that the product runs on a
    # machine with no accounts on it.
    sentry_dsn: str = field(default_factory=lambda: os.getenv("SENTRY_DSN", ""))

    # An error probe for chapter 14's checklist item "a deliberate error appears
    # in the monitoring tool". Unset - which is also what Render does to a
    # variable left blank - means the route answers 404, so the safe state is
    # the default rather than the thing somebody has to remember to turn off.
    error_probe_token: str = field(
        default_factory=lambda: os.getenv("KARUKI_ERROR_PROBE_TOKEN", "")
    )

    # The key to `/system/reap`, which is chapter 9's retention pass run from
    # outside on a schedule (T-3.13). Its own token and not the probe's: one of
    # those routes raises an exception and the other deletes audio.
    maintenance_token: str = field(
        default_factory=lambda: os.getenv("KARUKI_MAINTENANCE_TOKEN", "")
    )

    # Who a request belongs to when there is no auth configured. Chapter 11's
    # "everything runs locally" rests on this; nothing in production does.
    dev_user_id: str = field(
        default_factory=lambda: os.getenv(
            "KARUKI_DEV_USER_ID", "00000000-0000-0000-0000-000000000001"
        )
    )

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


settings = Settings()
