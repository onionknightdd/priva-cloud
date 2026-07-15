from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

ENC_PREFIX = "enc:v1:"

# Dev fallback key. Historically the ONLY key, so every pre-hardening ciphertext
# (account.api_key, …) is encrypted under it. It stays in the decrypt set FOREVER
# (as a trailing old-key) even after a real PRIVA_FERNET_KEY is provisioned, so
# old ciphertext keeps opening. NEVER encrypt real third-party secrets under this
# alone in prod — set PRIVA_FERNET_KEY (a K8s Secret).
_DEV_FALLBACK_KEY = "sbKkbgCHmtelZW8UO56q5q1JF-s4Uqs_qVPRylym1xY="


def _build_fernet() -> MultiFernet:
    """MultiFernet: encrypt with the primary key, decrypt against primary + old.

    Primary = ``PRIVA_FERNET_KEY`` (K8s Secret) or the dev fallback. Rotation:
    ``PRIVA_FERNET_KEYS_OLD`` (comma-separated) are decrypt-only. The dev fallback
    is always appended last so ciphertext written before hardening still decrypts.
    MultiFernet encrypts with the FIRST fernet and decrypts by trying each in order.
    """
    ordered: list[str] = []
    seen: set[str] = set()

    def _add(key: str) -> None:
        key = key.strip()
        if key and key not in seen:
            seen.add(key)
            ordered.append(key)

    primary = os.environ.get("PRIVA_FERNET_KEY", "").strip()
    _add(primary)
    for old in os.environ.get("PRIVA_FERNET_KEYS_OLD", "").split(","):
        _add(old)
    _add(_DEV_FALLBACK_KEY)  # trailing old-key: opens all legacy ciphertext

    fernets: list[Fernet] = []
    for key in ordered:
        try:
            fernets.append(Fernet(key.encode()))
        except (ValueError, TypeError):
            logger.error("Ignoring malformed Fernet key (must be 32 url-safe base64 bytes)")
    if not fernets:  # every provided key was malformed — fall back to dev key
        fernets.append(Fernet(_DEV_FALLBACK_KEY.encode()))

    if not primary:
        logger.warning(
            "PRIVA_FERNET_KEY not set — encrypting with the built-in dev fallback key. "
            "Provision a K8s-Secret-sourced key before storing real third-party credentials."
        )
    return MultiFernet(fernets)


_FERNET = _build_fernet()


def is_encrypted(value: str | None) -> bool:
    return isinstance(value, str) and value.startswith(ENC_PREFIX)


def encrypt_value(plaintext: str | None) -> str | None:
    if plaintext is None:
        return None
    if is_encrypted(plaintext):
        return plaintext
    return ENC_PREFIX + _FERNET.encrypt(plaintext.encode()).decode()


def decrypt_value(stored: str | None) -> str | None:
    if stored is None:
        return None
    if not is_encrypted(stored):
        return stored
    try:
        return _FERNET.decrypt(stored[len(ENC_PREFIX):].encode()).decode()
    except InvalidToken:
        logger.error("Failed to decrypt value: invalid token or corrupt data")
        return None
