from __future__ import annotations

import os
from pathlib import Path


def priva_home() -> Path:
    """Per-deployment state dir: $PRIVA_HOME/priva/.

    PRIVA_HOME is the parent dir (XDG_CONFIG_HOME-style); the app always
    appends 'priva'. Default parent is ~/.config, so default state dir is
    ~/.config/priva/ (matches pre-existing behavior).
    """
    raw = os.environ.get("PRIVA_HOME")
    base = Path(raw).expanduser() if raw else Path.home() / ".config"
    return base / "priva"


def resource_dir(resource_type: str) -> Path:
    """Runtime resource dir: $PRIVA_HOME/priva/resource/<type>/.

    The live source of truth for deployable resources (skills, etc.). On
    startup, resources are seeded here from the source code under
    ``priva/api/bundled/``; thereafter the API reads/writes this location.
    ``resource_type`` is the fixed sub-namespace (e.g. ``"skills"``); add new
    types under the same ``resource/`` root for consistency.
    """
    return priva_home() / "resource" / resource_type
