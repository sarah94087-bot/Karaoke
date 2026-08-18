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

    @property
    def is_local(self) -> bool:
        return self.environment == "local"


settings = Settings()
