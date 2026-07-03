from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


# Legacy axis — still used internally by the agent-run skill allowlist
# (``compute_enabled_skill_names`` / ``_get_skills_dir``). The listing/CRUD API
# below uses ``SkillScope`` + ``cwd`` instead.
SkillLevel = Literal["project", "global"]

# New axis for the per-user, per-workdir skills UI: ``personal`` =
# ``~/.claude/skills``; ``workdir`` = ``{cwd}/.claude/skills`` for one of the
# user's project directories.
SkillScope = Literal["personal", "workdir"]


class SkillSummary(BaseModel):
    name: str
    scope: SkillScope
    cwd: str | None = None  # the workdir path; None for personal skills
    description: str | None = None
    file_count: int = 0
    enabled: bool = True


class SkillGroup(BaseModel):
    """All skills discovered under one workdir's ``.claude/skills``."""

    cwd: str
    skills: list[SkillSummary] = Field(default_factory=list)


class SkillListResponse(BaseModel):
    personal: list[SkillSummary] = Field(default_factory=list)
    groups: list[SkillGroup] = Field(default_factory=list)


class FileTreeNode(BaseModel):
    name: str
    type: Literal["file", "directory"]
    children: list["FileTreeNode"] | None = None
    size: int | None = None


class SkillDetailResponse(BaseModel):
    name: str
    scope: SkillScope
    cwd: str | None = None
    description: str | None = None
    frontmatter: dict | None = None
    tree: list[FileTreeNode]
    base_path: str | None = None
    skill_md_content: str | None = None


class SkillFileResponse(BaseModel):
    path: str
    content: str
    language: str | None = None
    is_binary: bool = False


class SkillUploadResponse(BaseModel):
    name: str
    scope: SkillScope
    cwd: str | None = None
    message: str


class SkillsConfigRequest(BaseModel):
    skill_exclude: list[str] = Field(
        default_factory=list,
        description="Skill names to exclude from agent runs (denylist).",
    )


class SkillsConfigResponse(BaseModel):
    skill_exclude: list[str] = Field(default_factory=list)
