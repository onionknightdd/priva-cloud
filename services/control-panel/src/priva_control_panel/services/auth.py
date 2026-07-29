from __future__ import annotations

import secrets
import time
from datetime import datetime, timedelta, timezone

from fastapi import Depends, Header, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt

from priva_common.logging import get_app_logger
from priva_common.models.auth import TokenPayload, UserPublic, UserRecord, password_epoch
from priva_common.config import get_settings
from priva_common.user_store import get_user_store

logger = get_app_logger(__name__)

security = HTTPBearer(auto_error=False)


TOKEN_TYPE = "access"


def _edge():
    """Edge settings, tolerating the SimpleNamespace settings stubs used in tests."""
    return getattr(get_settings(), "edge", None)


def _audience() -> str:
    edge = _edge()
    return (getattr(edge, "jwt_audience", None) or "priva-api") if edge else "priva-api"


def _issuer() -> str:
    edge = _edge()
    return getattr(edge, "jwt_issuer", "priva-cp") if edge else "priva-cp"


def _epoch_of(user) -> str:
    """The password epoch for an account.

    Prefer the digest data-spine computed and sent: over gRPC ``password_hash``
    is ALWAYS "" (it is deliberately never serialized), so deriving the digest
    locally produced the same constant for every account and made the whole
    revocation check a no-op in every deployed configuration. The local
    derivation is kept only for the in-process transport, where the real hash IS
    present.
    """
    if user is None:
        return password_epoch("")
    carried = getattr(user, "password_epoch", None)
    if carried:
        return carried
    return password_epoch(getattr(user, "password_hash", "") or "")


def create_jwt(username: str, role: str, user=None) -> str:
    """Mint a platform access token. ``user`` is the freshly-loaded record; it
    carries the password epoch that binds this session to the credential."""
    settings = get_settings()
    if user is None:
        try:
            user = get_user_store().get_user(username)
        except Exception:
            user = None
    now = datetime.now(timezone.utc)
    payload = {
        "sub": username,
        "role": role,
        "iat": now,
        "exp": now + timedelta(hours=settings.auth.jwt_expire_hours),
        "iss": _issuer(),
        "aud": _audience(),
        "jti": secrets.token_hex(16),   # per-token id, for audit and future denylisting
        "typ": TOKEN_TYPE,
        "pwd": _epoch_of(user),
    }
    return jwt.encode(payload, settings.auth.jwt_secret, algorithm="HS256")


def decode_jwt(token: str) -> TokenPayload:
    settings = get_settings()
    try:
        data = jwt.decode(
            token, settings.auth.jwt_secret, algorithms=["HS256"],
            audience=_audience(), issuer=_issuer(),
            options={"require_exp": True, "require_iat": True,
                     "require_aud": True, "require_iss": True, "require_sub": True},
        )
    except JWTError as e:
        raise HTTPException(401, "Invalid or expired token") from e
    try:
        payload = TokenPayload(**data)
    except Exception as exc:  # signature-valid but malformed claims => 401, not 500
        raise HTTPException(401, "Invalid or expired token") from exc
    if payload.typ != TOKEN_TYPE:
        # e.g. a runner or service token replayed at the platform edge
        raise HTTPException(401, "Invalid or expired token")
    return payload


def user_record_to_public(user: UserRecord) -> UserPublic:
    return UserPublic(
        username=user.username,
        role=user.role,
        api_key=user.api_key,
        created_at=user.created_at,
        updated_at=user.updated_at,
        status=user.status,
        agent_runner_type=user.agent_runner_type,
    )


def assert_account_active(user: UserRecord) -> None:
    """Fail-closed account lifecycle gate — anything that isn't exactly ``active``
    (disabled / offboarding / purged) is refused.

    Applied at token resolution, so an admin disable revokes every already-issued JWT
    and API key on the very next request instead of at token expiry. 403 (vs the 401
    unauth path) so the SPA can distinguish "revoked" from "log in again", matching
    the EPP's data-plane gate.
    """
    if user.status != "active":
        raise HTTPException(403, "Account access revoked")


async def authenticate_raw_token(
    token: str | None,
    x_user_name: str | None = None,
) -> UserRecord | None:
    """Core auth logic: JWT, per-user API key, global API key, anonymous.

    Single source of truth — used by both HTTP (get_current_user) and WebSocket.
    Returns UserRecord on success, None for anonymous, raises HTTPException on failure.
    Credentials that belong to an account (JWT / per-user API key) additionally pass
    the lifecycle gate; the platform-wide global key is an operator escape hatch and
    keeps resolving so an admin can still act on a frozen account.
    """
    settings = get_settings()
    store = get_user_store()

    if token:
        # 1. Try JWT
        user = None
        try:
            payload = decode_jwt(token)
            user = store.get_user(payload.sub)
            if user and payload.pwd != _epoch_of(user):
                # Minted before the current password: a password change revokes
                # every session issued under the old credential.
                logger.info("rejecting token for {} — password changed since issue",
                            payload.sub)
                user = None
        except HTTPException:
            pass
        if user:
            assert_account_active(user)
            return user

        # 2. Try per-user API key. Guarded: data-spine raises when its HMAC secret
        # is unset, and every rejected JWT falls through to here — an unguarded
        # call turns a mis-provisioned data-spine into a 500 on each 401.
        try:
            user = store.find_by_api_key(token)
        except HTTPException:
            raise
        except Exception:
            logger.warning("api-key lookup failed", exc_info=True)
            user = None
        if user:
            assert_account_active(user)
            return user

        # 3. Try global API key
        if settings.auth.global_api_key and token == settings.auth.global_api_key:
            target_username = x_user_name or "admin"
            user = store.get_user(target_username)
            if user:
                return user.model_copy(update={"role": "admin"})
            return UserRecord(
                username=target_username,
                password_hash="",
                role="admin",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )

        raise HTTPException(401, "Invalid credentials")

    # No token provided
    if settings.auth.enable_anonymous:
        return None
    if store.has_users():
        raise HTTPException(401, "Authentication required")
    return None


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(security),
    x_user_name: str | None = Header(None, alias="x-user-name"),
) -> UserRecord | None:
    token = credentials.credentials if credentials else None
    user = await authenticate_raw_token(token, x_user_name)
    request.state.user = user
    # Tag auth method: JWT succeeds at step 1, API key at step 2/3
    if token and user:
        try:
            decode_jwt(token)
            request.state.auth_method = "jwt"
        except HTTPException:
            request.state.auth_method = "api_key"
    else:
        request.state.auth_method = "anonymous"
    return user


async def require_user(
    user: UserRecord | None = Depends(get_current_user),
) -> UserRecord:
    if user is None:
        raise HTTPException(401, "Authentication required")
    return user


async def require_active_account(
    user: UserRecord = Depends(require_user),
) -> UserRecord:
    """Require a provisioned account that is still allowed to use runtimes.

    The lifecycle half is already enforced at token resolution; this adds the
    provisioning half, so runtime discovery matches the EPP's fail-closed gate: an
    account without a tenant cannot discover or wake a Runner or Terminal pod.
    """
    assert_account_active(user)
    if not user.account_id:
        raise HTTPException(403, "Account is not provisioned")
    return user


async def require_admin(
    user: UserRecord = Depends(require_user),
) -> UserRecord:
    if user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return user


class LoginRateLimiter:
    """Two-dimensional attempt limiter.

    Keying on the username alone had it backwards in both directions: an attacker
    could rotate usernames to spend an unlimited budget, while five bad guesses
    against a *victim's* name locked that victim out — a free denial-of-service
    against any account whose username you know.

    So there are two buckets. The per-(ip, username) bucket bounds guessing at one
    account; the wider per-ip bucket bounds credential stuffing across many
    accounts. A victim is never locked out by someone else's source address.

    Known limitation: in-process, so N control-panel replicas mean N budgets.
    That is bounded and defensible at replicas=1 (the shipped default); a shared
    store is the fix when the control plane scales out.
    """

    def __init__(self, max_attempts: int = 5, window_seconds: int = 60,
                 max_per_ip: int = 30):
        self._attempts: dict[tuple[str, str], list[float]] = {}
        self._max = max_attempts
        self._max_per_ip = max_per_ip
        self._window = window_seconds

    def _recent(self, key: tuple[str, str], now: float) -> list[float]:
        hits = [t for t in self._attempts.get(key, []) if now - t < self._window]
        if hits:
            self._attempts[key] = hits
        else:
            self._attempts.pop(key, None)
        return hits

    def _sweep(self, now: float) -> None:
        # The dict is keyed by attacker-supplied values; drop empty buckets so a
        # username/IP-rotating attacker cannot grow it without bound.
        for key in [k for k, v in self._attempts.items()
                    if not any(now - t < self._window for t in v)]:
            self._attempts.pop(key, None)

    def check(self, username: str, client_ip: str = "") -> None:
        now = time.time()
        self._sweep(now)
        if len(self._recent(("ip", client_ip), now)) >= self._max_per_ip:
            raise HTTPException(429, "Too many attempts from this address, try again later")
        if len(self._recent((client_ip, username), now)) >= self._max:
            raise HTTPException(429, "Too many login attempts, try again later")

    def record_failure(self, username: str, client_ip: str = "") -> None:
        now = time.time()
        self._attempts.setdefault((client_ip, username), []).append(now)
        self._attempts.setdefault(("ip", client_ip), []).append(now)

    def reset(self, username: str, client_ip: str = "") -> None:
        # Only the account bucket clears on success; the per-ip bucket stands, so
        # one valid credential cannot launder an ongoing stuffing run.
        self._attempts.pop((client_ip, username), None)


rate_limiter = LoginRateLimiter()


def client_ip(request: Request) -> str:
    """Best-effort caller address.

    Behind the gateway the socket peer is the proxy, so prefer the leftmost
    X-Forwarded-For hop. Spoofable by design — this is a rate-limit key, not an
    authorization input.
    """
    forwarded = request.headers.get("x-forwarded-for", "")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return getattr(request.client, "host", "") or ""
