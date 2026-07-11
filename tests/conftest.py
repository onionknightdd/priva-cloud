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
