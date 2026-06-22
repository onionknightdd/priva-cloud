"""Shared fixtures for the operator unit suite.

The kopf handlers are plain functions (kopf's decorators return them unchanged), so
the tests call them directly with fabricated spec/status + a stub patch/logger and a
mocked kube client — no cluster, no kopf runtime.
"""

from __future__ import annotations

import pytest


class _Patch:
    """Stand-in for kopf's patch object — handlers write ``patch.status[...]`` and kopf
    applies it as a single PATCH on return. The tests inspect what was written."""

    def __init__(self) -> None:
        self.status: dict = {}


class _Logger:
    """No-op logger (handlers log freely; tests don't assert on it)."""

    def info(self, *a, **k) -> None: ...
    def warning(self, *a, **k) -> None: ...
    def debug(self, *a, **k) -> None: ...
    def error(self, *a, **k) -> None: ...


@pytest.fixture
def patch_obj() -> _Patch:
    return _Patch()


@pytest.fixture
def stub_logger() -> _Logger:
    return _Logger()
