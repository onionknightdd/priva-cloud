"""priva-cloud entry. Lazy dispatch: ``serve`` supervises all; any other
subcommand loads that service's entry-point and hands off the rest of argv."""

from __future__ import annotations

import sys

from .discovery import registered


def _usage() -> str:
    svc = ", ".join(sorted(registered())) or "(none installed)"
    return (
        "usage: priva-cloud <command> [args...]\n\n"
        "  serve [--only a,b] [args...]   supervise all discovered services\n"
        f"  <service> [args...]            run one service directly\n\n"
        f"installed services: {svc}\n"
    )


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(_usage())
        return 0

    sub, rest = argv[0], argv[1:]

    if sub == "serve":
        from . import serve
        return serve.run(rest)

    ep = registered().get(sub)
    if ep is None:
        print(f"priva-cloud: '{sub}' is not an installed service\n", file=sys.stderr)
        print(_usage(), file=sys.stderr)
        return 2
    func = ep.load()
    result = func(rest)
    return int(result or 0)


if __name__ == "__main__":
    raise SystemExit(main())
