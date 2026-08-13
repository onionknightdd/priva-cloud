from __future__ import annotations

import os
import shutil
import stat
from typing import Any, Literal

from claude_agent_sdk import ClaudeAgentOptions
from claude_agent_sdk.types import (
    HookMatcher,
    PermissionResultAllow,
    PermissionResultDeny,
    SyncHookJSONOutput,
)
from fastapi import HTTPException

from priva_common.models.agent import McpServersSelection, PermissionMode, RunMode
from priva_common.models.llm_profiles import LlmProfile
from priva_common.user_env import read_settings_env
from ..llm_profiles import open_profile_settings_overlay, resolve_model
from priva_common.workspace import get_workspace_for_username
from priva_common.runtime_settings import read_runtime_settings
from .system_prompt import build_run_system_prompt

_logger = None


def _get_logger():
    global _logger
    if _logger is None:
        from priva_common.logging import get_app_logger
        _logger = get_app_logger(__name__)
    return _logger


_memfd_cache: int | None = None  # keep fd open for process lifetime (Linux)


BUILTIN_DISALLOWED_TOOLS = [
    "CronCreate",
    "CronDelete",
    "CronList",
    "EnterWorktree",
    "ExitWorktree",
    "NotebookEdit",
    "RemoteTrigger",
    "PushNotification",
    "DesignSync",
    "ScheduleWakeup",
    "ReportFindings",
    "WebFetch",
    "WebSearch",
]


def _ensure_executable(cli_path: str) -> str:
    """Resolve symlinks, chmod +x, and wrap Node.js scripts without shebang.

    Uses Linux memfd_create for the wrapper — no file left on disk.
    """
    real_path = os.path.realpath(cli_path)
    if not os.path.isfile(real_path):
        return cli_path

    # Try chmod +x
    if not os.access(real_path, os.X_OK):
        try:
            os.chmod(real_path, os.stat(real_path).st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
            _get_logger().info("Added execute permission to: {}", real_path)
        except OSError:
            pass

    # Check for shebang or native binary magic bytes
    try:
        with open(real_path, "rb") as f:
            magic = f.read(4)
    except OSError:
        return cli_path

    if magic[:2] == b"#!":
        return cli_path

    # Native binaries (ELF, Mach-O) are directly executable — no wrapper needed
    _native_magics = {
        b"\x7fELF",          # ELF (Linux)
        b"\xcf\xfa\xed\xfe", # Mach-O 64-bit
        b"\xce\xfa\xed\xfe", # Mach-O 32-bit
        b"\xca\xfe\xba\xbe", # Mach-O fat/universal
    }
    if magic in _native_magics:
        return cli_path

    # Non-native script without shebang — need a wrapper to exec via node
    node_path = shutil.which("node")
    if not node_path:
        _get_logger().warning("No shebang in {} and node not found in PATH", real_path)
        return cli_path

    global _memfd_cache
    if _memfd_cache is not None:
        return f"/proc/self/fd/{_memfd_cache}"

    wrapper = f"#!/bin/sh\nexec {node_path} {real_path} \"$@\"\n".encode()
    try:
        import ctypes
        import ctypes.util
        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        fd = libc.memfd_create(b"claude-wrapper", 0)
        if fd < 0:
            raise OSError("memfd_create failed")
        os.write(fd, wrapper)
        os.fchmod(fd, 0o755)
        _memfd_cache = fd
        _get_logger().info("In-memory CLI wrapper (fd={}) -> node {}", fd, real_path)
        return f"/proc/self/fd/{fd}"
    except Exception:
        _get_logger().warning("memfd_create unavailable, falling back to direct path")
        return cli_path


async def _noop_pre_tool_hook(input_data: Any, tool_use_id: str, context: Any) -> SyncHookJSONOutput:
    """Required by the SDK when can_use_tool is configured."""
    return SyncHookJSONOutput(continue_=True)


async def _auto_approve_tool(
    tool_name: str, tool_input: dict[str, Any], context: Any
) -> PermissionResultAllow | PermissionResultDeny:
    # Even in bypassPermissions mode the CLI consults the callback when (a) a
    # PreToolUse hook returned permissionDecision "ask" (the admin-managed
    # hook lane — the hook's reason arrives as context.decision_reason), or
    # (b) it is writing under .claude/{skills,commands,agents}/** (built-in
    # protection in the Claude Code CLI — see wf5 in the bundled binary).
    # Without a can_use_tool callback the SDK raises "canUseTool callback is
    # not provided" and the tool call fails. This fallback serves runs with
    # no PermissionCoordinator (nobody to ask): hook asks fail closed, the
    # built-in-protection writes stay auto-approved.
    reason = getattr(context, "decision_reason", None)
    if reason:
        return PermissionResultDeny(
            message=f"需要用户确认但当前运行无法交互,已拒绝:{reason}"
        )
    return PermissionResultAllow(updated_input=None)


async def build_agent_options(
    session_id: str | None = None,
    permission_mode: PermissionMode | None = None,
    *,
    can_use_tool: Any = None,
    cwd: str | None = None,
    add_dirs: list[str] | None = None,
    username: str | None = None,
    model_override: str | None = None,
    run_mode: RunMode = "agent",
    auth_method: Literal["jwt", "api_key", "anonymous"] = "jwt",
    mcp_servers: McpServersSelection = "auto",
    inject_scheduler_tools: bool = False,  # deferred (Phase 4); kept for signature compat
    enable_file_checkpointing: bool = False,
    fork_session: bool = False,
    extra_allowed_tools: list[str] | None = None,
    inject_openclaw_tools: bool = False,
    enable_permission_feedback: bool = False,
    enable_prompt_suggestions: bool = False,
    max_turns: int | None = None,
    extra_disallowed_tools: list[str] | None = None,
    include_partial_messages: bool = False,
    vision_image_paths: list[str] | None = None,
    enable_cross_session_interaction: bool | None = None,
) -> ClaudeAgentOptions:
    if run_mode not in {"agent", "code"}:
        raise HTTPException(422, f"Invalid run_mode: {run_mode!r}")
    valid_mcp_selection = (
        mcp_servers is None
        or mcp_servers == "auto"
        or mcp_servers == "disable"
        or (
            isinstance(mcp_servers, list)
            and all(isinstance(name, str) for name in mcp_servers)
        )
    )
    if not valid_mcp_selection:
        raise HTTPException(422, "mcp_servers must be 'auto', 'disable', a string array, or null")

    if username is None:
        raise HTTPException(400, "Authentication required for agent runs")

    if cwd is None:
        # Fall back to the caller's own workspace (<work_dir>/<username>) — the
        # same default every HTTP/WS entry point resolves. Bare work_dir would
        # land one level ABOVE every project dir (and its .claude/ scope).
        cwd = get_workspace_for_username(username)
    os.makedirs(cwd, exist_ok=True)

    # Resolve the request's optional ``profile:model`` reference before creating
    # the SDK options.  The selected profile is snapshotted for this run, so a
    # Settings change cannot switch credentials halfway through a retry/stream.
    try:
        resolved = resolve_model(model_override)
    except HTTPException:
        # Unit/in-process callers can invoke the builder before the app
        # lifespan has performed migration.  Read the legacy env only as a
        # one-time migration fallback; production runs have a profile store.
        legacy = read_settings_env()
        if not legacy.get("ANTHROPIC_BASE_URL") or not legacy.get("ANTHROPIC_AUTH_TOKEN"):
            raise
        legacy_profile = LlmProfile(
            id="default", label="Default",
            base_url=legacy["ANTHROPIC_BASE_URL"],
            auth_token=legacy["ANTHROPIC_AUTH_TOKEN"],
            default_model=legacy.get("ANTHROPIC_MODEL") or None,
            opus_model=legacy.get("ANTHROPIC_DEFAULT_OPUS_MODEL") or None,
            sonnet_model=legacy.get("ANTHROPIC_DEFAULT_SONNET_MODEL") or None,
            haiku_model=legacy.get("ANTHROPIC_DEFAULT_HAIKU_MODEL") or None,
        )
        from ..llm_profiles import ResolvedProfile
        if model_override and ":" in model_override:
            _, model = model_override.split(":", 1)
        else:
            model = model_override or legacy_profile.default_model
        resolved = ResolvedProfile(profile=legacy_profile, model=model)
    model = resolved.model
    if vision_image_paths and not resolved.profile.vision_model:
        raise HTTPException(409, "vision_model_missing")
    if vision_image_paths and any(
        tool in {"mcp__Vision__*", "mcp__Vision__image_read"}
        for tool in (extra_disallowed_tools or [])
    ):
        raise HTTPException(409, "vision_tool_disabled")

    # options.env carries ONLY non-cred runtime keys — the cred keys are deliberately
    # absent (the CLI reads them from settings.json). Point the AGENT's python/pip at
    # the per-account /workspace venv (persistent, survives restarts). options.env is
    # MERGED onto the CLI subprocess's inherited os.environ (per-key override), so the
    # runner SERVICE's own os.environ is untouched and a user-installed package can't
    # shadow a dependency this service imports.
    runtime_settings = read_runtime_settings()
    cross_session_enabled = (
        runtime_settings.get("cross_session_interaction_enabled", False)
        if enable_cross_session_interaction is None
        else bool(enable_cross_session_interaction)
    )
    env_dict: dict[str, str] = {}
    if runtime_settings["extra_env_enabled"]:
        # read_runtime_settings has already validated names, values and limits.
        env_dict.update(runtime_settings["extra_env"])
    if runtime_settings.get("agent_teams_enabled", False):
        # Agent Teams is a Claude Code experimental gate. It is intentionally
        # managed separately from user-defined env so CLAUDE_CODE_* remains
        # protected in the generic environment editor.
        env_dict["CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS"] = "1"
    try:
        from ..sandbox_venv import venv_env_overlay
        env_dict.update(venv_env_overlay(env_dict))
    except Exception:
        _get_logger().warning("venv env overlay skipped", exc_info=True)

    def _stderr_logger(line: str) -> None:
        stripped = line.rstrip()
        if stripped:
            _stderr_logger.lines.append(stripped)  # type: ignore[attr-defined]
            if "[ERROR]" in stripped or "error" in stripped.lower():
                _get_logger().warning("CLI stderr: {}", stripped)

    _stderr_logger.lines = []  # type: ignore[attr-defined]

    # ``options.skills`` is the explicit allowlist (denylist-derived) of
    # discovered skills. The SDK auto-adds ``"Skill"`` to allowed_tools when
    # this is set, so we no longer pass it here. If computation fails (e.g.
    # a long-running daemon still has the pre-migration ChannelConfigStore in
    # memory), we leave ``skills=None`` so the CLI's own defaults apply —
    # better to over-expose than to crash the agent run.
    try:
        from ..skills import compute_enabled_skill_names
        enabled_skill_names: list[str] | None = compute_enabled_skill_names(username)
    except Exception:
        _get_logger().warning(
            "compute_enabled_skill_names failed; leaving options.skills unset",
            exc_info=True,
        )
        enabled_skill_names = None

    disallowed_tools = list(BUILTIN_DISALLOWED_TOOLS)
    if not cross_session_enabled:
        # ListPeers is the legacy alias for ListAgents in CLI 2.1.226. Deny
        # both so the product switch cannot be bypassed through the alias.
        disallowed_tools.extend(["ListAgents", "ListPeers"])
    if not enable_permission_feedback:
        # Caller cannot answer prompts — strip AskUserQuestion so the model
        # can't call it and stall the run waiting on a human.
        disallowed_tools.append("AskUserQuestion")
    for tool in extra_disallowed_tools or []:
        # Per-run channel denylist (AgentRunRequest.disallowed_tools) — e.g. the
        # Feishu DM connector blocks FileCanvas's register_file tool (no canvas panel there).
        if tool and tool not in disallowed_tools:
            disallowed_tools.append(tool)

    options = ClaudeAgentOptions(
        model=model,
        env=env_dict,
        cwd=cwd,
        add_dirs=list(add_dirs or []),
        permission_mode=permission_mode or "bypassPermissions",
        setting_sources=["project","user"],
        allowed_tools=[],
        disallowed_tools=disallowed_tools,
        stderr=_stderr_logger,
        include_hook_events=True,
        include_partial_messages=include_partial_messages,
        skills=enabled_skill_names,
        # Agent replaces the native prompt with platform reminders; Code keeps
        # the Claude Code preset and appends the exact same reminder payload.
        system_prompt=build_run_system_prompt(run_mode),
    )
    options._priva_run_mode = run_mode
    # Claude Agent SDK maps ``settings`` to --settings (highest user-controlled
    # settings layer) and ``model`` to --model.  Keep the secret in a 0600 file
    # under the app config path; it never appears in argv or options.env.
    overlay_path, overlay_manager = open_profile_settings_overlay(
        resolved.profile,
        model=model,
        extra_settings={
            # Discovery is controlled by the ListAgents deny rule and this
            # matching inbound rule prevents warm sockets from accepting peer
            # turns. SendMessage is intentionally not denied here: Claude uses
            # the same tool for subagents and Agent Teams, so a bare deny would
            # silently disable those independent features as well. Therefore
            # this product setting is not an outbound egress-isolation boundary.
            "crossSessionInbound": (
                "accept" if cross_session_enabled else "refuse"
            ),
            # This feature is scoped to the account Pod's local warm sessions.
            # If Remote Control is ever connected, crossing a machine boundary
            # must still require explicit approval even in bypass mode.
            "isolatePeerMachines": True,
        },
    )
    options.settings = overlay_path
    options._priva_profile_id = resolved.profile.id
    options._priva_model_id = model
    options._priva_overlay_manager = overlay_manager
    options._priva_overlay_path = overlay_path
    options._priva_vision_image_paths = tuple(vision_image_paths or ())
    if max_turns and max_turns > 0:
        # D14 runaway guard for unattended runs: the CLI stops at the cap and
        # the result message carries subtype=error_max_turns.
        options.max_turns = max_turns

    if extra_allowed_tools:
        existing = list(options.allowed_tools or [])
        for tool in extra_allowed_tools:
            if tool not in existing:
                existing.append(tool)
        options.allowed_tools = existing
    # Apply runtime configuration
    from priva_common.user_store import get_user_store as _get_user_store
    runtime = _get_user_store().get_runtime_config()

    # Apply CLI path if configured.
    # If the target is a Node.js script (no shebang / not a native binary),
    # the OS will refuse to exec it directly.  Detect that case and create a
    # thin wrapper script so subprocess can launch it.
    cli_path = runtime.get("cli_path")
    if cli_path:
        cli_path = _ensure_executable(str(cli_path))
        options.cli_path = cli_path
    prompt_suggestions = bool(
        enable_prompt_suggestions
        and runtime_settings["prompt_suggestion_enabled"]
    )
    if prompt_suggestions:
        # Claude Code requires BOTH the environment gate and CLI flag.
        options.env["CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION"] = "true"
        options.extra_args["prompt-suggestions"] = "true"
    options._priva_prompt_suggestion_enabled = prompt_suggestions

    if session_id:
        options.resume = session_id
    if enable_file_checkpointing:
        options.enable_file_checkpointing = True
        options.extra_args["replay-user-messages"] = None
    if fork_session and session_id:
        options.fork_session = True
    effective_mode = permission_mode or "bypassPermissions"
    # In bypass mode with no explicit callback, fall back to _auto_approve_tool
    # (auto-allow, but managed-hook "ask" decisions fail closed). Streaming
    # runs always pass the coordinator-backed unified callback upstream, so
    # this fallback only serves coordinator-less paths.
    if can_use_tool is None and effective_mode == "bypassPermissions":
        can_use_tool = _auto_approve_tool
    if can_use_tool is not None:
        options.can_use_tool = can_use_tool
        options.hooks = {"PreToolUse": [HookMatcher(matcher=None, hooks=[_noop_pre_tool_hook])]}

    # Ensure admin hooks are in .claude/settings.json and add in-process callbacks
    from ..hooks.builder import build_hooks
    programmatic_hooks = build_hooks(username, cwd, auth_method=auth_method)
    if programmatic_hooks:
        existing = options.hooks or {}
        for event, matchers in programmatic_hooks.items():
            existing.setdefault(event, []).extend(matchers)
        options.hooks = existing

    # MCP servers: the FILES are canonical (config-source consistency, item C).
    # "auto" injects nothing — the CLI discovers user servers from
    # $CLAUDE_CONFIG_DIR/.claude.json and project servers from {cwd}/.mcp.json
    # natively (headless approval via the enableAllProjectMcpServers default
    # seeded at startup), so terminal `claude` and SDK runs see the same set.
    # Injection + --strict-mcp-config remain ONLY for per-run filtering; the
    # FileCanvas SDK server added below rides options.mcp_servers either way
    # (without strict it merges on top of native discovery).
    if mcp_servers is None or mcp_servers == "disable" or mcp_servers == []:
        # Shut off all file-discovered servers; platform built-ins added below
        # are still passed explicitly and therefore survive strict mode.
        options.extra_args["strict-mcp-config"] = None
    elif isinstance(mcp_servers, list):
        from ..mcp.config_manager import McpConfigManager

        mcp_dict = McpConfigManager(username).build_mcp_dict(
            cwd=cwd, filter_names=mcp_servers
        )
        if mcp_dict:
            options.mcp_servers = mcp_dict
        # Strict even when the subset matched nothing — a stale name must mean
        # "no servers", not "fall back to everything".
        options.extra_args["strict-mcp-config"] = None
    # else "auto": native file discovery, no injection.

    # --- Scheduler MCP tools (US-2): the agent's only sanctioned self-scheduling
    # path, re-pointed at the dataplane (Phase 4a). JWT login sessions only —
    # same gating as FileCanvas below. Fail-soft: a broken injection must not
    # kill the run.
    if username and auth_method == "jwt" and inject_scheduler_tools:
        try:
            from ..scheduled_runs.mcp_tools import (
                SCHEDULER_MCP_SERVER_NAME,
                SCHEDULER_MCP_TOOL_PATTERN,
                build_scheduler_mcp_server,
            )

            existing = options.mcp_servers or {}
            if not isinstance(existing, dict):
                existing = {}
            existing[SCHEDULER_MCP_SERVER_NAME] = build_scheduler_mcp_server(username)
            options.mcp_servers = existing

            allowed = list(options.allowed_tools or [])
            if SCHEDULER_MCP_TOOL_PATTERN not in allowed:
                allowed.append(SCHEDULER_MCP_TOOL_PATTERN)
            options.allowed_tools = allowed
        except Exception:
            _get_logger().warning("Failed to inject scheduler MCP tools", exc_info=True)

    # --- Vision MCP: only for a route that already uploaded and validated
    # image attachments. Unlike optional built-ins, failure here is fatal: the
    # outer text model must never continue as though it had inspected images.
    if vision_image_paths:
        vision_model = resolved.profile.vision_model
        assert vision_model is not None
        from ..mcp.vision import (
            VISION_MCP_SERVER_NAME,
            VISION_MCP_TOOL_PATTERN,
            build_vision_mcp_server,
        )

        generated_server = build_vision_mcp_server(
            resolved.profile,
            vision_model,
            vision_image_paths,
        )
        existing = options.mcp_servers or {}
        if not isinstance(existing, dict):
            existing = {}
        existing[VISION_MCP_SERVER_NAME] = generated_server
        options.mcp_servers = existing

        allowed = list(options.allowed_tools or [])
        if VISION_MCP_TOOL_PATTERN not in allowed:
            allowed.append(VISION_MCP_TOOL_PATTERN)
        options.allowed_tools = allowed

    # --- Inject FileCanvas file-registration tool for JWT-backed login sessions only ---
    # Skipped entirely when the per-run denylist blocks FileCanvas (e.g. Feishu DM):
    # not injecting the server is cleaner than disallowing a visible tool — the
    # model never sees register_file at all.
    canvas_blocked = any(
        tool in {"mcp__FileCanvas__*", "mcp__FileCanvas__register_file"}
        for tool in (extra_disallowed_tools or [])
    )
    if username and auth_method == "jwt" and not canvas_blocked:
        try:
            from ..mcp.built_in import (
                FILE_CANVAS_MCP_SERVER_NAME,
                FILE_CANVAS_MCP_TOOL_PATTERN,
                build_file_canvas_mcp_server,
            )

            generated_server = build_file_canvas_mcp_server(cwd)
            existing = options.mcp_servers or {}
            if not isinstance(existing, dict):
                existing = {}
            existing[FILE_CANVAS_MCP_SERVER_NAME] = generated_server
            options.mcp_servers = existing

            allowed = list(options.allowed_tools or [])
            if FILE_CANVAS_MCP_TOOL_PATTERN not in allowed:
                allowed.append(FILE_CANVAS_MCP_TOOL_PATTERN)
            options.allowed_tools = allowed
        except Exception:
            _get_logger().warning("Failed to inject FileCanvas MCP server")

    # --- OpenClaw delegation tools: deferred (channel-connector, Phase 4).
    # The channels subsystem is not part of the agent-runner this phase; the
    # injection block is removed so the run path never imports ``services.channels``. ---

    return options
