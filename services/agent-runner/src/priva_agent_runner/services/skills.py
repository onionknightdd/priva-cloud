from __future__ import annotations

import io
import os
import re
import shutil
import tarfile
import zipfile
from pathlib import Path

import yaml
from fastapi import HTTPException

from priva_common.logging import get_app_logger
from priva_common.models.skills import (
    FileTreeNode,
    SkillDetailResponse,
    SkillFileResponse,
    SkillGroup,
    SkillLevel,
    SkillListResponse,
    SkillScope,
    SkillSummary,
)
from priva_common.config import get_settings
from priva_common.paths import claude_config_dir

logger = get_app_logger(__name__)

SKILL_NAME_RE = re.compile(r"^[a-z0-9-]+$")
RESERVED_WORDS = {"anthropic", "claude", "system", "admin", "root"}
MAX_UPLOAD_SIZE = 3 * 1024 * 1024  # 3MB — the COMPRESSED upload
# Decompression bounds. The upload cap above says nothing about what an archive
# expands to, which is the whole trick behind a zip bomb.
MAX_ARCHIVE_ENTRIES = 2000
MAX_ENTRY_BYTES = 5 * 1024 * 1024        # one file, uncompressed
MAX_TOTAL_BYTES = 50 * 1024 * 1024       # whole archive, uncompressed
MAX_COMPRESSION_RATIO = 200
MAX_FILE_READ_SIZE = 1 * 1024 * 1024  # 1MB
MAX_NAME_LENGTH = 64
MAX_DESCRIPTION_LENGTH = 1024
XML_TAG_RE = re.compile(r"<[^>]+>")

EXTENSION_LANGUAGE_MAP = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".json": "json",
    ".yaml": "yaml",
    ".yml": "yaml",
    ".md": "markdown",
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",
    ".css": "css",
    ".html": "html",
    ".xml": "xml",
    ".sql": "sql",
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".rb": "ruby",
    ".php": "php",
    ".c": "c",
    ".cpp": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".toml": "toml",
    ".ini": "ini",
    ".conf": "ini",
    ".txt": "plaintext",
    ".env": "bash",
    ".dockerfile": "dockerfile",
    ".r": "r",
    ".lua": "lua",
    ".swift": "swift",
    ".kt": "kotlin",
    ".scala": "scala",
}


def _get_skills_dir(level: SkillLevel, username: str | None = None) -> Path:
    if level == "global":
        # Maps to SDK setting_sources=["user"] → $CLAUDE_CONFIG_DIR/skills/
        # (~/.claude/skills only in local dev; on pods HOME ≠ CLAUDE_CONFIG_DIR
        # and the CLI never reads ~/.claude).
        return claude_config_dir() / "skills"
    settings = get_settings()
    base = os.path.expanduser(settings.server.work_dir)
    if username is None:
        raise HTTPException(400, "Username required for project-level skills")
    # Maps to SDK setting_sources=["project"] → {cwd}/.claude/skills/
    return Path(base) / username / ".claude" / "skills"


# ---------------------------------------------------------------------------
# Per-workdir + personal discovery (the listing/CRUD API)
#
# ``personal`` skills live in ``$CLAUDE_CONFIG_DIR/skills`` (SDK
# setting_sources=["user"]; ~/.claude/skills only in local dev).
# ``workdir`` skills live in ``{cwd}/.claude/skills`` for each of the user's
# project directories — enumerated the same way the sessions endpoint does
# (``list_sessions(directory=None)`` distinct cwds ∪ the default workspace).
# This is independent of the agent-run allowlist (``compute_enabled_skill_names``)
# and the flat-by-name ``skill_exclude`` denylist, which are left unchanged.
# ---------------------------------------------------------------------------

def _personal_skills_dir() -> Path:
    return claude_config_dir() / "skills"


def _default_workspace(username: str) -> str:
    settings = get_settings()
    base = os.path.expanduser(settings.server.work_dir)
    return str(Path(base) / username)


def _list_user_workdirs(username: str) -> list[str]:
    """Distinct session cwds ∪ the user's default workspace, in stable order."""
    cwds: list[str] = []
    seen: set[str] = set()
    default_ws = _default_workspace(username)
    cwds.append(default_ws)
    seen.add(default_ws)
    try:
        from claude_agent_sdk import list_sessions
        for s in list_sessions(directory=None):
            cwd = getattr(s, "cwd", None)
            if cwd and cwd not in seen:
                seen.add(cwd)
                cwds.append(cwd)
    except Exception:
        logger.warning("list_sessions failed during skill workdir enumeration", exc_info=True)
    return cwds


def _resolve_skills_dir(scope: SkillScope, cwd: str | None) -> Path:
    """Map a (scope, cwd) pair to the on-disk ``.claude/skills`` directory."""
    if scope == "personal":
        return _personal_skills_dir()
    if scope == "workdir":
        if not cwd or not os.path.isabs(cwd):
            raise HTTPException(400, "An absolute 'cwd' is required for workdir-scoped skills")
        return Path(cwd) / ".claude" / "skills"
    raise HTTPException(400, f"Unknown skill scope: {scope}")


def _get_skill_exclude(username: str) -> list[str]:
    """Read the skill_exclude denylist (with lazy migration) from .priva.user.yml.

    Delegates to the shared ``priva_common.skill_exclude`` helper. Failures fall
    back to ``[]`` so all discovered skills stay enabled rather than crashing a run.
    """
    from priva_common.skill_exclude import get_skill_exclude
    try:
        value = get_skill_exclude(username)
    except Exception:
        logger.warning("get_skill_exclude failed; defaulting to empty denylist", exc_info=True)
        return []
    return list(value) if isinstance(value, list) else []


def compute_enabled_skill_names(username: str) -> list[str]:
    """Return the list of skill names to pass to ``ClaudeAgentOptions.skills``.

    Enumerates discovered skills (project + global) and removes anything in
    the user's ``skill_exclude`` denylist. Result is an allowlist suitable for
    direct assignment.
    """
    exclude = set(_get_skill_exclude(username))
    seen: set[str] = set()
    enabled: list[str] = []
    for level in ("project", "global"):
        try:
            skills_dir = _get_skills_dir(level, username)
        except HTTPException:
            continue
        if not skills_dir.exists():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir():
                continue
            if not (entry / "SKILL.md").exists():
                continue
            name = entry.name
            if name in seen or name in exclude:
                seen.add(name)
                continue
            seen.add(name)
            enabled.append(name)
    return enabled


def _parse_frontmatter(skill_md_path: Path) -> dict:
    """Extract YAML frontmatter from SKILL.md."""
    try:
        text = skill_md_path.read_text(encoding="utf-8")
    except Exception:
        return {}
    if not text.startswith("---"):
        return {}
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}
    try:
        return yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError:
        return {}


def _count_files(directory: Path) -> int:
    count = 0
    for item in directory.rglob("*"):
        if item.is_file():
            count += 1
    return count


def _build_tree(directory: Path) -> list[FileTreeNode]:
    nodes: list[FileTreeNode] = []
    try:
        entries = sorted(directory.iterdir(), key=lambda p: (p.is_file(), p.name.lower()))
    except PermissionError:
        return nodes
    for entry in entries:
        if entry.is_dir():
            children = _build_tree(entry)
            nodes.append(FileTreeNode(name=entry.name, type="directory", children=children))
        else:
            size = entry.stat().st_size if entry.exists() else 0
            nodes.append(FileTreeNode(name=entry.name, type="file", size=size))
    return nodes


def _detect_binary(data: bytes) -> bool:
    return b"\x00" in data[:8192]


def _detect_language(path: str) -> str | None:
    ext = Path(path).suffix.lower()
    if Path(path).name == "Dockerfile":
        return "dockerfile"
    return EXTENSION_LANGUAGE_MAP.get(ext)


def _validate_skill_name(name: str) -> None:
    if not name:
        raise HTTPException(422, "Skill name is required")
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(422, f"Skill name must be at most {MAX_NAME_LENGTH} characters")
    if not SKILL_NAME_RE.match(name):
        raise HTTPException(422, "Skill name must contain only lowercase letters, numbers, and hyphens")
    if XML_TAG_RE.search(name):
        raise HTTPException(422, "Skill name must not contain XML tags")
    if name in RESERVED_WORDS:
        raise HTTPException(422, f"Skill name '{name}' is reserved")


def _validate_skill_description(desc: str | None) -> None:
    if not desc:
        raise HTTPException(422, "Skill description is required")
    if len(desc) > MAX_DESCRIPTION_LENGTH:
        raise HTTPException(422, f"Skill description must be at most {MAX_DESCRIPTION_LENGTH} characters")
    if XML_TAG_RE.search(desc):
        raise HTTPException(422, "Skill description must not contain XML tags")


def _validate_frontmatter(frontmatter: dict) -> None:
    name = frontmatter.get("name")
    if not isinstance(name, str):
        raise HTTPException(422, "SKILL.md frontmatter must contain a 'name' field (string)")
    _validate_skill_name(name)
    desc = frontmatter.get("description")
    if not isinstance(desc, str):
        raise HTTPException(422, "SKILL.md frontmatter must contain a 'description' field (string)")
    _validate_skill_description(desc)


def _safe_resolve(base: Path, relative: str) -> Path:
    """Resolve path and verify it's inside base directory.

    Containment is a path-component test, not a string-prefix test: with
    ``startswith`` a base of ``…/.claude/skills`` also "contains" its sibling
    ``…/.claude/skills-evil``, so a name like ``../skills-evil/x`` resolved
    outside the intended tree while passing the check.
    """
    resolved = (base / relative).resolve()
    root = base.resolve()
    # The base itself is NOT a valid target. Permitting it meant name="." (or
    # "", "./", "a/..") resolved to the skills ROOT, and delete_skill then
    # rmtree'd every skill the user had — a one-request wipe that the audit log
    # recorded as an ordinary delete of a skill called ".".
    if resolved == root or not resolved.is_relative_to(root):
        raise HTTPException(400, "Path traversal detected")
    return resolved


def _scan_skills_dir(
    skills_dir: Path, scope: SkillScope, cwd: str | None, exclude: set[str]
) -> list[SkillSummary]:
    """List the skills directly under one ``.claude/skills`` directory."""
    out: list[SkillSummary] = []
    if not skills_dir.exists():
        return out
    personal_dir_str = str(_personal_skills_dir().resolve())
    for entry in sorted(skills_dir.iterdir()):
        if not entry.is_dir():
            continue
        # Skip workdir symlinks pointing into ~/.claude/skills — those are
        # listed under the "personal" group instead.
        if scope == "workdir" and entry.is_symlink():
            try:
                target = str(Path(os.readlink(entry)).resolve())
                if target.startswith(personal_dir_str):
                    continue
            except (OSError, ValueError):
                pass
        skill_md = entry / "SKILL.md"
        if not skill_md.exists():
            continue
        fm = _parse_frontmatter(skill_md)
        out.append(
            SkillSummary(
                name=entry.name,
                scope=scope,
                cwd=cwd,
                description=fm.get("description"),
                file_count=_count_files(entry),
                enabled=entry.name not in exclude,
            )
        )
    return out


def list_skills(username: str) -> SkillListResponse:
    """All skills for the user: personal (~/.claude/skills) + one group per
    workdir ({cwd}/.claude/skills). Empty workdir groups are omitted."""
    exclude = set(_get_skill_exclude(username))

    personal = _scan_skills_dir(_personal_skills_dir(), "personal", None, exclude)

    groups: list[SkillGroup] = []
    for cwd in _list_user_workdirs(username):
        skills = _scan_skills_dir(Path(cwd) / ".claude" / "skills", "workdir", cwd, exclude)
        if skills:
            groups.append(SkillGroup(cwd=cwd, skills=skills))

    return SkillListResponse(personal=personal, groups=groups)


def get_skill_detail(scope: SkillScope, cwd: str | None, name: str, username: str) -> SkillDetailResponse:
    # skill_hub validates the name on every entry point; these three never did.
    _validate_skill_name(name)
    skills_dir = _resolve_skills_dir(scope, cwd)
    skill_path = _safe_resolve(skills_dir, name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Skill '{name}' not found")

    skill_md = skill_path / "SKILL.md"
    fm = _parse_frontmatter(skill_md) if skill_md.exists() else {}
    tree = _build_tree(skill_path)
    skill_md_content = None
    if skill_md.exists() and skill_md.is_file():
        raw = skill_md.read_bytes()
        if not _detect_binary(raw):
            skill_md_content = raw.decode("utf-8", errors="replace")

    return SkillDetailResponse(
        name=name,
        scope=scope,
        cwd=cwd,
        description=fm.get("description"),
        frontmatter=fm if fm else None,
        tree=tree,
        base_path=str(skill_path),
        skill_md_content=skill_md_content,
    )


def get_file_content(scope: SkillScope, cwd: str | None, name: str, path: str, username: str) -> SkillFileResponse:
    # skill_hub validates the name on every entry point; these three never did.
    _validate_skill_name(name)
    skills_dir = _resolve_skills_dir(scope, cwd)
    skill_path = _safe_resolve(skills_dir, name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Skill '{name}' not found")

    file_path = _safe_resolve(skill_path, path)
    if not file_path.is_file():
        raise HTTPException(404, f"File '{path}' not found in skill '{name}'")

    size = file_path.stat().st_size
    if size > MAX_FILE_READ_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_FILE_READ_SIZE // (1024*1024)}MB size limit")

    raw = file_path.read_bytes()
    is_binary = _detect_binary(raw)

    return SkillFileResponse(
        path=path,
        content="" if is_binary else raw.decode("utf-8", errors="replace"),
        language=_detect_language(path),
        is_binary=is_binary,
    )


def upload_skill(
    scope: SkillScope, cwd: str | None, file_data: bytes, filename: str, username: str
) -> tuple[str, SkillScope, str | None]:
    # Validate file size
    if len(file_data) > MAX_UPLOAD_SIZE:
        raise HTTPException(413, f"File exceeds {MAX_UPLOAD_SIZE // (1024*1024)}MB size limit")

    # Validate file format and extract
    members, read_file = read_archive(file_data, filename)
    skill_dir_name, fm = validate_bundle(members, read_file)
    skill_name = fm["name"]

    target_dir = _resolve_skills_dir(scope, cwd)
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = target_dir / skill_name

    if dest.exists():  # replace an existing skill of the same name
        shutil.rmtree(dest)
    dest.mkdir(parents=True, exist_ok=True)

    write_bundle(members, read_file, skill_dir_name, dest)
    return skill_name, scope, cwd


# --- shared archive pipeline -------------------------------------------------
# ONE implementation. The Skill Hub used to carry a near-verbatim copy of the
# block above, and when the traversal guards were tightened here the copy kept
# the old `".." in parts` check and the bare `dest / relative` write — so the
# same archive that was rejected on one route still escaped on the other.


def read_archive(file_data: bytes, filename: str) -> tuple[list[str], callable]:
    """Dispatch on the extension. Both extractors enforce the bomb limits."""
    lower_name = filename.lower()
    if lower_name.endswith(".zip") or lower_name.endswith(".skill"):
        return _extract_zip(file_data)
    if lower_name.endswith(".tar.gz") or lower_name.endswith(".tgz"):
        return _extract_tar(file_data, "r:gz")
    if lower_name.endswith(".tar"):
        return _extract_tar(file_data, "r:")
    raise HTTPException(400, "Only .zip, .tar, .tar.gz, and .skill files are accepted")


def validate_bundle(members: list[str], read_file) -> tuple[str, dict]:
    """Validate every entry name, resolve the single top-level dir, and parse the
    SKILL.md frontmatter. Returns ``(top_dir, frontmatter)``."""
    top_dirs = set()
    for m in members:
        _validate_member_name(m)
        parts = m.split("/")
        if parts[0]:
            top_dirs.add(parts[0])

    if len(top_dirs) != 1:
        raise HTTPException(400, "Archive must contain exactly one top-level directory")

    skill_dir_name = top_dirs.pop()
    skill_md_path = f"{skill_dir_name}/SKILL.md"
    if skill_md_path not in members:
        raise HTTPException(400, f"Archive must contain {skill_dir_name}/SKILL.md")

    skill_md_content = read_file(skill_md_path)
    if skill_md_content is None:
        raise HTTPException(400, "Could not read SKILL.md from archive")

    text = skill_md_content.decode("utf-8", errors="replace")
    fm = {}
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            try:
                fm = yaml.safe_load(parts[1]) or {}
            except yaml.YAMLError:
                raise HTTPException(422, "SKILL.md has invalid YAML frontmatter")

    _validate_frontmatter(fm)
    return skill_dir_name, fm


def write_bundle(members: list[str], read_file, skill_dir_name: str, dest: Path) -> None:
    """Write the bundle under ``dest``, every destination path contained.

    _validate_member_name has already rejected the known-bad shapes; resolving
    each write against ``dest`` catches whatever it missed and any symlink
    already on disk that would redirect the write out of the tree.
    """
    for member_path in members:
        if not member_path.startswith(skill_dir_name + "/"):
            continue
        relative = member_path[len(skill_dir_name) + 1 :]
        if not relative or member_path.endswith("/"):
            if relative:
                _safe_resolve(dest, relative).mkdir(parents=True, exist_ok=True)
            continue
        content = read_file(member_path)
        if content is not None:
            file_dest = _safe_resolve(dest, relative)
            file_dest.parent.mkdir(parents=True, exist_ok=True)
            file_dest.write_bytes(content)


def _validate_member_name(name: str) -> None:
    """Reject archive entry names that can escape the extraction directory.

    A bare ``".." in parts`` check is not enough. ``demo//tmp/pwn`` has no ``..``
    and passes a ``startswith("demo/")`` prefix test, but stripping the leading
    ``demo`` leaves ``/tmp/pwn`` — and ``Path("…/demo") / "/tmp/pwn"`` discards
    the base entirely, writing to an absolute path. Empty components are the bug;
    the rest below are the neighbouring shapes.
    """
    if not name or name in (".", "./"):
        raise HTTPException(400, "Archive contains an empty entry name")
    if "\x00" in name:
        raise HTTPException(400, "Archive entry name contains NUL")
    if "\\" in name:
        # Windows-style separators are not split by the POSIX logic below, so a
        # component like `..\..\x` would slip through the traversal check.
        raise HTTPException(400, f"Archive entry name contains a backslash: {name}")
    if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
        raise HTTPException(400, f"Archive contains an absolute path: {name}")
    parts = name.split("/")
    if ".." in parts:
        raise HTTPException(400, "Archive contains path traversal (..)")
    # Trailing "" is just the directory-entry marker ("demo/"); an empty part
    # anywhere else is a doubled separator, i.e. the escape above.
    if "" in parts[:-1]:
        raise HTTPException(400, f"Archive entry name has an empty path component: {name}")


def _guard_declared(sizes: list[int], compressed_len: int) -> None:
    """Reject decompression bombs from the archive index, before reading data.

    MAX_UPLOAD_SIZE bounds only the COMPRESSED upload; deflate reaches ~1000:1,
    so a 3 MB archive within that limit can expand to gigabytes and OOM the pod.
    """
    if len(sizes) > MAX_ARCHIVE_ENTRIES:
        raise HTTPException(400, f"Archive has too many entries (max {MAX_ARCHIVE_ENTRIES})")
    if any(size > MAX_ENTRY_BYTES for size in sizes):
        raise HTTPException(
            400, f"Archive entry exceeds {MAX_ENTRY_BYTES // (1024 * 1024)}MB uncompressed")
    total = sum(sizes)
    if total > MAX_TOTAL_BYTES:
        raise HTTPException(
            400, f"Archive expands to more than {MAX_TOTAL_BYTES // (1024 * 1024)}MB")
    if compressed_len and total > compressed_len * MAX_COMPRESSION_RATIO:
        raise HTTPException(400, "Archive compression ratio looks like a decompression bomb")


def _read_bounded(fh, budget: list[int]) -> bytes:
    """Read one member with a hard cap, decrementing a shared total budget.

    The declared sizes checked above come from attacker-supplied headers and can
    lie, so the actual read is bounded too: ask for one byte more than allowed
    and reject if we get it.
    """
    allowed = min(MAX_ENTRY_BYTES, budget[0])
    chunk = fh.read(allowed + 1)
    if len(chunk) > allowed:
        raise HTTPException(400, "Archive entry is larger than its declared size")
    budget[0] -= len(chunk)
    return chunk


def _extract_zip(data: bytes) -> tuple[list[str], callable]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise HTTPException(400, "Invalid zip file")

    infos = zf.infolist()
    _guard_declared([i.file_size for i in infos], len(data))
    budget = [MAX_TOTAL_BYTES]

    def read_file(path: str) -> bytes | None:
        try:
            with zf.open(path) as fh:
                return _read_bounded(fh, budget)
        except KeyError:
            return None

    return [i.filename for i in infos], read_file


def _extract_tar(data: bytes, mode: str) -> tuple[list[str], callable]:
    try:
        tf = tarfile.open(fileobj=io.BytesIO(data), mode=mode)
        members = tf.getmembers()
    except HTTPException:
        raise
    except Exception:
        raise HTTPException(400, "Invalid tar archive")

    # tar carries link/device/fifo entries that a plain "is it a file?" check
    # would silently skip; refuse them outright so an archive cannot smuggle a
    # symlink into the skill tree.
    for m in members:
        if not (m.isfile() or m.isdir()):
            raise HTTPException(400, f"Archive contains an unsupported entry type: {m.name}")
    _guard_declared([m.size for m in members], len(data))
    budget = [MAX_TOTAL_BYTES]

    def read_file(path: str) -> bytes | None:
        try:
            member = tf.getmember(path)
        except KeyError:
            return None
        fh = tf.extractfile(member)
        return _read_bounded(fh, budget) if fh else None

    return [m.name for m in members], read_file


def delete_skill(scope: SkillScope, cwd: str | None, name: str, username: str) -> None:
    # skill_hub validates the name on every entry point; these three never did.
    _validate_skill_name(name)
    skills_dir = _resolve_skills_dir(scope, cwd)
    skill_path = _safe_resolve(skills_dir, name)
    if not skill_path.is_dir():
        raise HTTPException(404, f"Skill '{name}' not found")

    shutil.rmtree(skill_path)
