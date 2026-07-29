"""Test-harness store default: pin the legacy sqlite backend.

The PRODUCT default is postgres (priva_common.config.DataspineSettings.backend),
which needs a reachable server; the suite pins sqlite here so a bare `pytest`
needs zero infrastructure. The Postgres implementation is exercised by the
backend-parametrized dataplane tests (tests/api/test_dataplane_grpc.py,
tests/api/test_migrate_to_pg.py) whenever TEST_POSTGRES_DSN points at a
disposable server. setdefault: an explicit outer env still wins.
"""

import os

os.environ.setdefault("PRIVA_DATASPINE__BACKEND", "sqlite")
# The api-key lookup HMAC no longer falls back to auth.jwt_secret (the two were
# one leaked value away from forging both platform logins and lookup entries).
# Helm provisions it per-deployment; the suite pins a throwaway value.
os.environ.setdefault("PRIVA_DATASPINE__API_KEY_HMAC_SECRET", "test-api-key-hmac-secret")
# priva_common.crypto refuses to encrypt under the public dev fallback key unless
# this opt-in is present, so a deployment that forgets PRIVA_FERNET_KEY fails loudly
# instead of writing credentials anyone with this repo can decrypt.
os.environ.setdefault("PRIVA_ALLOW_DEV_FERNET", "1")

# Services now refuse to boot without a provisioned signing identity; the suite
# runs single-process where the ephemeral keypair is the intended mode.
# tests/api/test_service_identity_distribution.py covers the production posture.
os.environ.setdefault("PRIVA_ALLOW_EPHEMERAL_IDENTITY", "1")
