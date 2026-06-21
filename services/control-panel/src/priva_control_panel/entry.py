"""control-panel launcher entry-point (``priva-cloud control-panel`` / ``python -m``).

CLI flags are *additive overrides* over the environment: each provided flag sets the
matching env var **before** the app is imported, so the pydantic ``Settings`` model (and
``app.py``'s direct ``os.environ`` reads) pick them up. This keeps the k8s path intact —
deployments still inject everything via ConfigMap/Secret env; flags are for local/standalone
runs. Secrets (jwt_secret, global_api_key, hmac) are intentionally NOT exposed as flags.
"""

from __future__ import annotations

import argparse
import os


def _set_env(name: str, value: str | None) -> None:
    """Set an env var only when a value was actually provided on the CLI."""
    if value is not None:
        os.environ[name] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="priva-cloud control-panel")

    # --- process / serving ---
    parser.add_argument("--host", default=os.environ.get("CONTROL_PANEL_HOST", "0.0.0.0"),
                        help="HTTP bind host (env: CONTROL_PANEL_HOST)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CONTROL_PANEL_PORT", "8080")),
                        help="HTTP bind port (env: CONTROL_PANEL_PORT)")
    parser.add_argument("--config", default=os.environ.get("PRIVA_CONFIG_FILE"),
                        help="path to config.yaml (env: PRIVA_CONFIG_FILE)")
    parser.add_argument("--reload", action="store_true", help="uvicorn autoreload (dev)")

    # --- operational overrides (each maps to an env var consumed by Settings/app.py) ---
    parser.add_argument("--web-dist", default=None,
                        help="user SPA dist dir (env: PRIVA_WEB_DIST)")
    parser.add_argument("--web-dist-admin", default=None,
                        help="admin SPA dist dir (env: PRIVA_WEB_DIST_ADMIN)")
    parser.add_argument("--extproc-port", default=None,
                        help="gRPC ext_proc (EPP) listener port (env: PRIVA_EDGE__EXTPROC_PORT)")
    parser.add_argument("--work-dir", default=None,
                        help="server work dir (env: PRIVA_SERVER__WORK_DIR)")
    parser.add_argument("--log-dir", default=None,
                        help="base dir for relative log paths (env: PRIVA_LOG_DIR)")
    parser.add_argument("--dataspine-transport", default=None, choices=["in_process", "grpc"],
                        help="data-plane transport (env: PRIVA_DATASPINE__TRANSPORT)")
    parser.add_argument("--dataspine-grpc-dsn", default=None,
                        help="data-spine host:port when transport=grpc (env: PRIVA_DATASPINE__GRPC_DSN)")
    parser.add_argument("--kubeconfig", default=None,
                        help="kubeconfig path for off-cluster mode (env: PRIVA_KUBERNETES__KUBECONFIG)")
    incluster = parser.add_mutually_exclusive_group()
    incluster.add_argument("--in-cluster", dest="in_cluster", action="store_const", const="true",
                           default=None, help="use in-cluster k8s ServiceAccount (env: PRIVA_KUBERNETES__IN_CLUSTER)")
    incluster.add_argument("--no-in-cluster", dest="in_cluster", action="store_const", const="false",
                           help="use kubeconfig instead of in-cluster auth")

    args = parser.parse_args(argv)

    # --config is special: app.py / Settings resolve it from PRIVA_CONFIG_FILE, and we
    # absolutize it so child processes and relative log paths resolve consistently.
    if args.config:
        os.environ["PRIVA_CONFIG_FILE"] = os.path.abspath(os.path.expanduser(args.config))

    # Map operational flags -> env (additive; only when explicitly provided).
    _set_env("PRIVA_WEB_DIST", args.web_dist)
    _set_env("PRIVA_WEB_DIST_ADMIN", args.web_dist_admin)
    _set_env("PRIVA_EDGE__EXTPROC_PORT", args.extproc_port)
    _set_env("PRIVA_SERVER__WORK_DIR", args.work_dir)
    _set_env("PRIVA_LOG_DIR", args.log_dir)
    _set_env("PRIVA_DATASPINE__TRANSPORT", args.dataspine_transport)
    _set_env("PRIVA_DATASPINE__GRPC_DSN", args.dataspine_grpc_dsn)
    _set_env("PRIVA_KUBERNETES__KUBECONFIG", args.kubeconfig)
    _set_env("PRIVA_KUBERNETES__IN_CLUSTER", args.in_cluster)

    import uvicorn

    uvicorn.run(
        "priva_control_panel.app:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        ws_ping_interval=None,
        ws_ping_timeout=None,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
