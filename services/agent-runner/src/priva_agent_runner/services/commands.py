"""CRUD for slash-commands (custom commands) at the User and Project scopes
(config-source consistency, item E).

Commands are Markdown files the CLI discovers under ``.claude/commands/``:

- ``user``    -> ``$CLAUDE_CONFIG_DIR/commands/<name>.md``  (every project)
- ``project`` -> ``{cwd}/.claude/commands/<name>.md``       (that workdir)

Frontmatter: ``description``, ``argument-hint``, ``allowed-tools`` (+ optional
``model``); body is the prompt template. Mirrors the sub-agent service so the
scope model, path-safety and validation stay consistent.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml
from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.commands import (
    CommandCreateRequest,
    CommandDetail,
    CommandListResponse,
    CommandSummary,
    CommandUpdateRequest,
)
from priva_common.paths import claude_config_dir
from priva_common.workspace import get_workspace_for_username

logger = get_app_logger(__name__)

COMMAND_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_-]*$")
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 2048
MAX_PROMPT_BYTES = 256 * 1024
VALID_SCOPES = ("user", "project")


def _commands_dir(username: str, scope: str = "project", cwd: str | None = None) -> Path:
    """Resolve (scope, cwd) to its .claude/commands directory (no mkdir).

    - ``user``    -> $CLAUDE_CONFIG_DIR/commands
    - ``project`` -> {cwd}/.claude/commands  (cwd=None -> default workspace)
    """
    if scope == "user":
        return claude_config_dir() / "commands"
    if cwd:
        if not os.path.isabs(cwd):
            raise HTTPException(400, "An absolute 'cwd' is required for project commands")
        base = cwd
    else:
        base = get_workspace_for_username(username)
    return Path(base).expanduser() / ".claude" / "commands"


def _safe_resolve(base: Path, relative: str) -> Path:
    resolved = (base / relative).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise HTTPException(400, "Path traversal detected")
    return resolved


def _validate_scope(scope: str) -> None:
    if scope not in VALID_SCOPES:
        raise HTTPException(422, f"Invalid scope: {scope}")


def _validate_name(name: str) -> None:
    if not name:
        raise HTTPException(422, "Command name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(422, f"Command name must be at most {MAX_NAME_LENGTH} characters")
    if not COMMAND_NAME_RE.match(name):
        raise HTTPException(
            422, "Command name must start alphanumeric and contain only letters, numbers, - and _"
        )


def _validate_full(detail: CommandDetail) -> None:
    _validate_name(detail.name)
    if detail.description and len(detail.description) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(422, f"Description must be at most {MAX_DESCRIPTION_LENGTH} characters")
    if len((detail.prompt or "").encode("utf-8")) > MAX_PROMPT_BYTES:
        raise HTTPException(422, "Command body is too large")


def _normalize_list_value(value: Any) -> list[str]:
    """Accept ``[a, b]`` or ``"a, b"`` CSV form, return a list."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v).strip() for v in value if str(v).strip()]
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return []


def _detail_path(username: str, scope: str, cwd: str | None, name: str) -> Path:
    return _safe_resolve(_commands_dir(username, scope, cwd), f"{name}.md")


def _parse_command_md(path: Path, scope: str, cwd: str | None) -> CommandDetail:
    if not path.exists() or not path.is_file():
        raise HTTPException(404, f"Command file not found: {path.name}")
    text = path.read_text(encoding="utf-8")

    body = text
    fm: dict[str, Any] = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError as exc:
                raise HTTPException(500, f"Invalid YAML frontmatter in {path.name}: {exc}")
            body = parts[2].lstrip("\n")
    if not isinstance(fm, dict):
        fm = {}

    return CommandDetail(
        name=path.stem,
        description=str(fm.get("description") or ""),
        scope=scope,
        cwd=cwd,
        argument_hint=str(fm.get("argument-hint") or fm.get("argument_hint") or ""),
        allowed_tools=_normalize_list_value(fm.get("allowed-tools") or fm.get("allowed_tools")),
        model=fm.get("model"),
        prompt=body,
    )


def _serialize_command_md(detail: CommandDetail) -> str:
    fm: dict[str, Any] = {}
    if detail.description:
        fm["description"] = detail.description
    if detail.argument_hint:
        fm["argument-hint"] = detail.argument_hint
    if detail.allowed_tools:
        # Comma-joined string is what the CLI documents for allowed-tools.
        fm["allowed-tools"] = ", ".join(detail.allowed_tools)
    if detail.model:
        fm["model"] = detail.model

    body = (detail.prompt or "").lstrip("\n")
    if not fm:
        return f"{body}\n" if body else ""
    fm_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True).rstrip()
    return f"---\n{fm_text}\n---\n\n{body}\n"


def _scan_commands_dir(base: Path, scope: str, cwd: str | None) -> list[CommandSummary]:
    items: list[CommandSummary] = []
    if not base.exists():
        return items
    for entry in sorted(base.iterdir()):
        if not entry.is_file() or entry.suffix != ".md":
            continue
        try:
            detail = _parse_command_md(entry, scope, cwd)
        except HTTPException:
            logger.warning("Failed to parse command file: {}", entry)
            continue
        items.append(
            CommandSummary(
                name=detail.name,
                description=detail.description,
                scope=scope,
                cwd=cwd,
            )
        )
    return items


def list_commands(username: str) -> CommandListResponse:
    """All commands: user scope + one group per project workdir. Flat list; each
    item carries scope+cwd (mirrors the subagents list)."""
    from .mcp.config_manager import list_user_workdirs

    items: list[CommandSummary] = []
    items.extend(_scan_commands_dir(_commands_dir(username, "user", None), "user", None))
    for cwd in list_user_workdirs(username):
        items.extend(_scan_commands_dir(_commands_dir(username, "project", cwd), "project", cwd))
    return CommandListResponse(commands=items)


def get_command(username: str, scope: str, cwd: str | None, name: str) -> CommandDetail:
    _validate_scope(scope)
    _validate_name(name)
    path = _detail_path(username, scope, cwd, name)
    if not path.exists():
        raise HTTPException(404, f"Command '{name}' not found")
    return _parse_command_md(path, scope, cwd)


def create_command(username: str, req: CommandCreateRequest) -> CommandDetail:
    _validate_scope(req.scope)
    detail = CommandDetail(
        name=req.name,
        description=req.description or "",
        scope=req.scope,
        cwd=req.cwd,
        argument_hint=req.argument_hint or "",
        allowed_tools=req.allowed_tools or [],
        model=req.model,
        prompt=req.prompt or "",
    )
    _validate_full(detail)

    path = _detail_path(username, req.scope, req.cwd, detail.name)
    if path.exists():
        raise HTTPException(409, f"Command '{detail.name}' already exists")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_serialize_command_md(detail), encoding="utf-8")
    return detail


def update_command(
    username: str, scope: str, cwd: str | None, name: str, req: CommandUpdateRequest
) -> CommandDetail:
    _validate_scope(scope)
    _validate_name(name)
    existing = get_command(username, scope, cwd, name)

    new_name = req.new_name or existing.name
    if new_name != existing.name:
        _validate_name(new_name)

    merged = CommandDetail(
        name=new_name,
        description=req.description if req.description is not None else existing.description,
        scope=scope,
        cwd=cwd,
        argument_hint=req.argument_hint if req.argument_hint is not None else existing.argument_hint,
        allowed_tools=req.allowed_tools if req.allowed_tools is not None else existing.allowed_tools,
        model=req.model if req.model is not None else existing.model,
        prompt=req.prompt if req.prompt is not None else existing.prompt,
    )
    _validate_full(merged)

    old_path = _detail_path(username, scope, cwd, name)
    new_path = _detail_path(username, scope, cwd, new_name)
    if new_name != existing.name:
        if new_path.exists():
            raise HTTPException(409, f"Command '{new_name}' already exists")
        os.rename(old_path, new_path)
    new_path.write_text(_serialize_command_md(merged), encoding="utf-8")
    return merged


def delete_command(username: str, scope: str, cwd: str | None, name: str) -> None:
    _validate_scope(scope)
    _validate_name(name)
    path = _detail_path(username, scope, cwd, name)
    if not path.exists():
        raise HTTPException(404, f"Command '{name}' not found")
    path.unlink()
