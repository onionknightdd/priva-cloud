"""Skill Hub service — browse, preview, and install bundled skills on demand."""

from __future__ import annotations

import shutil
from pathlib import Path

from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.skill_hub import (
    HubDeliverResponse,
    HubSkillDetailResponse,
    HubSkillListResponse,
    HubSkillSummary,
)
from priva_common.models.skills import SkillFileResponse, SkillScope
from priva_common.paths import resource_dir
from .skills import (
    MAX_FILE_READ_SIZE,
    MAX_UPLOAD_SIZE,
    _build_tree,
    _count_files,
    _detect_binary,
    _detect_language,
    _list_user_workdirs,
    _parse_frontmatter,
    _personal_skills_dir,
    _resolve_skills_dir,
    _safe_resolve,
    _validate_skill_name,
    read_archive,
    validate_bundle,
    write_bundle,
)

logger = get_app_logger(__name__)

# Source-code seed: shipped in the package, copied into the runtime dir on
# startup. Not read by the API at runtime — see _runtime_skills_dir().
_SOURCE_SKILLS_DIR = Path(__file__).parent.parent / "bundled" / "skills"

# Files/dirs to skip when copying bundled resources
_IGNORE = shutil.ignore_patterns(".DS_Store", "__pycache__", "*.pyc")


def _runtime_skills_dir() -> Path:
    """Live source of truth: $PRIVA_HOME/priva/resource/skills/.

    Seeded from _SOURCE_SKILLS_DIR on startup; the catalog, admin lifecycle,
    and on-demand delivery all read/write here.
    """
    return resource_dir("skills")


def seed_bundled_skills() -> None:
    """Seed the runtime skills dir from the source-code seed on startup.

    Per-skill delete-and-rewrite: each skill under _SOURCE_SKILLS_DIR replaces
    its runtime counterpart. Skills present only in the runtime dir (e.g.
    admin-uploaded) are left untouched.
    """
    if not _SOURCE_SKILLS_DIR.is_dir():
        logger.warning("Source skills seed dir not found: {}", _SOURCE_SKILLS_DIR)
        return

    runtime_dir = _runtime_skills_dir()
    runtime_dir.mkdir(parents=True, exist_ok=True)

    seeded = 0
    for skill_dir in sorted(_SOURCE_SKILLS_DIR.iterdir()):
        if not skill_dir.is_dir():
            continue
        dest = runtime_dir / skill_dir.name
        try:
            if dest.exists():
                shutil.rmtree(dest)
            shutil.copytree(str(skill_dir), str(dest), ignore=_IGNORE)
            seeded += 1
        except Exception as exc:
            logger.warning("Failed to seed bundled skill '{}': {}", skill_dir.name, exc)

    logger.info("Seeded {} bundled skill(s) into {}", seeded, runtime_dir)


def _skill_search_dirs(username: str) -> list[Path]:
    """Every ``.claude/skills`` dir an installed copy could live in: the personal
    (global) dir plus each of the user's project workdirs. Mirrors the targets the
    install/create picker offers, so "installed" means "installed anywhere the user
    could have put it"."""
    dirs = [_personal_skills_dir()]
    for cwd in _list_user_workdirs(username):
        dirs.append(Path(cwd) / ".claude" / "skills")
    return dirs


def _is_installed(name: str, username: str, search_dirs: list[Path] | None = None) -> bool:
    dirs = search_dirs if search_dirs is not None else _skill_search_dirs(username)
    for base in dirs:
        dest = base / name
        if dest.is_dir() and not dest.is_symlink():
            return True
    return False


def list_hub_skills(username: str) -> HubSkillListResponse:
    skills: list[HubSkillSummary] = []
    if not _runtime_skills_dir().is_dir():
        return HubSkillListResponse(skills=skills)

    # Enumerate the user's install targets once, not per skill.
    search_dirs = _skill_search_dirs(username)
    for entry in sorted(_runtime_skills_dir().iterdir()):
        if not entry.is_dir():
            continue
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        fm = _parse_frontmatter(skill_md)
        meta = fm.get("metadata") or {}
        skills.append(
            HubSkillSummary(
                name=entry.name,
                description=fm.get("description"),
                icon=meta.get("icon"),
                icon_color=meta.get("icon_color"),
                file_count=_count_files(entry),
                installed=_is_installed(entry.name, username, search_dirs),
            )
        )

    return HubSkillListResponse(skills=skills)


def get_hub_skill_detail(name: str, username: str) -> HubSkillDetailResponse:
    _validate_skill_name(name)
    skill_path = _safe_resolve(_runtime_skills_dir(), name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Bundled skill '{name}' not found")

    skill_md = skill_path / "SKILL.md"
    fm = _parse_frontmatter(skill_md) if skill_md.exists() else {}
    meta = fm.get("metadata") or {}
    tree = _build_tree(skill_path)

    return HubSkillDetailResponse(
        name=name,
        description=fm.get("description"),
        icon=meta.get("icon"),
        icon_color=meta.get("icon_color"),
        frontmatter=fm if fm else None,
        tree=tree,
        installed=_is_installed(name, username),
    )


def get_hub_skill_file(name: str, path: str) -> SkillFileResponse:
    _validate_skill_name(name)
    skill_path = _safe_resolve(_runtime_skills_dir(), name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Bundled skill '{name}' not found")

    file_path = _safe_resolve(skill_path, path)
    if not file_path.is_file():
        raise HTTPException(404, f"File '{path}' not found in bundled skill '{name}'")

    size = file_path.stat().st_size
    if size > MAX_FILE_READ_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_READ_SIZE // (1024 * 1024)}MB size limit")

    raw = file_path.read_bytes()
    is_binary = _detect_binary(raw)

    return SkillFileResponse(
        path=path,
        content="" if is_binary else raw.decode("utf-8", errors="replace"),
        language=_detect_language(path),
        is_binary=is_binary,
    )


def deliver_hub_skill(
    name: str,
    username: str,
    scope: SkillScope = "personal",
    cwd: str | None = None,
) -> HubDeliverResponse:
    _validate_skill_name(name)
    source = _safe_resolve(_runtime_skills_dir(), name)
    if not source.is_dir():
        raise HTTPException(404, f"Bundled skill '{name}' not found")

    # Resolve the target the same way the upload/create flow does: personal →
    # $CLAUDE_CONFIG_DIR/skills, workdir → {cwd}/.claude/skills.
    dest = _resolve_skills_dir(scope, cwd) / name

    # Remove existing if present (overwrite)
    if dest.exists():
        shutil.rmtree(dest)

    dest.mkdir(parents=True, exist_ok=True)
    shutil.copytree(str(source), str(dest), dirs_exist_ok=True, ignore=_IGNORE)
    logger.info(
        "Delivered bundled skill '{}' to user '{}' (scope={}, cwd={})",
        name, username, scope, cwd,
    )

    return HubDeliverResponse(
        name=name,
        message=f"Skill '{name}' installed successfully",
    )


def upload_hub_skill(file_data: bytes, filename: str) -> HubDeliverResponse:
    if len(file_data) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_SIZE // (1024 * 1024)}MB size limit")

    # Shared pipeline — this route used to carry its own copy of the parse and
    # extract logic, which kept the weaker `".." in parts` check and a bare
    # `dest / relative` write. An entry like `demo//tmp/pwn` has no `..`, passes
    # the prefix test, and resolves to an ABSOLUTE path that discards `dest`.
    members, read_file = read_archive(file_data, filename)
    skill_dir_name, fm = validate_bundle(members, read_file)
    skill_name = fm["name"]

    dest = _runtime_skills_dir() / skill_name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    write_bundle(members, read_file, skill_dir_name, dest)

    logger.info("Uploaded bundled skill '{}' to hub", skill_name)

    return HubDeliverResponse(
        name=skill_name,
        message=f"Bundled skill '{skill_name}' uploaded successfully",
    )


def delete_hub_skill(name: str) -> None:
    _validate_skill_name(name)
    skill_path = _safe_resolve(_runtime_skills_dir(), name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Bundled skill '{name}' not found")

    shutil.rmtree(skill_path)
    logger.info("Deleted bundled skill '{}' from hub", name)
