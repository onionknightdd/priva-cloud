"""Test-harness store default: pin the legacy sqlite backend.

The PRODUCT default is postgres (priva_common.config.DataspineSettings.backend),
which needs a reachable server; the suite pins sqlite here so a bare `pytest`
needs zero infrastructure. The Postgres implementation is exercised by the
backend-parametrized dataplane tests (tests/api/test_dataplane_grpc.py,
tests/api/test_migrate_to_pg.py) whenever TEST_POSTGRES_DSN points at a
disposable server. setdefault: an explicit outer env still wins.
"""

import os

import pytest

os.environ.setdefault("PRIVA_DATASPINE__BACKEND", "sqlite")
# The api-key lookup HMAC no longer falls back to auth.jwt_secret (the two were
# one leaked value away from forging both platform logins and lookup entries).
# Helm provisions it per-deployment; the suite pins a throwaway value.
os.environ.setdefault("PRIVA_DATASPINE__API_KEY_HMAC_SECRET", "test-api-key-hmac-secret")
# PyJWT requires at least 256 bits for HS256. Use a throwaway but correctly sized
# test key so the suite exercises the production startup posture without warning.
os.environ.setdefault(
    "PRIVA_AUTH__JWT_SECRET",
    "test-only-jwt-secret-0123456789abcdef0123456789abcdef",
)
# priva_common.crypto refuses to encrypt under the public dev fallback key unless
# this opt-in is present, so a deployment that forgets PRIVA_FERNET_KEY fails loudly
# instead of writing credentials anyone with this repo can decrypt.
os.environ.setdefault("PRIVA_ALLOW_DEV_FERNET", "1")

# Services now refuse to boot without a provisioned signing identity; the suite
# runs single-process where the ephemeral keypair is the intended mode.
# tests/api/test_service_identity_distribution.py covers the production posture.
os.environ.setdefault("PRIVA_ALLOW_EPHEMERAL_IDENTITY", "1")
# There is deliberately no production default for the workload's own name — an
# unconfigured pod would otherwise inherit a real role's method allowlist at
# data-spine. The suite hosts several control-plane callers in one process, so
# name it here; `as_service_identity` overrides it per phase where it matters.
os.environ.setdefault("PRIVA_SERVICE_IDENTITY__SERVICE_NAME", "control-panel")


@pytest.fixture
def as_service_identity():
    """Switch the gRPC client's signed workload identity within one test.

    Transport tests exercise more than one production caller against one server;
    make each phase declare the same identity it has in the deployed process.
    """
    from priva_common import service_token
    from priva_common.config import get_settings

    settings = get_settings()
    saved = settings.dataspine.service_token

    def set_identity(svc: str, *, account_id: str | None = None) -> None:
        settings.dataspine.service_token = service_token.mint(
            svc, account_id=account_id
        )
        service_token.reset_cache()

    yield set_identity
    settings.dataspine.service_token = saved
    service_token.reset_cache()
