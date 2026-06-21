"""control-panel launcher entry-point (``priva-cloud control-panel`` / ``python -m``)."""

from __future__ import annotations

import argparse
import os


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="priva-cloud control-panel")
    parser.add_argument("--host", default=os.environ.get("CONTROL_PANEL_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("CONTROL_PANEL_PORT", "8080")))
    parser.add_argument("--config", default=os.environ.get("PRIVA_CONFIG_FILE"))
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args(argv)

    if args.config:
        os.environ["PRIVA_CONFIG_FILE"] = os.path.abspath(os.path.expanduser(args.config))

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
