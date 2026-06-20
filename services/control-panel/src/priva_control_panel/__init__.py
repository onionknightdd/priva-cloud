"""Priva Cloud control-panel.

The single front door in dev mode A (code-split §13): auth + admin + config
faces + user-data + resource, owns data-spine (``compose()``), serves the user
SPA at ``/`` and the admin SPA at ``/admin``, and reverse-proxies the runtime
(agent/pty/files + the agent-coupled routers) to agent-runner, injecting a
signed runner token. See ``app.py`` and ``proxy.py``.
"""
