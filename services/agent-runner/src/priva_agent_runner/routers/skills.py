from __future__ import annotations

import io
import os
import tarfile

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse

from priva_common.logging import get_app_logger
from priva_common.models.auth import UserRecord
from priva_common.models.skills import (
    SkillDetailResponse,
    SkillFileResponse,
    SkillListResponse,
    SkillScope,
    SkillUploadResponse,
    SkillsConfigRequest,
    SkillsConfigResponse,
)
from priva_common.audit_log import AuditEntry, get_audit_logger
from ..deps import require_user
from priva_common import skill_exclude as _skill_exclude
from ..services.skills import (
    MAX_UPLOAD_SIZE,
    _validate_skill_name,
    _resolve_skills_dir,
    _safe_resolve,
    delete_skill,
    get_file_content,
    get_skill_detail,
    list_skills,
    upload_skill,
)

logger = get_app_logger(__name__)

router = APIRouter(prefix="/api/sandbox/resource/skills", tags=["skills"])

# The download packs the whole skill dir into memory. Bound it: the directory is
# writable by the agent itself, so it is not covered by the upload limits.
MAX_DOWNLOAD_BYTES = 100 * 1024 * 1024


@router.get("/", response_model=SkillListResponse)
async def list_all_skills(user: UserRecord = Depends(require_user)):
    return list_skills(user.username)


@router.get("/config", response_model=SkillsConfigResponse)
async def get_skills_config(user: UserRecord = Depends(require_user)):
    value = _skill_exclude.get_skill_exclude(user.username)
    return SkillsConfigResponse(skill_exclude=list(value))


@router.put("/config", response_model=SkillsConfigResponse)
async def update_skills_config(
    request: SkillsConfigRequest,
    user: UserRecord = Depends(require_user),
):
    _skill_exclude.save_skill_exclude(user.username, list(request.skill_exclude or []))

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="skills.config_updated",
            target="skill_exclude",
            details={"count": len(request.skill_exclude or [])},
        )
    )

    return SkillsConfigResponse(skill_exclude=list(request.skill_exclude or []))


@router.get("/detail", response_model=SkillDetailResponse)
async def get_skill(
    name: str,
    scope: SkillScope,
    cwd: str | None = None,
    user: UserRecord = Depends(require_user),
):
    return get_skill_detail(scope, cwd, name, user.username)


@router.get("/file", response_model=SkillFileResponse)
async def get_skill_file(
    name: str,
    path: str,
    scope: SkillScope,
    cwd: str | None = None,
    user: UserRecord = Depends(require_user),
):
    return get_file_content(scope, cwd, name, path, user.username)


@router.get("/download")
async def download_skill_endpoint(
    name: str,
    scope: SkillScope,
    cwd: str | None = None,
    user: UserRecord = Depends(require_user),
):
    _validate_skill_name(name)
    skills_dir = _resolve_skills_dir(scope, cwd)
    _safe_resolve(skills_dir, name)
    skill_dir = skills_dir / name
    if not skill_dir.is_dir():
        raise HTTPException(404, "Skill not found")

    # The archive is built in memory, and nothing bounds what the directory
    # holds — the upload caps apply to uploads, but the agent's own Write tool
    # and the user_files upload both write here too. Measure before packing.
    total = 0
    for root, _dirs, files in os.walk(skill_dir):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                continue
    if total > MAX_DOWNLOAD_BYTES:
        raise HTTPException(
            413,
            f"Skill is too large to download ({total // (1024 * 1024)}MB, "
            f"limit {MAX_DOWNLOAD_BYTES // (1024 * 1024)}MB)")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(
            str(skill_dir),
            arcname=name,
            filter=lambda ti: (
                None
                if ti.name.endswith((".pyc", ".DS_Store"))
                or "__pycache__" in ti.name
                else ti
            ),
        )
    buf.seek(0)

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="skill.downloaded",
            target=name,
            details={"scope": scope, "cwd": cwd},
        )
    )

    def iterfile():
        while chunk := buf.read(8192):
            yield chunk

    return StreamingResponse(
        iterfile(),
        media_type="application/gzip",
        headers={"Content-Disposition": f'attachment; filename="{name}.tar.gz"'},
    )


@router.post("/upload", response_model=SkillUploadResponse)
async def upload_skill_endpoint(
    file: UploadFile = File(...),
    scope: SkillScope = Form("personal"),
    cwd: str | None = Form(None),
    user: UserRecord = Depends(require_user),
):
    # upload_skill() enforces MAX_UPLOAD_SIZE, but only after the body is fully
    # materialised — read one byte past it instead.
    file_data = await file.read(MAX_UPLOAD_SIZE + 1)
    if len(file_data) > MAX_UPLOAD_SIZE:
        raise HTTPException(
            413, f"File exceeds {MAX_UPLOAD_SIZE // (1024 * 1024)}MB size limit")
    skill_name, skill_scope, skill_cwd = upload_skill(
        scope, cwd, file_data, file.filename or "upload.zip", user.username
    )

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="skill.uploaded",
            target=skill_name,
            details={"scope": skill_scope, "cwd": skill_cwd},
        )
    )

    return SkillUploadResponse(
        name=skill_name,
        scope=skill_scope,
        cwd=skill_cwd,
        message=f"Skill '{skill_name}' uploaded successfully",
    )


@router.delete("/item")
async def delete_skill_endpoint(
    name: str,
    scope: SkillScope,
    cwd: str | None = None,
    user: UserRecord = Depends(require_user),
):
    delete_skill(scope, cwd, name, user.username)

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="skill.deleted",
            target=name,
            details={"scope": scope, "cwd": cwd},
        )
    )

    return {"message": f"Skill '{name}' deleted successfully"}
