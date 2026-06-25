"""agent-runner launcher entry-point (``priva-cloud agent-runner`` / ``python -m``).

Pins the single-account process env *before* importing the app/config, then runs
uvicorn. Account pinning (§E): ACCOUNT_ID + USERNAME identify the one account;
optional CONFIG_HOME / WORKSPACE_DIR / PRIVA_HOME / CLAUDE_CONFIG_DIR override the
shared-default paths (when unset, AR and control-panel share the same
``~/priva_workspace`` + ``~/.config/priva`` by running under one user/env).
"""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="priva-cloud agent-runner")
    parser.add_argument("--host", default=os.environ.get("AGENT_RUNNER_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("AGENT_RUNNER_PORT", "8091")))
    parser.add_argument("--account-id", default=os.environ.get("ACCOUNT_ID"))
    parser.add_argument("--username", default=os.environ.get("USERNAME"))
    parser.add_argument("--config", default=os.environ.get("PRIVA_CONFIG_FILE"))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    # --- Single-account env pin (must precede priva_common.config import) ---
    if args.config:
        os.environ["PRIVA_CONFIG_FILE"] = os.path.abspath(os.path.expanduser(args.config))
    if args.account_id:
        os.environ["ACCOUNT_ID"] = args.account_id
    if args.username:
        os.environ["USERNAME"] = args.username

    # Optional explicit workspace/state overrides (else shared defaults apply).
    workspace = os.environ.get("WORKSPACE_DIR")
    if workspace:
        os.environ.setdefault("PRIVA_HOME", os.environ.get("CONFIG_HOME", workspace))
    if not os.environ.get("CLAUDE_CONFIG_DIR") and os.environ.get("CONFIG_HOME"):
        os.environ["CLAUDE_CONFIG_DIR"] = os.environ["CONFIG_HOME"]

    # Non-root + readOnlyRootFilesystem: HOME must point at a writable path on the
    # per-account volume (the baked /home/sandbox is on the read-only root fs). The
    # operator sets HOME=/workspace/.home; ensure it exists. node/claude write here.
    if workspace:
        os.environ.setdefault("HOME", os.path.join(workspace, ".home"))
    home = os.environ.get("HOME")
    if home:
        try:
            os.makedirs(home, exist_ok=True)
        except OSError:
            pass

    if not os.environ.get("ACCOUNT_ID"):
        parser.error("agent-runner requires --account-id (or ACCOUNT_ID env) to pin its account")

    import uvicorn

    uvicorn.run(
        "priva_agent_runner.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
