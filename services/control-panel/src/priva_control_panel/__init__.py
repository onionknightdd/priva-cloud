"""Priva Cloud control-panel.

The single front door: auth + admin + config faces + user-data + resource, owns
data-spine (``compose()``), and serves the user SPA at ``/`` and the admin SPA at
``/admin``. Runtime traffic (agent/pty/files) is not served here — agentgateway
routes it to the per-account agent-runner via the InferencePool, steered by this
app's ext_proc EndpointPicker. See ``app.py`` and ``extproc.py``.
"""
