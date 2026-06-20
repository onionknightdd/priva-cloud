"""Signed-header verify seam for the agent-runner.

Thin re-export of the shared HS256 runner-token verifier. Kept as its own
module so the prod swap (JWKS/mTLS) is a one-file change behind the same
``verify`` name referenced throughout the runner.
"""

from __future__ import annotations

from priva_common.runner_token import verify

__all__ = ["verify"]
