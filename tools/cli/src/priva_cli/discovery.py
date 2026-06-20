"""Service discovery via the ``priva_cloud.services`` entry-point group.

Boundary-safe: imports nothing service-side until a subcommand is dispatched.
"""

from __future__ import annotations

from importlib.metadata import entry_points


def registered() -> dict[str, object]:
    """Return {name: EntryPoint} for every installed priva-cloud service."""
    return {ep.name: ep for ep in entry_points(group="priva_cloud.services")}
