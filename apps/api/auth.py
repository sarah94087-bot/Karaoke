"""Who is asking (T-3.7). D-16 is Supabase Auth.

The browser signs in against Supabase and gets a JWT; this verifies it and turns
it into a user id. Verification is **local** - the project's public keys are
fetched once and cached - because the alternative is an HTTP call to Supabase on
every single request, which on a free instance is a round trip added to every
page the user opens.

**This is the one signature in this project that is not hand-rolled**, and the
difference is worth stating. The storage links and the S3 requests are HMAC:
one hash, one comparison, and a mistake makes the service refuse everything,
loudly. Supabase signs with ES256 - elliptic curve - where a subtly wrong
verifier does the opposite: it accepts tokens it should not, silently. So this
file uses PyJWT, which is the only new dependency the API has taken since
phase 1.

`AUTH_NONE` exists because chapter 11 requires the whole product to run locally,
and a machine with no Supabase project should still be able to upload a song and
sing. It is not a fallback that can happen by accident: the API refuses to start
in production without a real verifier, and says so at startup either way.
"""

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

log = logging.getLogger("karuki.auth")

# How long a set of public keys is trusted before it is fetched again. Supabase
# rotates rarely; an hour bounds how long a revoked key would keep working
# without making the fetch part of a request's cost.
JWKS_TTL_SECONDS = 3600


class AuthError(RuntimeError):
    """The token is missing, malformed, expired or not ours."""


class Verifier(Protocol):
    """What the API needs from whatever proves identity."""

    name: str

    def user_id(self, token: str | None) -> uuid.UUID:
        """The user the token names, or raise."""
        ...


@dataclass
class NoAuth:
    """Everybody is the same local user.

    For `python -m apps.api` on a machine with no Supabase project, and for the
    test suite, which would otherwise need a signing key to exercise anything at
    all. Chapter 11 is why this exists; the startup warning is why it cannot be
    mistaken for the real thing.
    """

    name: str = "none"
    dev_user: uuid.UUID = uuid.UUID("00000000-0000-0000-0000-000000000001")

    def user_id(self, token: str | None) -> uuid.UUID:
        return self.dev_user


@dataclass
class SupabaseVerifier:
    """Verify a Supabase access token against the project's published keys."""

    jwks_url: str
    issuer: str
    name: str = "supabase"
    audience: str = "authenticated"
    _keys: dict[str, Any] = field(default_factory=dict, repr=False)
    _fetched_at: float = 0.0

    def user_id(self, token: str | None) -> uuid.UUID:
        if not token:
            raise AuthError("no token")
        import jwt

        try:
            header = jwt.get_unverified_header(token)
        except Exception as exc:
            raise AuthError(f"malformed token: {exc}") from exc

        key = self._key_for(header.get("kid"))
        try:
            claims = jwt.decode(
                token,
                key=key,
                algorithms=[header.get("alg", "ES256")],
                audience=self.audience,
                issuer=self.issuer,
                # The defaults, named rather than assumed: these are the checks
                # that make a token a proof rather than a string.
                options={"require": ["exp", "sub"], "verify_exp": True, "verify_aud": True},
            )
        except Exception as exc:
            raise AuthError(f"token rejected: {exc}") from exc

        subject = claims.get("sub", "")
        try:
            return uuid.UUID(subject)
        except ValueError as exc:
            raise AuthError(f"token subject is not a user id: {subject!r}") from exc

    def _key_for(self, kid: str | None) -> Any:
        import time

        fresh = time.time() - self._fetched_at < JWKS_TTL_SECONDS
        if not fresh or (kid and kid not in self._keys):
            # Re-fetched when a `kid` is unknown as well as on expiry: that is
            # what a key rotation looks like from here, and refusing every
            # request until an hour has passed would be an outage of our making.
            self._fetch_keys()
        if kid and kid in self._keys:
            return self._keys[kid]
        if len(self._keys) == 1:
            return next(iter(self._keys.values()))
        raise AuthError(f"no public key for kid {kid!r}")

    def _fetch_keys(self) -> None:
        import json
        import urllib.request

        import jwt

        from packages.providers.net import USER_AGENT, trust_system_certificates

        trust_system_certificates()
        request = urllib.request.Request(self.jwks_url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                document = json.load(response)
        except OSError as exc:
            # Keep whatever was cached: a network blip should not sign
            # everybody out of a service that is otherwise working.
            log.warning("could not fetch %s: %s", self.jwks_url, exc)
            if self._keys:
                return
            raise AuthError("the identity provider is unreachable") from exc

        import time

        self._keys = {
            entry["kid"]: jwt.PyJWK(entry).key
            for entry in document.get("keys", [])
            if entry.get("kid")
        }
        self._fetched_at = time.time()
        log.info("loaded %d signing key(s) from %s", len(self._keys), self.jwks_url)


def get_verifier(supabase_url: str, dev_user_id: str) -> Verifier:
    """Supabase when there is a project, the local user when there is not."""
    if not supabase_url:
        log.warning(
            "no SUPABASE_URL: every request is attributed to the local development user, "
            "and songs are not protected from one another"
        )
        return NoAuth(dev_user=uuid.UUID(dev_user_id))
    base = supabase_url.rstrip("/")
    return SupabaseVerifier(
        jwks_url=f"{base}/auth/v1/.well-known/jwks.json",
        issuer=f"{base}/auth/v1",
    )


def bearer_token(header: str | None) -> str | None:
    """The token out of an `Authorization: Bearer …` header."""
    if not header:
        return None
    scheme, _, value = header.partition(" ")
    if scheme.lower() != "bearer" or not value.strip():
        return None
    return value.strip()
