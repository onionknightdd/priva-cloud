from __future__ import annotations

from pydantic import BaseModel

from .skills import FileTreeNode, SkillScope


class HubSkillSummary(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None
    icon_color: str | None = None
    file_count: int = 0
    installed: bool = False


class HubSkillListResponse(BaseModel):
    skills: list[HubSkillSummary]


class HubSkillDetailResponse(BaseModel):
    name: str
    description: str | None = None
    icon: str | None = None
    icon_color: str | None = None
    frontmatter: dict | None = None
    tree: list[FileTreeNode]
    installed: bool = False


class HubDeliverRequest(BaseModel):
    # Install target, mirroring the skill upload/create flow: "personal" →
    # $CLAUDE_CONFIG_DIR/skills, "workdir" → {cwd}/.claude/skills.
    scope: SkillScope = "personal"
    cwd: str | None = None


class HubDeliverResponse(BaseModel):
    name: str
    message: str
