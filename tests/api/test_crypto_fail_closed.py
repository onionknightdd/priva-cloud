"""At-rest encryption must never silently fall back to the public dev key.

`_DEV_FALLBACK_KEY` is committed to this repository. Before this, a deployment
that forgot PRIVA_FERNET_KEY logged one warning and then encrypted every stored
api_key and Feishu app_secret under it — recoverable by anyone holding the
source plus a database dump. Helm never injected the key, so that was the state
of every deployed cluster.

The fix splits the key's two roles: it stays in the DECRYPT set forever (legacy
ciphertext must keep opening, and re-encryption needs to read it), but it may
only ENCRYPT behind an explicit local-dev opt-in.
"""

from __future__ import annotations

import pytest

from priva_common import crypto


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("PRIVA_FERNET_KEY", "PRIVA_FERNET_KEYS_OLD", "PRIVA_ALLOW_DEV_FERNET"):
        monkeypatch.delenv(var, raising=False)
    crypto.reset()
    yield monkeypatch
    crypto.reset()


def _real_key() -> str:
    from cryptography.fernet import Fernet
    return Fernet.generate_key().decode()


def test_encrypt_refuses_without_a_provisioned_key(clean_env):
    assert not crypto.encryption_key_is_configured()
    with pytest.raises(RuntimeError, match="PRIVA_FERNET_KEY is not set"):
        crypto.encrypt_value("sk-ant-real-credential")


def test_boot_gate_refuses_to_start(clean_env):
    """data-spine calls this in serve(); a mis-provisioned deployment must die
    at boot, not at the first credential write."""
    with pytest.raises(RuntimeError, match="PRIVA_FERNET_KEY is not set"):
        crypto.assert_encryption_key_configured()


def test_legacy_ciphertext_still_decrypts_without_a_key(clean_env):
    """The migration path: read what the dev key wrote, so it can be re-encrypted."""
    clean_env.setenv("PRIVA_ALLOW_DEV_FERNET", "1")
    crypto.reset()
    legacy = crypto.encrypt_value("old-secret")

    clean_env.delenv("PRIVA_ALLOW_DEV_FERNET")
    crypto.reset()
    assert not crypto.encryption_key_is_configured()
    assert crypto.decrypt_value(legacy) == "old-secret"   # readable
    with pytest.raises(RuntimeError):                     # but not writable
        crypto.encrypt_value("new-secret")


def test_a_real_key_encrypts_and_the_dev_key_is_never_primary(clean_env):
    key = _real_key()
    clean_env.setenv("PRIVA_FERNET_KEY", key)
    crypto.reset()

    assert crypto.encryption_key_is_configured()
    crypto.assert_encryption_key_configured()
    blob = crypto.encrypt_value("sk-ant-real-credential")
    assert crypto.decrypt_value(blob) == "sk-ant-real-credential"

    # the ciphertext must NOT be readable under the public dev key alone
    from cryptography.fernet import Fernet, InvalidToken
    dev = Fernet(crypto._DEV_FALLBACK_KEY.encode())
    with pytest.raises(InvalidToken):
        dev.decrypt(blob[len(crypto.ENC_PREFIX):].encode())


def test_malformed_key_does_not_demote_to_the_dev_key(clean_env):
    """A typo'd Secret used to silently degrade to the public key."""
    clean_env.setenv("PRIVA_FERNET_KEY", "obviously-not-base64-32-bytes")
    crypto.reset()
    assert not crypto.encryption_key_is_configured()
    with pytest.raises(RuntimeError):
        crypto.encrypt_value("sk-ant-real-credential")


def test_rotation_reads_old_and_writes_new(clean_env):
    old, new = _real_key(), _real_key()
    clean_env.setenv("PRIVA_FERNET_KEY", old)
    crypto.reset()
    written_under_old = crypto.encrypt_value("credential")

    clean_env.setenv("PRIVA_FERNET_KEY", new)
    clean_env.setenv("PRIVA_FERNET_KEYS_OLD", old)
    crypto.reset()
    assert crypto.decrypt_value(written_under_old) == "credential"   # still readable
    rewritten = crypto.encrypt_value("credential")
    assert rewritten != written_under_old

    clean_env.delenv("PRIVA_FERNET_KEYS_OLD")   # retire the old key
    crypto.reset()
    assert crypto.decrypt_value(rewritten) == "credential"
    assert crypto.decrypt_value(written_under_old) is None


def test_a_retired_key_never_becomes_the_encryption_key(clean_env):
    """PRIVA_FERNET_KEY unset + PRIVA_FERNET_KEYS_OLD set used to slide a RETIRED,
    decrypt-only key into slot 0, where MultiFernet encrypts with it — and
    `encryption_key_is_configured()` reported True, so the boot gate passed."""
    old = _real_key()
    clean_env.setenv("PRIVA_FERNET_KEYS_OLD", old)
    crypto.reset()

    assert not crypto.encryption_key_is_configured()
    with pytest.raises(RuntimeError):
        crypto.assert_encryption_key_configured()
    with pytest.raises(RuntimeError):
        crypto.encrypt_value("real-credential")


def test_a_malformed_primary_does_not_promote_an_old_key(clean_env):
    old = _real_key()
    clean_env.setenv("PRIVA_FERNET_KEY", "not-a-valid-key")
    clean_env.setenv("PRIVA_FERNET_KEYS_OLD", old)
    crypto.reset()

    assert not crypto.encryption_key_is_configured()
    with pytest.raises(RuntimeError):
        crypto.encrypt_value("real-credential")


def test_old_keys_still_decrypt_while_the_primary_encrypts(clean_env):
    old, new = _real_key(), _real_key()
    clean_env.setenv("PRIVA_FERNET_KEY", old)
    crypto.reset()
    legacy = crypto.encrypt_value("credential")

    clean_env.setenv("PRIVA_FERNET_KEY", new)
    clean_env.setenv("PRIVA_FERNET_KEYS_OLD", old)
    crypto.reset()
    assert crypto.decrypt_value(legacy) == "credential"

    from cryptography.fernet import Fernet, InvalidToken
    fresh = crypto.encrypt_value("credential")
    with pytest.raises(InvalidToken):   # written under the NEW primary, not the old one
        Fernet(old.encode()).decrypt(fresh[len(crypto.ENC_PREFIX):].encode())


def test_dev_opt_in_encrypts_with_the_dev_key_not_a_retired_one(clean_env):
    """With the dev opt-in and no primary, the dev key must be the one that
    encrypts. Appending it last let a PRIVA_FERNET_KEYS_OLD entry take slot 0 and
    silently become the encryption key while the log claimed the dev fallback."""
    from cryptography.fernet import Fernet

    old = _real_key()
    clean_env.setenv("PRIVA_ALLOW_DEV_FERNET", "1")
    clean_env.setenv("PRIVA_FERNET_KEYS_OLD", old)
    crypto.reset()

    blob = crypto.encrypt_value("x")
    ciphertext = blob[len(crypto.ENC_PREFIX):].encode()
    assert Fernet(crypto._DEV_FALLBACK_KEY.encode()).decrypt(ciphertext) == b"x"
    assert crypto.decrypt_value(blob) == "x"      # the old key still opens legacy data
