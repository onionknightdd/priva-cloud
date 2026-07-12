"""Admin Hook Policy API — CRUD over the data-spine `hook_policy` table.

The write path for the admin "Runtime" panel (Agent Runner Sandbox › Runtime).
Rows reach every agent-runner at its next session build (snapshot fetch, ~30s
TTL cache) — nothing here talks to runner pods directly, and data-spine is
never on the hook fire hot path.

Rules enforced here (frozen in docs/hooks-policy-design.md §4):
- create always saves enabled=false (arm explicitly after review)
- hook_type "mcp_tool" is schema-reserved → 422 in v1
- DELETE of a predefined (seeded) row → 409; script ≤ 64 KiB; timeout 1–600
- command scripts get a compile-only syntax check (python3 compile / bash -n)
- every mutation is audited
"""

from __future__ import annotations

import asyncio
import json
import re

from fastapi import APIRouter, Depends, HTTPException

from priva_common.audit_log import AuditEntry, get_audit_logger
from priva_common.dataplane import HookPolicyRecord
from priva_common.hook_seeds import (
    DEFAULT_COMMAND_TIMEOUT,
    DEFAULT_HTTP_TIMEOUT,
    HOOK_TYPES,
    INTERPRETERS,
    MAX_SCRIPT_BYTES,
    SUPPORTED_HOOK_EVENTS,
    seed_by_id,
)
from priva_common.logging import get_app_logger
from priva_common.models.admin import (
    HookPolicyCreate,
    HookPolicyItem,
    HookPolicyListResponse,
    HookPolicySeedResponse,
    HookPolicyUpdate,
    HookPolicyValidateRequest,
    HookPolicyValidateResponse,
    HookPolicyValidationError,
)
from priva_common.user_store import UserRecord

from ..services.auth import require_admin

logger = get_app_logger(__name__)

router = APIRouter(
    prefix="/api/admin/hook-policy",
    tags=["admin-hook-policy"],
    dependencies=[Depends(require_admin)],
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
_ENV_VAR_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# --- validation ---------------------------------------------------------------

async def _check_script_syntax(interpreter: str, script_body: str) -> tuple[str, int | None] | None:
    """Compile-only syntax check. Returns (message, line) on error, None if OK."""
    if interpreter == "python3":
        try:
            compile(script_body, "<hook-script>", "exec")
        except SyntaxError as exc:
            return (f"python syntax error: {exc.msg}", exc.lineno)
        return None
    if interpreter == "bash":
        try:
            proc = await asyncio.create_subprocess_exec(
                "bash", "-n",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(script_body.encode()), timeout=5)
        except FileNotFoundError:
            logger.warning("bash not available for hook syntax check; skipping")
            return None
        except asyncio.TimeoutError:
            return ("bash -n timed out", None)
        if proc.returncode != 0:
            msg = (stderr or b"").decode(errors="replace").strip()
            # "bash: line 3: syntax error ..." — surface the line when present
            m = re.search(r"line (\d+)", msg)
            return (msg or "bash syntax error", int(m.group(1)) if m else None)
        return None
    return None


async def _validate(policy: HookPolicyRecord, *, is_create: bool) -> list[HookPolicyValidationError]:
    errors: list[HookPolicyValidationError] = []

    def err(field: str, message: str, line: int | None = None) -> None:
        errors.append(HookPolicyValidationError(field=field, message=message, line=line))

    if is_create and not _ID_RE.match(policy.id or ""):
        err("id", "id must be a slug: ^[a-z0-9][a-z0-9-]{0,63}$")
    if policy.hook_type not in HOOK_TYPES:
        err("hook_type", f"hook_type must be one of {list(HOOK_TYPES)}")
    if policy.hook_type == "mcp_tool":
        err("hook_type", "mcp_tool hooks are not supported yet")
    if not (policy.name or "").strip():
        err("name", "name is required")
    if not (policy.description or "").strip():
        err("description", "description is required (shown to users)")
    if not policy.events:
        err("events", "at least one event is required")
    else:
        for ev in policy.events:
            if ev not in SUPPORTED_HOOK_EVENTS:
                err("events", f"unsupported event '{ev}' (supported: {list(SUPPORTED_HOOK_EVENTS)})")
    for ev in policy.enforced_events:
        if ev not in policy.events:
            err("enforced_events", f"'{ev}' is not in this hook's events")
    if policy.matcher:
        try:
            re.compile(policy.matcher)
        except re.error as exc:
            err("matcher", f"matcher must be a valid regex: {exc}")
    if not (1 <= policy.timeout_seconds <= 600):
        err("timeout_seconds", "timeout_seconds must be between 1 and 600")
    for var in policy.allowed_env_vars:
        if not _ENV_VAR_RE.match(var):
            err("allowed_env_vars", f"'{var}' is not a valid environment variable name")

    if policy.hook_type == "command":
        if policy.interpreter not in INTERPRETERS:
            err("interpreter", f"interpreter must be one of {list(INTERPRETERS)}")
        if not policy.script_body.strip():
            err("script_body", "script_body is required for command hooks")
        elif len(policy.script_body.encode("utf-8")) > MAX_SCRIPT_BYTES:
            err("script_body", f"script_body exceeds {MAX_SCRIPT_BYTES // 1024} KiB")
        elif policy.interpreter in INTERPRETERS:
            syntax = await _check_script_syntax(policy.interpreter, policy.script_body)
            if syntax:
                err("script_body", syntax[0], syntax[1])
    elif policy.hook_type == "http":
        if not policy.url.startswith(("http://", "https://")):
            err("url", "url must start with http:// or https://")
        if policy.headers_json:
            try:
                headers = json.loads(policy.headers_json)
                if not isinstance(headers, dict) or not all(
                    isinstance(k, str) and isinstance(v, str) for k, v in headers.items()
                ):
                    err("headers_json", "headers_json must be a JSON object of string values")
            except ValueError:
                err("headers_json", "headers_json is not valid JSON")

    return errors


def _default_timeout(hook_type: str) -> int:
    return DEFAULT_HTTP_TIMEOUT if hook_type == "http" else DEFAULT_COMMAND_TIMEOUT


# --- responses ------------------------------------------------------------------

def _to_item(p: HookPolicyRecord) -> HookPolicyItem:
    seed_state: str | None = None
    latest_seed_version: int | None = None
    if p.predefined:
        seed = seed_by_id(p.id)
        if seed is not None:
            latest_seed_version = seed.seed_version
            if p.content_hash == seed.hash:
                seed_state = "current"
            elif seed.seed_version > p.seed_version:
                seed_state = "outdated"  # edited AND a newer seed shipped → diff banner
            else:
                seed_state = "edited"
        else:  # seed removed from the shipped set but the row survives
            seed_state = "edited"
    return HookPolicyItem(
        **p.model_dump(),
        seed_state=seed_state,
        latest_seed_version=latest_seed_version,
    )


def _client():
    from priva_common.dataplane import get_client

    return get_client()


# --- endpoints -------------------------------------------------------------------

@router.get("", response_model=HookPolicyListResponse)
async def list_hook_policies():
    items = [_to_item(p) for p in _client().hook_policies.list()]
    return HookPolicyListResponse(items=items, supported_events=list(SUPPORTED_HOOK_EVENTS))


@router.post("", response_model=HookPolicyItem, status_code=201)
async def create_hook_policy(
    request: HookPolicyCreate,
    current_user: UserRecord = Depends(require_admin),
):
    record = HookPolicyRecord(
        id=request.id.strip(),
        hook_type=request.hook_type,
        name=request.name.strip(),
        description=request.description.strip(),
        events=request.events,
        matcher=request.matcher,
        timeout_seconds=request.timeout_seconds or _default_timeout(request.hook_type),
        interpreter=request.interpreter,
        script_body=request.script_body,
        url=request.url.strip(),
        headers_json=request.headers_json,
        allowed_env_vars=request.allowed_env_vars,
        mcp_server=request.mcp_server,
        mcp_tool=request.mcp_tool,
        enabled=False,  # new rows always arrive disarmed
        enforced=request.enforced,
        default_on=request.default_on,
        target=request.target,
        updated_by=current_user.username,
    )
    errors = await _validate(record, is_create=True)
    if errors:
        raise HTTPException(422, [e.model_dump() for e in errors])
    try:
        saved = _client().hook_policies.upsert(record, expect="create")
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="admin.hook_policy_created",
        target=saved.id,
        details={"hook_type": saved.hook_type, "events": saved.events,
                 "enforced": saved.enforced, "content_hash": saved.content_hash},
    ))
    return _to_item(saved)


@router.put("/{policy_id}", response_model=HookPolicyItem)
async def update_hook_policy(
    policy_id: str,
    request: HookPolicyUpdate,
    current_user: UserRecord = Depends(require_admin),
):
    client = _client()
    existing = client.hook_policies.get(policy_id)
    if existing is None:
        raise HTTPException(404, f"hook policy '{policy_id}' not found")

    provided = request.model_dump(exclude_unset=True)
    if not provided:
        return _to_item(existing)

    merged = existing.model_copy(update=provided)
    merged.updated_by = current_user.username
    errors = await _validate(merged, is_create=False)
    if errors:
        raise HTTPException(422, [e.model_dump() for e in errors])

    mask = list(provided.keys()) + ["updated_by"]
    try:
        saved = client.hook_policies.upsert(merged, update_mask=mask, expect="update")
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="admin.hook_policy_changed",
        target=policy_id,
        details={k: v for k, v in provided.items() if k != "script_body"}
        | ({"content_hash": saved.content_hash} if "script_body" in provided else {}),
    ))
    return _to_item(saved)


@router.delete("/{policy_id}", status_code=204)
async def delete_hook_policy(
    policy_id: str,
    current_user: UserRecord = Depends(require_admin),
):
    try:
        _client().hook_policies.delete(policy_id)
    except LookupError as exc:
        raise HTTPException(404, str(exc))
    except PermissionError as exc:
        raise HTTPException(409, str(exc))
    get_audit_logger().append(AuditEntry(
        actor=current_user.username,
        action="admin.hook_policy_deleted",
        target=policy_id,
    ))


@router.post("/validate", response_model=HookPolicyValidateResponse)
async def validate_hook_policy(request: HookPolicyValidateRequest):
    record = HookPolicyRecord(
        id=request.id or "draft",
        hook_type=request.hook_type,
        name=request.name,
        description=request.description,
        events=request.events,
        matcher=request.matcher,
        timeout_seconds=request.timeout_seconds or _default_timeout(request.hook_type),
        interpreter=request.interpreter,
        script_body=request.script_body,
        url=request.url,
        headers_json=request.headers_json,
        allowed_env_vars=request.allowed_env_vars,
    )
    errors = await _validate(record, is_create=bool(request.id))
    return HookPolicyValidateResponse(valid=not errors, errors=errors)


@router.get("/{policy_id}/seed", response_model=HookPolicySeedResponse)
async def get_hook_policy_seed(policy_id: str):
    """The shipped seed content for a predefined row (the diff banner's right side)."""
    seed = seed_by_id(policy_id)
    if seed is None:
        raise HTTPException(404, f"'{policy_id}' is not a shipped seed")
    return HookPolicySeedResponse(
        id=seed.id,
        seed_version=seed.seed_version,
        name=seed.name,
        description=seed.description,
        events=list(seed.events),
        matcher=seed.matcher,
        interpreter=seed.interpreter,
        script_body=seed.script_body,
        timeout_seconds=seed.timeout_seconds,
        default_on=seed.default_on,
    )
