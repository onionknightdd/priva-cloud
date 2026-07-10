from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query

from priva_common.logging import get_app_logger
from priva_common.models.auth import UserRecord
from priva_common.models.mcp import (
    McpHeaderItem,
    McpLevel,
    McpServerCapabilities,
    McpServerCreateRequest,
    McpServerDetail,
    McpServerListResponse,
    McpServerSummary,
    McpServerUpdateRequest,
    McpValidateRequest,
    McpValidateResponse,
    McpValidateToolRequest,
    McpValidateToolResponse,
)
from priva_common.audit_log import AuditEntry, get_audit_logger
from ..deps import require_user
from ..services.mcp.config_manager import McpConfigManager
from ..services.mcp.validator import test_mcp_tool, validate_mcp_server

logger = get_app_logger(__name__)

router = APIRouter(prefix="/api/sandbox/resource/mcp", tags=["mcp"])


def _config_to_detail(name: str, config: dict, level: str, cwd: str | None = None) -> McpServerDetail:
    headers_dict = config.get("headers", {})
    headers = [McpHeaderItem(key=k, value=v) for k, v in headers_dict.items()]
    return McpServerDetail(
        name=name,
        type=config.get("type", "http"),
        url=config.get("url", ""),
        level=level,
        cwd=cwd,
        headers=headers,
        timeout=config.get("timeout", 60),
    )


def _config_to_summary(name: str, config: dict, level: str, cwd: str | None = None) -> McpServerSummary:
    return McpServerSummary(
        name=name,
        type=config.get("type", "http"),
        url=config.get("url", ""),
        level=level,
        cwd=cwd,
        header_count=len(config.get("headers", {})),
        timeout=config.get("timeout", 60),
    )


@router.get("/", response_model=McpServerListResponse)
async def list_mcp_servers(user: UserRecord = Depends(require_user)):
    mgr = McpConfigManager(user.username)
    summaries: list[McpServerSummary] = []
    # Project servers grouped per workdir ({cwd}/.mcp.json), then global.
    for cwd, servers in mgr.read_project_groups():
        for name, config in servers.items():
            summaries.append(_config_to_summary(name, config, "project", cwd))
    for name, config in mgr.read_global_servers().items():
        summaries.append(_config_to_summary(name, config, "global", None))
    return McpServerListResponse(servers=summaries)


@router.get("/{level}/{name}", response_model=McpServerDetail)
async def get_mcp_server(
    level: McpLevel,
    name: str,
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    mgr = McpConfigManager(user.username)
    if level == "project":
        servers = mgr.read_project_servers(cwd)
    else:
        servers = mgr.read_global_servers()

    if name not in servers:
        raise HTTPException(404, f"MCP server '{name}' not found at {level} level")
    return _config_to_detail(name, servers[name], level, cwd if level == "project" else None)


@router.get("/{level}/{name}/capabilities", response_model=McpServerCapabilities)
async def get_mcp_server_capabilities(
    level: McpLevel,
    name: str,
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    mgr = McpConfigManager(user.username)
    if level == "project":
        servers = mgr.read_project_servers(cwd)
    else:
        servers = mgr.read_global_servers()

    if name not in servers:
        raise HTTPException(404, f"MCP server '{name}' not found at {level} level")

    config = servers[name]
    result = await validate_mcp_server(
        server_type=config.get("type", "http"),
        url=config.get("url", ""),
        headers=config.get("headers"),
        timeout=config.get("timeout", 30),
    )
    if not result.success:
        raise HTTPException(502, f"Failed to connect to MCP server: {result.error}")

    return McpServerCapabilities(
        tools=result.tools,
        prompts=result.prompts,
        resources=result.resources,
        server_name=result.server_name,
        server_version=result.server_version,
    )


@router.post("/", response_model=McpServerDetail)
async def create_mcp_server(
    request: McpServerCreateRequest,
    user: UserRecord = Depends(require_user),
):
    mgr = McpConfigManager(user.username)
    headers_dict = {h.key: h.value for h in request.headers}
    config = {
        "type": request.type,
        "url": request.url,
        "headers": headers_dict,
        "timeout": request.timeout,
    }

    if request.level == "project":
        mgr.add_project_server(request.cwd, request.name, config)
    else:
        mgr.add_global_server(request.name, config)

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="mcp.created",
            target=request.name,
            details={"level": request.level, "type": request.type, "cwd": request.cwd},
        )
    )

    return _config_to_detail(request.name, config, request.level, request.cwd if request.level == "project" else None)


@router.put("/{level}/{name}", response_model=McpServerDetail)
async def update_mcp_server(
    level: McpLevel,
    name: str,
    request: McpServerUpdateRequest,
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    mgr = McpConfigManager(user.username)
    updates: dict = {}
    if request.type is not None:
        updates["type"] = request.type
    if request.url is not None:
        updates["url"] = request.url
    if request.headers is not None:
        updates["headers"] = {h.key: h.value for h in request.headers}
    if request.timeout is not None:
        updates["timeout"] = request.timeout

    if level == "project":
        updated = mgr.update_project_server(cwd, name, updates)
    else:
        updated = mgr.update_global_server(name, updates)

    if updated is None:
        raise HTTPException(404, f"MCP server '{name}' not found at {level} level")

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="mcp.updated",
            target=name,
            details={"level": level, "cwd": cwd, "fields": list(updates.keys())},
        )
    )

    return _config_to_detail(name, updated, level, cwd if level == "project" else None)


@router.delete("/{level}/{name}")
async def delete_mcp_server(
    level: McpLevel,
    name: str,
    cwd: str | None = Query(None),
    user: UserRecord = Depends(require_user),
):
    mgr = McpConfigManager(user.username)
    if level == "project":
        deleted = mgr.delete_project_server(cwd, name)
    else:
        deleted = mgr.delete_global_server(name)

    if not deleted:
        raise HTTPException(404, f"MCP server '{name}' not found at {level} level")

    audit = get_audit_logger()
    audit.append(
        AuditEntry(
            actor=user.username,
            action="mcp.deleted",
            target=name,
            details={"level": level, "cwd": cwd},
        )
    )

    return {"message": f"MCP server '{name}' deleted successfully"}


@router.post("/validate", response_model=McpValidateResponse)
async def validate_mcp_server_endpoint(
    request: McpValidateRequest,
    user: UserRecord = Depends(require_user),
):
    headers_dict = {h.key: h.value for h in request.headers}
    return await validate_mcp_server(
        server_type=request.type,
        url=request.url,
        headers=headers_dict,
        timeout=request.timeout,
    )


@router.post("/validate/tool", response_model=McpValidateToolResponse)
async def validate_mcp_tool_endpoint(
    request: McpValidateToolRequest,
    user: UserRecord = Depends(require_user),
):
    headers_dict = {h.key: h.value for h in request.headers}
    return await test_mcp_tool(
        server_type=request.type,
        url=request.url,
        headers=headers_dict,
        timeout=request.timeout,
        tool_name=request.tool_name,
        tool_arguments=request.tool_arguments,
    )
