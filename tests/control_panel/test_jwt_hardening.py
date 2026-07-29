"""Platform login JWT: claims, type confusion, and password-change revocation.

The token used to carry only {sub, role, exp}: no issuer, no audience, no type
tag, and nothing tying it to the credential it was issued for. A 24h token
therefore stayed valid after a password change — the wrong answer precisely when
a credential is suspected compromised.
"""

from __future__ import annotations

import bcrypt
import pytest
from fastapi import HTTPException
from jose import jwt

from priva_common.config import get_settings
from priva_control_panel.services import auth as A


def _hash(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def _rec(password_hash: str, *, epoch: str | None = None):
    """A UserRecord as the in-process transport yields it (real hash)."""
    from priva_common.models.auth import UserRecord
    return UserRecord(username="alice", password_hash=password_hash, role="user",
                      account_id="acc-1", status="active", password_epoch=epoch)


def _grpc_rec(epoch: str):
    """A UserRecord as the gRPC transport yields it: the bcrypt hash is NEVER
    serialized, so password_hash is "" and only the digest arrives."""
    from priva_common.models.auth import UserRecord
    return UserRecord(username="alice", password_hash="", role="user",
                      account_id="acc-1", status="active", password_epoch=epoch)


def test_token_carries_the_hardening_claims():
    token = A.create_jwt("alice", "user", _rec(_hash("pw")))
    claims = jwt.get_unverified_claims(token)
    assert claims["sub"] == "alice" and claims["role"] == "user"
    for required in ("iat", "exp", "iss", "aud", "jti", "typ", "pwd"):
        assert required in claims, f"missing {required}"
    assert claims["typ"] == A.TOKEN_TYPE
    # the pwd epoch is a digest, never the hash itself
    assert claims["pwd"] != _hash("pw")
    assert len(claims["pwd"]) == 16


def test_two_tokens_get_distinct_jti():
    rec = _rec(_hash("pw"))
    a = jwt.get_unverified_claims(A.create_jwt("alice", "user", rec))
    b = jwt.get_unverified_claims(A.create_jwt("alice", "user", rec))
    assert a["jti"] != b["jti"]


def test_decode_rejects_a_foreign_issuer_or_audience():
    settings = get_settings()
    secret = settings.auth.jwt_secret
    import time

    base = {"sub": "alice", "role": "user", "iat": int(time.time()),
            "exp": int(time.time()) + 600, "typ": A.TOKEN_TYPE, "pwd": "x" * 16}

    wrong_iss = jwt.encode({**base, "iss": "somebody-else", "aud": A._audience()},
                           secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        A.decode_jwt(wrong_iss)

    wrong_aud = jwt.encode({**base, "iss": A._issuer(), "aud": "some-other-api"},
                           secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        A.decode_jwt(wrong_aud)


def test_decode_rejects_a_token_of_the_wrong_type():
    """A token minted for another purpose must not be replayable as a login."""
    import time

    settings = get_settings()
    other = jwt.encode(
        {"sub": "alice", "role": "user", "iat": int(time.time()),
         "exp": int(time.time()) + 600, "iss": A._issuer(), "aud": A._audience(),
         "typ": "refresh", "pwd": "x" * 16},
        settings.auth.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        A.decode_jwt(other)


def test_decode_requires_an_expiry():
    settings = get_settings()
    forever = jwt.encode(
        {"sub": "alice", "role": "user", "iss": A._issuer(), "aud": A._audience(),
         "typ": A.TOKEN_TYPE, "pwd": "x" * 16},
        settings.auth.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        A.decode_jwt(forever)


@pytest.mark.asyncio
async def test_password_change_revokes_already_issued_tokens(monkeypatch):
    """The reported gap: change_my_password only rewrote the hash, so every live
    session survived a credential rotation."""
    from priva_common.models.auth import UserRecord

    old_hash, new_hash = _hash("old-password"), _hash("new-password")
    record = UserRecord(username="alice", password_hash=old_hash, role="user",
                        account_id="acc-1", status="active")

    store = type("S", (), {
        "get_user": lambda self, u: record,
        "find_by_api_key": lambda self, t: None,
        "has_users": lambda self: True,
    })()
    monkeypatch.setattr(A, "get_user_store", lambda: store)

    token = A.create_jwt("alice", "user", record)
    assert (await A.authenticate_raw_token(token)).username == "alice"

    record.password_hash = new_hash            # the password changes
    with pytest.raises(HTTPException) as exc:  # the old session dies
        await A.authenticate_raw_token(token)
    assert exc.value.status_code == 401

    # a token minted after the change works again
    fresh = A.create_jwt("alice", "user", record)
    assert (await A.authenticate_raw_token(fresh)).username == "alice"


def test_mint_without_a_hash_resolves_it_rather_than_locking_the_account_out(monkeypatch):
    """Omitting the hash must not embed the epoch of an empty password — that
    would mint a token its own account can never use."""
    from priva_common.models.auth import UserRecord

    h = _hash("pw")
    record = UserRecord(username="alice", password_hash=h, role="user", status="active")
    store = type("S", (), {"get_user": lambda self, u: record})()
    monkeypatch.setattr(A, "get_user_store", lambda: store)

    claims = jwt.get_unverified_claims(A.create_jwt("alice", "user"))
    assert claims["pwd"] == A.password_epoch(h)


@pytest.mark.asyncio
async def test_revocation_works_on_the_grpc_transport(monkeypatch):
    """The shipped configuration. converters.user_from_pb sets password_hash=""
    deliberately (the hash must not cross the wire), so deriving the epoch
    locally gave sha256("") for EVERY account and the check compared a constant
    to itself — a no-op in every deployed cluster. data-spine now sends the
    digest instead.
    """
    record = _grpc_rec("epoch-before")
    store = type("S", (), {
        "get_user": lambda self, u: record,
        "find_by_api_key": lambda self, t: None,
        "has_users": lambda self: True,
    })()
    monkeypatch.setattr(A, "get_user_store", lambda: store)

    token = A.create_jwt("alice", "user", record)
    assert jwt.get_unverified_claims(token)["pwd"] == "epoch-before"
    assert (await A.authenticate_raw_token(token)).username == "alice"

    record.password_epoch = "epoch-after"        # data-spine reports a new digest
    with pytest.raises(HTTPException) as exc:
        await A.authenticate_raw_token(token)
    assert exc.value.status_code == 401


def test_a_token_without_an_audience_is_rejected():
    """python-jose short-circuits its audience check when the claim is absent,
    so `audience=` alone asserted nothing — require_aud is what enforces it."""
    import time
    settings = get_settings()
    no_aud = jwt.encode(
        {"sub": "alice", "role": "user", "iat": int(time.time()),
         "exp": int(time.time()) + 600, "iss": A._issuer(),
         "typ": A.TOKEN_TYPE, "pwd": "x" * 16},
        settings.auth.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException):
        A.decode_jwt(no_aud)


def test_malformed_claims_are_a_401_not_a_500():
    """TokenPayload(**data) used to sit outside the try, so a signature-valid
    token missing `role` raised ValidationError straight past the caller's
    `except HTTPException`."""
    import time
    settings = get_settings()
    bad = jwt.encode(
        {"sub": "alice", "iat": int(time.time()), "exp": int(time.time()) + 600,
         "iss": A._issuer(), "aud": A._audience(), "typ": A.TOKEN_TYPE},
        settings.auth.jwt_secret, algorithm="HS256")
    with pytest.raises(HTTPException) as exc:
        A.decode_jwt(bad)
    assert exc.value.status_code == 401
