"""The token verifier (T-3.7).

This is the boundary. Everything else in the API trusts the user id that comes
out of here, so what matters is not that a good token works - it is that the
bad ones do not, one reason at a time.

Real ES256 keys, generated here: no network, no Supabase project, and no secret
in the repository. The tokens are signed for real and verified for real, which
is the only way this file means anything.
"""

import datetime as dt
import uuid

import pytest

jwt = pytest.importorskip("jwt", reason="pyjwt is in the api dependency group")

from apps.api.auth import (  # noqa: E402
    AuthError,
    NoAuth,
    SupabaseVerifier,
    bearer_token,
    get_verifier,
)

ISSUER = "https://project.supabase.co/auth/v1"
USER = uuid.UUID("cd7708d6-5899-4d0c-9de7-7b165fd089ba")


@pytest.fixture(scope="module")
def keypair():
    """One EC key, and the JWKS a project would publish for it."""
    from cryptography.hazmat.primitives.asymmetric import ec

    private = ec.generate_private_key(ec.SECP256R1())
    public = jwt.algorithms.ECAlgorithm(jwt.algorithms.ECAlgorithm.SHA256).to_jwk(
        private.public_key(), as_dict=True
    )
    public["kid"] = "test-key"
    public["alg"] = "ES256"
    return private, public


@pytest.fixture
def verifier(keypair) -> SupabaseVerifier:
    _, public = keypair
    made = SupabaseVerifier(jwks_url="https://project.supabase.co/x", issuer=ISSUER)
    # The keys a fetch would have produced. Nothing here touches the network.
    made._keys = {public["kid"]: jwt.PyJWK(public).key}
    made._fetched_at = 1e12
    return made


def token(keypair, **claims) -> str:
    private, public = keypair
    now = dt.datetime.now(dt.UTC)
    payload = {
        "iss": ISSUER,
        "sub": str(USER),
        "aud": "authenticated",
        "exp": now + dt.timedelta(hours=1),
        "iat": now,
        "email": "singer@example.com",
        **claims,
    }
    return jwt.encode(payload, private, algorithm="ES256", headers={"kid": public["kid"]})


def test_a_real_token_names_its_user(verifier, keypair):
    assert verifier.user_id(token(keypair)) == USER


def test_an_expired_token_is_refused(verifier, keypair):
    """The claim that matters most: a token is a proof for an hour, not for
    ever, and signing out has to mean something."""
    stale = token(keypair, exp=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1))

    with pytest.raises(AuthError):
        verifier.user_id(stale)


def test_a_token_signed_by_somebody_else_is_refused(verifier):
    """The whole reason this is not hand-rolled. A verifier that got the curve
    arithmetic subtly wrong would accept this and say nothing."""
    from cryptography.hazmat.primitives.asymmetric import ec

    forger = ec.generate_private_key(ec.SECP256R1())
    forged = jwt.encode(
        {
            "iss": ISSUER,
            "sub": str(uuid.uuid4()),
            "aud": "authenticated",
            "exp": dt.datetime.now(dt.UTC) + dt.timedelta(hours=1),
        },
        forger,
        algorithm="ES256",
        headers={"kid": "test-key"},
    )

    with pytest.raises(AuthError):
        verifier.user_id(forged)


def test_a_token_from_another_project_is_refused(verifier, keypair):
    """Same algorithm, same shape, different issuer - which is what a token
    from somebody else's Supabase project looks like."""
    with pytest.raises(AuthError):
        verifier.user_id(token(keypair, iss="https://someone-else.supabase.co/auth/v1"))


def test_a_token_for_another_audience_is_refused(verifier, keypair):
    with pytest.raises(AuthError):
        verifier.user_id(token(keypair, aud="some-other-service"))


def test_an_unsigned_token_is_refused(verifier):
    """`alg: none` is the oldest trick there is, and the one a library exists
    to have already thought about."""
    unsigned = jwt.encode({"sub": str(USER), "aud": "authenticated"}, key="", algorithm="none")

    with pytest.raises(AuthError):
        verifier.user_id(unsigned)


def test_a_token_with_no_subject_is_refused(verifier, keypair):
    with pytest.raises(AuthError):
        verifier.user_id(token(keypair, sub=""))


def test_nonsense_is_refused_rather_than_crashing(verifier):
    for rubbish in ("", "not-a-token", "a.b.c", "Bearer something"):
        with pytest.raises(AuthError):
            verifier.user_id(rubbish)


def test_no_token_at_all_is_refused(verifier):
    with pytest.raises(AuthError):
        verifier.user_id(None)


# --- the header -------------------------------------------------------------


def test_the_token_comes_out_of_a_bearer_header():
    assert bearer_token("Bearer abc.def.ghi") == "abc.def.ghi"
    assert bearer_token("bearer abc.def.ghi") == "abc.def.ghi", "the scheme is case-insensitive"


def test_anything_else_carries_no_token():
    assert bearer_token(None) is None
    assert bearer_token("") is None
    assert bearer_token("Basic dXNlcjpwYXNz") is None
    assert bearer_token("Bearer ") is None


# --- choosing one -----------------------------------------------------------


def test_a_project_url_means_real_verification():
    assert isinstance(get_verifier("https://project.supabase.co", str(USER)), SupabaseVerifier)


def test_no_project_means_the_local_user():
    """Chapter 11: the whole product runs on a machine with no accounts on it.
    `create_app` is what refuses this in production."""
    chosen = get_verifier("", str(USER))

    assert isinstance(chosen, NoAuth)
    assert chosen.user_id(None) == USER
