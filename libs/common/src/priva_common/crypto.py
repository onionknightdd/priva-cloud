from __future__ import annotations

import os
import threading

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

ENC_PREFIX = "enc:v1:"

# Dev fallback key. Historically the ONLY key, so every pre-hardening ciphertext
# (account.api_key, feishu app_secret, …) is encrypted under it. It stays in the
# DECRYPT set forever so old ciphertext keeps opening.
#
# It is public — it is committed to this repo — so anyone with the source and a
# database dump can read anything encrypted under it. It therefore must never
# ENCRYPT: without an explicit dev opt-in, a missing PRIVA_FERNET_KEY is a
# mis-provisioned deployment and encryption fails loudly instead of silently
# writing recoverable-by-anyone ciphertext.
_DEV_FALLBACK_KEY = "sbKkbgCHmtelZW8UO56q5q1JF-s4Uqs_qVPRylym1xY="

# Opt IN to encrypting under the public dev key. Absent => production posture.
_ALLOW_DEV_ENV = "PRIVA_ALLOW_DEV_FERNET"

_MISCONFIGURED = (
    "PRIVA_FERNET_KEY is not set (or is malformed). Refusing to encrypt: the only "
    "remaining key is the dev fallback, which is published in this repository, so "
    "the ciphertext would be readable by anyone with the source. Provision a "
    f"K8s-Secret-sourced key, or set {_ALLOW_DEV_ENV}=1 for local development."
)

_lock = threading.Lock()
_state: tuple[MultiFernet, bool] | None = None  # (fernet, primary_is_real)


def _allow_dev() -> bool:
    return os.environ.get(_ALLOW_DEV_ENV, "").strip().lower() in ("1", "true", "yes")


def _build() -> tuple[MultiFernet, bool]:
    """MultiFernet: encrypt with the first key, decrypt against all of them.

    Primary = ``PRIVA_FERNET_KEY``. Rotation: ``PRIVA_FERNET_KEYS_OLD``
    (comma-separated) are decrypt-only. The dev fallback is always appended last
    so legacy ciphertext still opens — but being last means it never encrypts
    unless it is the only key left.
    """
    def _load(key: str) -> Fernet | None:
        try:
            return Fernet(key.strip().encode())
        except (ValueError, TypeError):
            logger.error("Ignoring malformed Fernet key (must be 32 url-safe base64 bytes)")
            return None

    # The encryption key is whatever PRIVA_FERNET_KEY holds — never "whatever
    # ended up first". Inferring the primary from list position meant that with
    # PRIVA_FERNET_KEY unset but PRIVA_FERNET_KEYS_OLD set, a RETIRED key slid
    # into slot 0 and became the encryption key, while the config looked valid.
    primary_raw = os.environ.get("PRIVA_FERNET_KEY", "").strip()
    primary = _load(primary_raw) if primary_raw else None
    primary_is_real = primary is not None

    fernets: list[Fernet] = []
    seen: set[str] = set()
    if primary is not None:
        fernets.append(primary)
        seen.add(primary_raw)
    elif _allow_dev():
        # Dev opt-in with no primary: the dev key encrypts, and it must go FIRST.
        # Appending it last let a retired PRIVA_FERNET_KEYS_OLD entry take slot 0
        # and quietly become the encryption key while the log said otherwise.
        fernets.append(Fernet(_DEV_FALLBACK_KEY.encode()))
        seen.add(_DEV_FALLBACK_KEY)

    # Decrypt-only, in order. These can never encrypt: they are never first
    # unless there is no primary at all, in which case encryption is refused.
    for old in os.environ.get("PRIVA_FERNET_KEYS_OLD", "").split(","):
        old = old.strip()
        if not old or old in seen:
            continue
        loaded = _load(old)
        if loaded is not None:
            seen.add(old)
            fernets.append(loaded)

    if _DEV_FALLBACK_KEY not in seen:
        fernets.append(Fernet(_DEV_FALLBACK_KEY.encode()))  # opens legacy ciphertext

    if not primary_is_real:
        if _allow_dev():
            logger.warning(
                "PRIVA_FERNET_KEY not set — encrypting with the PUBLIC dev fallback key "
                "because {} is set. Never do this outside local development.", _ALLOW_DEV_ENV,
            )
        else:
            logger.error(_MISCONFIGURED)
    return MultiFernet(fernets), primary_is_real


def _fernet() -> tuple[MultiFernet, bool]:
    global _state
    if _state is None:
        with _lock:
            if _state is None:
                _state = _build()
    return _state


def reset() -> None:
    """Test seam — re-read the environment on next use."""
    global _state
    with _lock:
        _state = None


def encryption_key_is_configured() -> bool:
    return _fernet()[1]


def assert_encryption_key_configured() -> None:
    """Boot-time gate. Call from a service's startup so a mis-provisioned
    deployment dies immediately instead of at the first credential write."""
    fernet, primary_is_real = _fernet()
    if not primary_is_real and not _allow_dev():
        raise RuntimeError(_MISCONFIGURED)


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_value(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    if is_encrypted(plaintext):
        return plaintext
    fernet, primary_is_real = _fernet()
    if not primary_is_real and not _allow_dev():
        raise RuntimeError(_MISCONFIGURED)
    return ENC_PREFIX + fernet.encrypt(plaintext.encode()).decode()


def decrypt_value(stored: str | None) -> str | None:
    """Decryption always tries every key, dev fallback included: reading legacy
    ciphertext is how a deployment migrates off it."""
    if stored is None:
        return None
    if not is_encrypted(stored):
        return stored
    try:
        return _fernet()[0].decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt value: invalid token or corrupt data")
        return None
