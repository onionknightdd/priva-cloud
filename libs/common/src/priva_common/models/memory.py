"""Memory (CLAUDE.md) editor models.

Claude Code loads memory from ``CLAUDE.md`` at two scopes the platform exposes:

- ``user``    -> ``$CLAUDE_CONFIG_DIR/CLAUDE.md``  (applies to every run)
- ``project`` -> ``{cwd}/CLAUDE.md``               (that workdir; project root,
                                                    NOT under .claude/)

Both are read natively by the CLI in SDK runs AND terminal sessions.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MemoryScope = Literal["user", "project"]


class MemoryScopeInfo(BaseModel):
    scope: MemoryScope
    cwd: str | None = None
    path: str
    exists: bool
    size: int = 0


class MemoryListResponse(BaseModel):
    scopes: list[MemoryScopeInfo]


class MemoryContent(BaseModel):
    scope: MemoryScope
    cwd: str | None = None
    path: str
    content: str
    exists: bool


class MemoryUpdateRequest(BaseModel):
    content: str


# --- Auto memory (Claude-written) -------------------------------------------
#
# A second, distinct memory system: unlike CLAUDE.md (user-authored), *auto
# memory* is written by Claude Code itself (CLI >= 2.1.59, on by default) as it
# works — build commands, debugging insights, preferences it discovers. It lives
# beside the session transcripts at
#
#     $CLAUDE_CONFIG_DIR/projects/<sanitized-cwd>/memory/
#
# as a ``MEMORY.md`` index (first 200 lines / 25 KB loaded every session) plus
# optional topic files loaded on demand. It is inherently per-project (per cwd),
# so there is no "user" scope here. The platform only *curates* it (browse /
# edit / delete) and toggles it per project (``autoMemoryEnabled`` in
# ``{cwd}/.claude/settings.json``) — it never authors files, so no "create".


class AutoMemoryFile(BaseModel):
    name: str                      # e.g. "MEMORY.md", "debugging.md"
    path: str                      # absolute
    size: int = 0
    is_index: bool = False         # name == "MEMORY.md" (the always-loaded index)


class AutoMemoryProject(BaseModel):
    cwd: str
    label: str                     # short label (last path segment of cwd)
    memory_dir: str                # absolute path to the memory/ dir
    enabled: bool = True           # autoMemoryEnabled at this project scope (default on)
    exists: bool = False           # the memory/ dir exists on disk
    files: list[AutoMemoryFile] = Field(default_factory=list)


class AutoMemoryListResponse(BaseModel):
    projects: list[AutoMemoryProject]


class AutoMemoryContent(BaseModel):
    cwd: str
    name: str
    path: str
    content: str
    exists: bool


class AutoMemoryEnabledRequest(BaseModel):
    enabled: bool
