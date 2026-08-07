"""In-process MCP server: the agent's ONLY sanctioned self-scheduling path
(US-2). Port of the monolith ``priva/api/services/scheduler/mcp_tools.py``
re-pointed at the dataplane scheduler client — the pod is single-account, so
every operation is scoped to the pinned account and a guessed foreign job_id
resolves to nothing. The monolith's ``write_command('reload_user')`` refresh
is gone by design: scheduler replicas re-list the job set ≤30s (D6);
``trigger_now`` goes to the scheduler's internal API instead of a command file.

The confirm-before-create UX rule and the "durable scheduling ≠ sub-agent
delegation" scoping carry over verbatim.
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import httpx
from claude_agent_sdk import create_sdk_mcp_server, tool

from priva_common.config import get_settings
from priva_common.dataplane import get_client
from priva_common.logging import get_app_logger
from priva_common.models.scheduler import (
    AgentRunConfig,
    CronTriggerConfig,
    HttpCallConfig,
    IntervalTriggerConfig,
    ScheduledJobDefinition,
    UserScriptConfig,
)
from priva_common.service_token import auth_header

logger = get_app_logger(__name__)


SCHEDULER_MCP_SERVER_NAME = "Scheduler"
SCHEDULER_MCP_TOOL_PATTERN = f"mcp__{SCHEDULER_MCP_SERVER_NAME}__*"


SCHEDULER_TOOL_SCOPE = (
    "Priva scheduler tools manage durable scheduled automations only: cron jobs "
    "and recurring interval jobs saved in the scheduler. They are not sub-agent "
    "delegation tools and are not a way to run an agent for the current request. "
    "If the user asks to use/ask/run/delegate to an agent or sub-agent now, such "
    "as 'ask the research agent to do X', 'let xxx agent finish X', "
    "'让 xxx agent 完成 xxx', or '派一个 sub agent 做 xxx', do not use scheduler tools; "
    "use the built-in Agent/sub-agent mechanism instead."
)


def _resolve_account_id(username: str) -> str | None:
    """The pod's pinned account (env, set at boot) — dataplane lookup fallback
    for the in-process dev mode."""
    pinned = os.environ.get("ACCOUNT_ID")
    if pinned:
        return pinned
    try:
        user = get_client().accounts.get_by_username(username)
        return user.account_id if user else None
    except Exception:
        logger.warning("account lookup failed for {}", username, exc_info=True)
        return None


def _find_job(account_id: str, needle: str) -> ScheduledJobDefinition | None:
    """ID → exact name → partial name, always within this account's jobs."""
    jobs = get_client().scheduler.list_jobs(account_id)
    for j in jobs:
        if j.id == needle:
            return j
    for j in jobs:
        if j.name.lower() == needle.lower():
            return j
    for j in jobs:
        if needle.lower() in j.name.lower():
            return j
    return None


def _format_trigger(trigger) -> str:
    if hasattr(trigger, "expr"):
        return f"cron {trigger.expr}"
    parts = []
    for unit, suffix in (("weeks", "w"), ("days", "d"), ("hours", "h"),
                         ("minutes", "m"), ("seconds", "s")):
        v = getattr(trigger, unit, 0)
        if v:
            parts.append(f"{v}{suffix}")
    return f"every {' '.join(parts)}" if parts else "interval (default)"


def _text(msg: str, *, error: bool = False) -> dict:
    out: dict = {"content": [{"type": "text", "text": msg}]}
    if error:
        out["is_error"] = True
    return out


def build_scheduler_tools(username: str) -> list:
    """The 7 scheduler tools scoped to the pod's account."""

    @tool(
        "scheduler_list_jobs",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "List existing scheduled automation jobs for the current user. Use only when "
            "the user explicitly asks to list/show scheduled jobs, cron jobs, recurring "
            "automations, or scheduler entries. Returns job names, types, triggers, and status."
        ),
        {},
    )
    async def list_jobs(args):
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)
        jobs = await asyncio.to_thread(get_client().scheduler.list_jobs, account_id)
        if not jobs:
            return _text("No scheduled jobs found.")
        lines = []
        for j in jobs:
            jt = j.job_config.job_type if j.job_config else "agent_run"
            lines.append(
                f"- **{j.name}** (id: `{j.id}`, type: {jt}, status: {j.status})\n"
                f"  Schedule: {_format_trigger(j.trigger)} | TZ: {j.timezone}"
            )
        return _text("\n".join(lines))

    @tool(
        "scheduler_view_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "View detailed information about an existing scheduled automation job by its "
            "ID or name. Use only when the user is asking about a saved scheduler job, "
            "not when they ask an agent or sub-agent to perform work now."
        ),
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID (8-char UUID prefix). Can also pass the job name — will be matched."},
            },
            "required": ["job_id"],
        },
    )
    async def view_job(args):
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)
        needle = args.get("job_id", "")
        job = await asyncio.to_thread(_find_job, account_id, needle)
        if not job:
            return _text(f"Job not found: {needle}", error=True)

        jc = job.job_config
        jt = jc.job_type if jc else "agent_run"
        detail = (
            f"**{job.name}** (id: `{job.id}`)\n"
            f"- Type: {jt}\n"
            f"- Status: {job.status}\n"
            f"- Schedule: {_format_trigger(job.trigger)}\n"
            f"- Timezone: {job.timezone}\n"
            f"- Created: {job.created_at.isoformat()}\n"
        )
        if jt == "agent_run":
            prompt = jc.prompt if jc else job.prompt
            model = (jc.model if jc else job.model) or "default"
            detail += f"- Model: {model}\n- Prompt: {prompt}\n"
        elif jt == "http_call" and jc:
            detail += f"- Method: {jc.method}\n- URL: {jc.url}\n- Timeout: {jc.timeout_seconds}s\n"
        elif jt == "user_script" and jc:
            detail += f"- Language: {jc.language}\n- Source: {jc.source}\n"
            if jc.source == "file":
                detail += f"- File: {jc.file_path}\n"
            detail += f"- Timeout: {jc.timeout_seconds}s\n"
        return _text(detail)

    @tool(
        "scheduler_create_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "Create a new SAVED SCHEDULED AUTOMATION job. Cron / interval only — this "
            "tool does NOT run anything immediately and does NOT delegate the current "
            "conversation task to an agent.\n"
            "\n"
            "STRICT ROUTING RULE: only use this tool when the user explicitly asks for a "
            "saved schedule, cron job, recurring automation, or repeated interval. The "
            "word 'agent' is never enough by itself. Look for schedule phrases like "
            "'every day', 'every N minutes', 'weekdays at 9am', 'cron', 'schedule', "
            "'recurring', 'periodically', '定时', '每天', '每周', '每隔 N 分钟', or '周期性'.\n"
            "\n"
            "WHEN NOT TO USE: do NOT use for one-shot or current-turn requests like "
            "'run X', 'test X', 'try X', 'execute X now', 'ask the coding agent to do X', "
            "'use a sub agent for X', '让 xxx agent 完成 xxx', or '派一个 agent 处理 xxx'. "
            "Those should use the built-in Agent/sub-agent mechanism or directly call "
            "the relevant tool. The `agent_run` job type below is an internal "
            "scheduler enum for cron/interval automation; it is not the Agent/sub-agent tool.\n"
            "\n"
            "IMPORTANT: Before calling this tool, you MUST use the AskUserQuestion tool to confirm the job configuration with the user. "
            "Present the job name, type, schedule, and type-specific parameters for the user to review and approve.\n"
            "\n"
            "## Job types\n"
            "\n"
            "### agent_run — Internal scheduler enum for recurring agent automation\n"
            "Required params: prompt\n"
            "Optional params: model (override the default model)\n"
            "Use only when the user wants a saved cron/interval automation that launches "
            "an agent later or repeatedly. Do not select this just because the user said "
            "'agent'; current agent/sub-agent work must not be routed here. The scheduled "
            "agent session runs with bypassPermissions mode in the user's workspace, "
            "fired by cron/interval — NOT immediately.\n"
            "\n"
            "### http_call — Make an HTTP request to an endpoint\n"
            "Required params: url\n"
            "Optional params: method (default GET), headers (dict), body (string), timeout_seconds (default 30)\n"
            "Use for health checks, webhooks, API polling, etc.\n"
            "\n"
            "### user_script — Execute a Python or shell script\n"
            "Required params: language (python or shell), and either file_path (relative to workspace) or script (inline content)\n"
            "Optional params: timeout_seconds (default 300)\n"
            "If script param is provided, source is 'inline'. If file_path is provided, source is 'file'.\n"
            "Scripts run in the user's workspace directory.\n"
            "\n"
            "## Schedule\n"
            "\n"
            "trigger_type=cron: Use cron_expr with standard 5-field format: 'minute hour day month day_of_week'\n"
            "  Examples: '0 9 * * *' (daily 9am), '*/30 * * * *' (every 30min), '0 9 * * 1-5' (weekdays 9am)\n"
            "\n"
            "trigger_type=interval: Use interval_minutes for the repeat period.\n"
            "  Examples: 5 (every 5 min), 60 (hourly), 1440 (daily)\n"
            "\n"
            "## Workflow\n"
            "\n"
            "1. Gather requirements from the user (what to run, how often)\n"
            "2. Use AskUserQuestion to present the full job config for confirmation\n"
            "3. Only call this tool after the user approves\n"
            "4. If there is no explicit schedule/recurrence requirement, do not call this tool\n"
        ),
        {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Human-readable job name. Keep it short and descriptive."},
                "job_type": {"type": "string", "enum": ["agent_run", "http_call", "user_script"], "description": "The scheduled automation kind. `agent_run` means a saved cron/interval automation that launches an agent later or repeatedly; it must not be used for current sub-agent delegation."},
                "trigger_type": {"type": "string", "enum": ["cron", "interval"], "description": "Schedule type: 'cron' for cron expressions, 'interval' for fixed repeat intervals."},
                "cron_expr": {"type": "string", "description": "5-field cron expression. Required when trigger_type=cron. Format: 'minute hour day month day_of_week'. Examples: '0 9 * * *', '*/15 * * * *', '0 0 1 * *'."},
                "interval_minutes": {"type": "number", "description": "Repeat interval in minutes. Required when trigger_type=interval. Examples: 5, 30, 60, 1440."},
                "timezone": {"type": "string", "description": "IANA timezone for the schedule. Defaults to Asia/Shanghai."},
                "prompt": {"type": "string", "description": "[agent_run] The prompt saved for the recurring scheduled agent automation. Should be a complete, self-contained instruction for future cron/interval runs. Do not use this to ask a sub-agent to perform the current user request."},
                "model": {"type": "string", "description": "[agent_run] Optional model override. Leave empty to use the system default."},
                "url": {"type": "string", "description": "[http_call] The full URL to call. Must include protocol (http:// or https://)."},
                "method": {"type": "string", "enum": ["GET", "POST", "PUT", "DELETE"], "description": "[http_call] HTTP method. Defaults to GET."},
                "headers": {"type": "object", "description": "[http_call] HTTP headers as key-value pairs. Example: {\"Authorization\": \"Bearer xxx\", \"Content-Type\": \"application/json\"}"},
                "body": {"type": "string", "description": "[http_call] Request body string. Typically JSON for POST/PUT requests."},
                "script": {"type": "string", "description": "[user_script] Inline script content. Provide this OR file_path, not both. If provided, source is set to 'inline'."},
                "file_path": {"type": "string", "description": "[user_script] Path to script file, relative to the user's workspace. Provide this OR script, not both. If provided, source is set to 'file'."},
                "language": {"type": "string", "enum": ["python", "shell"], "description": "[user_script] Script language. Determines the interpreter (python3 or /bin/bash)."},
                "timeout_seconds": {"type": "number", "description": "[http_call/user_script] Execution timeout. Defaults: 30s for http_call, 300s for user_script."},
            },
            "required": ["name", "job_type", "trigger_type"],
        },
    )
    async def create_job(args):
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)

        if args["trigger_type"] == "cron":
            trigger = CronTriggerConfig(expr=args.get("cron_expr", "0 9 * * *"))
        else:
            mins = args.get("interval_minutes", 60)
            trigger = IntervalTriggerConfig(hours=int(mins // 60), minutes=int(mins % 60))

        jt = args["job_type"]
        if jt == "agent_run":
            job_config = AgentRunConfig(prompt=args.get("prompt", ""), model=args.get("model"))
        elif jt == "http_call":
            job_config = HttpCallConfig(
                method=args.get("method", "GET"), url=args.get("url", ""),
                headers=args.get("headers", {}), body=args.get("body"),
                timeout_seconds=int(args.get("timeout_seconds", 30)),
            )
        elif jt == "user_script":
            job_config = UserScriptConfig(
                language=args.get("language", "python"),
                source="inline" if args.get("script") else "file",
                file_path=args.get("file_path"), script=args.get("script"),
                timeout_seconds=int(args.get("timeout_seconds", 300)),
            )
        else:
            return _text(f"Unknown job type: {jt}", error=True)

        now = datetime.now(timezone.utc)
        defn = ScheduledJobDefinition(
            id=str(uuid4())[:8],
            name=args["name"],
            prompt=job_config.prompt if hasattr(job_config, "prompt") else "",
            trigger=trigger,
            timezone=args.get("timezone") or "Asia/Shanghai",
            status="active",
            job_config=job_config,
            created_at=now,
            updated_at=now,
        )
        created = await asyncio.to_thread(
            get_client().scheduler.create_job, account_id, defn)
        return _text(
            f"Created job **{created.name}** (id: `{created.id}`, type: {jt}, status: active). "
            f"It arms on every scheduler replica within ~30s."
        )

    @tool(
        "scheduler_delete_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "Delete an existing scheduled automation job by its ID or name. Use only "
            "for saved scheduler jobs, not for stopping or cancelling a current "
            "Agent/sub-agent task. IMPORTANT: Use AskUserQuestion to confirm with the "
            "user before deleting."
        ),
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID or name to delete"},
            },
            "required": ["job_id"],
        },
    )
    async def delete_job(args):
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)
        needle = args.get("job_id", "")
        job = await asyncio.to_thread(_find_job, account_id, needle)
        if not job:
            return _text(f"Job not found: {needle}", error=True)
        await asyncio.to_thread(get_client().scheduler.delete_job, job.id)
        return _text(f"Deleted job **{job.name}** (id: `{job.id}`). Run history is kept.")

    @tool(
        "scheduler_trigger_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "Manually trigger an EXISTING saved scheduled job by ID or name. This is "
            "only for retrying/testing a scheduler entry that already exists; it is not "
            "a general one-shot execution tool. Do not use this to start a new agent or "
            "sub-agent for the current request. Use AskUserQuestion to confirm before "
            "triggering if the job has side effects."
        ),
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID or name to trigger"},
            },
            "required": ["job_id"],
        },
    )
    async def trigger_job(args):
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)
        needle = args.get("job_id", "")
        job = await asyncio.to_thread(_find_job, account_id, needle)
        if not job:
            return _text(f"Job not found: {needle}", error=True)
        url = f"{get_settings().scheduler.internal_url}/internal/trigger/{job.id}"
        try:
            async with httpx.AsyncClient(trust_env=False, timeout=10.0) as cx:
                resp = await cx.post(url, headers=auth_header())
        except httpx.HTTPError as exc:
            return _text(f"Scheduler unreachable: {exc}", error=True)
        if resp.status_code != 202:
            return _text(f"Trigger failed ({resp.status_code}): {resp.text[:200]}", error=True)
        return _text(
            f"Triggered immediate run for **{job.name}** (id: `{job.id}`). "
            f"The run appears in the job's history."
        )

    @tool(
        "scheduler_pause_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "Pause an existing scheduled automation job so it stops running on its "
            "saved schedule. Use only for scheduler entries, not for current agent or "
            "sub-agent work."
        ),
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID or name to pause"},
            },
            "required": ["job_id"],
        },
    )
    async def pause_job(args):
        return await _set_status(args.get("job_id", ""), "paused", "Paused")

    @tool(
        "scheduler_resume_job",
        (
            f"{SCHEDULER_TOOL_SCOPE}\n\n"
            "Resume an existing paused scheduled automation job. Use only for saved "
            "scheduler entries, not for current agent or sub-agent work."
        ),
        {
            "type": "object",
            "properties": {
                "job_id": {"type": "string", "description": "Job ID or name to resume"},
            },
            "required": ["job_id"],
        },
    )
    async def resume_job(args):
        return await _set_status(args.get("job_id", ""), "active", "Resumed")

    async def _set_status(needle: str, status: str, verb: str) -> dict:
        account_id = _resolve_account_id(username)
        if not account_id:
            return _text("Scheduler unavailable: account not resolved.", error=True)
        job = await asyncio.to_thread(_find_job, account_id, needle)
        if not job:
            return _text(f"Job not found: {needle}", error=True)
        await asyncio.to_thread(get_client().scheduler.set_job_status, job.id, status)
        return _text(f"{verb} job **{job.name}** (id: `{job.id}`)")

    return [list_jobs, view_job, create_job, delete_job, trigger_job, pause_job, resume_job]


def build_scheduler_mcp_server(username: str):
    """The in-process SDK MCP server registered by the options builder."""
    return create_sdk_mcp_server(
        name=SCHEDULER_MCP_SERVER_NAME,
        version="1.0.0",
        tools=build_scheduler_tools(username),
    )
