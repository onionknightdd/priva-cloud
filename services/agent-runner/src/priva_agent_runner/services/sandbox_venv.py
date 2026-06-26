"""Per-account Python venv on the persistent ``/workspace`` volume.

The runner pod has ``readOnlyRootFilesystem``, so the system site-packages
(``/usr/local``) can't be written — and wouldn't persist anyway, since only
``/workspace`` survives the pod's scale-to-zero/wake. So any package the AGENT
installs must live in a venv on ``/workspace``.

Crucially, that venv must be visible ONLY to the agent's command execution, never
to the runner SERVICE process — whose imports must stay on the pristine system
interpreter so a user-installed package can never shadow a dependency the service
needs. The isolation falls out of how ``claude_agent_sdk`` spawns the CLI: the
subprocess env is ``{**os.environ, **options.env}`` (subprocess_cli transport). We
only ever inject the venv into the per-run ``options.env`` (see
``options.build_agent_options``) — ``os.environ`` (the service) is never touched.

Lifecycle: bootstrapped once at pod startup (``app`` lifespan); since ``/workspace``
persists, later wakes just see it already there.
"""

from __future__ import annotations

import os
import subprocess

_logger = None


def _get_logger():
    global _logger
    if _logger is None:
        from priva_common.logging import get_app_logger
        _logger = get_app_logger(__name__)
    return _logger


def _workspace_root() -> str:
    # The operator sets WORKSPACE_DIR=/workspace; fall back for off-cluster dev.
    return os.environ.get("WORKSPACE_DIR") or "/workspace"


def venv_path() -> str:
    return os.path.join(_workspace_root(), ".venv")


def _cache_dir(name: str) -> str:
    return os.path.join(_workspace_root(), ".cache", name)


def _bin_dir() -> str:
    return os.path.join(venv_path(), "bin")


def ensure_user_venv() -> bool:
    """Create the per-account venv on ``/workspace`` if absent. Idempotent and
    fail-soft (a failure just means the agent falls back to the read-only system
    interpreter, where installs fail cleanly rather than silently vanishing).

    Built with ``uv`` (already in the image, fast) and ``--seed`` so the venv ships
    ``pip``/``setuptools`` — the agent can use either ``pip install`` or
    ``uv pip install``. Returns True when the venv is present afterwards.
    """
    if os.path.isdir(_bin_dir()):
        return True
    path = venv_path()
    try:
        os.makedirs(_workspace_root(), exist_ok=True)
        subprocess.run(
            ["uv", "venv", "--seed", path],
            check=True, capture_output=True, timeout=180,
            env={**os.environ, "UV_CACHE_DIR": _cache_dir("uv")},
        )
        _get_logger().info("created per-account venv at {}", path)
        return True
    except Exception as exc:
        # Fall back to the stdlib (slower; needs ensurepip in the base image).
        try:
            import venv as _venv
            _venv.create(path, with_pip=True)
            _get_logger().info("created per-account venv (stdlib) at {}", path)
            return True
        except Exception:
            _get_logger().warning("could not create per-account venv at {}: {}", path, exc)
            return False


def venv_env_overlay(base_env: dict | None = None) -> dict:
    """Env keys to merge into the agent CLI's ``options.env`` so its ``python`` /
    ``pip`` resolve to the ``/workspace`` venv. Returns ``{}`` when the venv isn't
    present (the run proceeds on the system interpreter).

    PATH must be REBUILT, not appended to ``os.environ`` here: ``options.env``
    *replaces* the inherited PATH in the CLI subprocess, so we prepend the venv bin
    to whatever PATH the subprocess would otherwise inherit (the run's own env if it
    set one, else the service's PATH) — keeping ``bash``/``ls``/system tools working.
    """
    bin_dir = _bin_dir()
    if not os.path.isdir(bin_dir):
        return {}
    base_env = base_env or {}
    base_path = base_env.get("PATH") or os.environ.get("PATH", "")
    return {
        "PATH": f"{bin_dir}:{base_path}" if base_path else bin_dir,
        "VIRTUAL_ENV": venv_path(),
        # Caches on the persistent volume → fast re-installs, no writes to the
        # read-only root / ephemeral /tmp layers.
        "UV_CACHE_DIR": _cache_dir("uv"),
        "PIP_CACHE_DIR": _cache_dir("pip"),
    }
