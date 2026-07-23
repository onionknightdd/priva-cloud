"""data-spine gRPC server — exposes the in-process services over the wire.

When CP / agent-runner / operator run as separate pods (no shared filesystem),
they reach durable state through this single-writer server instead of composing
the repo in-process. Sync grpc server over a thread pool: SqliteRepo serializes
writes behind one lock and PgRepo is connection-pool-backed, so a thread pool is
correct and simple for both backends.

Builds proto messages FROM the boundary records (the mirror of dataplane.converters).
All nine domains are served, scheduler included (Phase 4a).
"""

from __future__ import annotations

import json
import uuid
from concurrent import futures

import grpc

from priva_common.dataplane import converters as cv
from priva_common.dataplane.v1 import (
    account_pb2,
    account_pb2_grpc,
    admin_pb2,
    admin_pb2_grpc,
    binding_pb2,
    binding_pb2_grpc,
    common_pb2,
    feishu_channel_config_pb2,
    feishu_channel_config_pb2_grpc,
    hook_policy_pb2,
    hook_policy_pb2_grpc,
    quota_pb2,
    quota_pb2_grpc,
    registration_pb2,
    registration_pb2_grpc,
    resource_spec_pb2,
    resource_spec_pb2_grpc,
    runner_defaults_pb2,
    runner_defaults_pb2_grpc,
    scheduler_pb2,
    scheduler_pb2_grpc,
)
from priva_common.logging import get_app_logger
from priva_common.models.scheduler import JobRunRecord, ScheduledJobDefinition

from .service import build_inprocess_client, build_repo, describe_store

logger = get_app_logger(__name__)


def _s(v) -> str:
    if v is None:
        return ""
    return v if isinstance(v, str) else v.isoformat()


def _acct_pb(u) -> account_pb2.Account:
    if u is None:
        return account_pb2.Account()  # empty account_id => not found
    return account_pb2.Account(
        account_id=u.account_id or "",
        username=u.username,
        role=u.role,
        status=u.status,
        api_key=u.api_key or "",
        feishu_user_id=u.feishu_user_id or "",
        feishu_display_name=u.feishu_display_name or "",
        created_at=_s(u.created_at),
        updated_at=_s(u.updated_at),
        agent_runner_type=u.agent_runner_type or "auto_scale",
    )


def _quota_pb(q) -> quota_pb2.Quota:
    if q is None:
        return quota_pb2.Quota()
    return quota_pb2.Quota(
        account_id=q.account_id,
        tier=q.tier,
        max_concurrent_sessions=q.max_concurrent_sessions,
        idle_grace_seconds=q.idle_grace_seconds,
        updated_at=q.updated_at or "",
    )


def _binding_pb(b) -> binding_pb2.Binding:
    if b is None:
        return binding_pb2.Binding()
    return binding_pb2.Binding(
        binding_id=b.binding_id,
        account_id=b.account_id,
        session_uuid=b.session_uuid or "",  # NULL (detached) → "" on the wire
        first_run_done=b.first_run_done,
        feishu_chat_id=b.feishu_chat_id or "",
        bound_at=b.bound_at or "",
        rebound_at=b.rebound_at or "",
    )


def _rspec_pb(r) -> resource_spec_pb2.ResourceSpec:
    if r is None:
        return resource_spec_pb2.ResourceSpec()  # empty account_id => unset
    return resource_spec_pb2.ResourceSpec(
        account_id=r.account_id,
        cpu_cores=r.cpu_cores,
        memory_mb=r.memory_mb,
        volume_gb=r.volume_gb,
        updated_at=r.updated_at or "",
    )


def _feishu_pb(r) -> feishu_channel_config_pb2.FeishuChannelConfig:
    if r is None:
        return feishu_channel_config_pb2.FeishuChannelConfig()  # empty account_id => unset
    return feishu_channel_config_pb2.FeishuChannelConfig(
        account_id=r.account_id,
        app_id=r.app_id or "",
        has_app_secret=r.has_app_secret,        # presence only — app_secret is NEVER serialized
        app_secret_updated_at=r.app_secret_updated_at or "",
        user_enabled=r.user_enabled,
        admin_disabled=r.admin_disabled,
        effective_enabled=r.effective_enabled,
        single_chat_access_mode=r.single_chat_access_mode or "owner_only",
        allowed_union_ids=r.allowed_union_ids or "[]",
        welcome_message=r.welcome_message or "",
        reject_message=r.reject_message or "",
        model=r.model or "",
        max_queue_size=r.max_queue_size,
        enable_permission_feedback=r.enable_permission_feedback,
        feedback_timeout_seconds=r.feedback_timeout_seconds,
        domain=r.domain or "feishu",
        conn_status=r.conn_status or "disabled",
        last_error_code=r.last_error_code or 0,
        last_error_message=r.last_error_message or "",
        last_connected_at=r.last_connected_at or "",
        status_updated_at=r.status_updated_at or "",
        desired_digest=r.desired_digest or "",
        updated_by=r.updated_by or "",
        updated_at=r.updated_at or "",
        owner_union_id=r.owner_union_id or "",
        owner_open_id=r.owner_open_id or "",
        owner_bound_at=r.owner_bound_at or "",
        group_chat_enabled=r.group_chat_enabled,
        effective_group_enabled=r.effective_group_enabled,
    )


def _channel_platform_pb(r) -> feishu_channel_config_pb2.ChannelPlatformConfig:
    return feishu_channel_config_pb2.ChannelPlatformConfig(
        group_chat_disabled=r.group_chat_disabled,
        updated_by=r.updated_by or "",
        updated_at=r.updated_at or "",
    )


def _rdefaults_pb(r) -> runner_defaults_pb2.RunnerDefaults:
    return runner_defaults_pb2.RunnerDefaults(
        idle_grace_seconds=r.idle_grace_seconds,
        min_alive_after_wake_seconds=r.min_alive_after_wake_seconds,
        cpu_cores=r.cpu_cores,
        memory_mb=r.memory_mb,
        storage_gb=r.storage_gb,
        runner_image=r.runner_image,
        terminal_resource_percent=r.terminal_resource_percent,
        terminal_max_sessions=r.terminal_max_sessions,
        terminal_idle_timeout_seconds=r.terminal_idle_timeout_seconds,
        terminal_max_lifetime_seconds=r.terminal_max_lifetime_seconds,
        terminal_scale_down_grace_seconds=r.terminal_scale_down_grace_seconds,
        updated_at=r.updated_at or "",
    )


def _hook_policy_pb(p) -> hook_policy_pb2.HookPolicy:
    if p is None:
        return hook_policy_pb2.HookPolicy()  # empty id => not found
    return hook_policy_pb2.HookPolicy(
        id=p.id,
        hook_type=p.hook_type,
        name=p.name,
        description=p.description,
        events=list(p.events),
        matcher=p.matcher,
        timeout_seconds=p.timeout_seconds,
        interpreter=p.interpreter,
        script_body=p.script_body,
        content_hash=p.content_hash,
        url=p.url,
        headers_json=p.headers_json,
        allowed_env_vars=list(p.allowed_env_vars),
        mcp_server=p.mcp_server,
        mcp_tool=p.mcp_tool,
        enabled=p.enabled,
        enforced=p.enforced,
        default_on=p.default_on,
        predefined=p.predefined,
        seed_version=p.seed_version,
        target=p.target,
        updated_at=p.updated_at or "",
        updated_by=p.updated_by,
        enforced_events=list(p.enforced_events),
    )


def _pending_pb(p) -> registration_pb2.PendingRegistration:
    if p is None:
        return registration_pb2.PendingRegistration()  # empty request_id => not found
    return registration_pb2.PendingRegistration(
        request_id=p.request_id,
        username=p.username,
        display_name=p.display_name or "",
        runner_type=p.runner_type,
        cpu_cores=p.cpu_cores,
        memory_mb=p.memory_mb,
        volume_gb=p.volume_gb,
        note=p.note or "",
        status=p.status,
        created_at=p.created_at or "",
        updated_at=p.updated_at or "",
        password_hash=p.password_hash or "",
    )


class _AccountServicer(account_pb2_grpc.AccountServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Get(self, request, context):
        return _acct_pb(self.svc.get(request.account_id))

    def GetByUsername(self, request, context):
        return _acct_pb(self.svc.get_by_username(request.username))

    def List(self, request, context):
        return account_pb2.AccountList(accounts=[_acct_pb(u) for u in self.svc.list()])

    def Create(self, request, context):
        try:
            return _acct_pb(self.svc.create(
                request.username,
                request.password,
                request.role or "user",
                agent_runner_type=request.agent_runner_type or "auto_scale",
                password_hash=request.password_hash or None,
            ))
        except ValueError as exc:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, str(exc))

    def Update(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "password" in mask:
            kw["password"] = request.password
        if "role" in mask:
            kw["role"] = request.role
        if "status" in mask:
            kw["status"] = request.status
        if "agent_runner_type" in mask:
            kw["agent_runner_type"] = request.agent_runner_type
        if "api_key" in mask:
            kw["api_key"] = request.api_key or None
        if "feishu_user_id" in mask:
            kw["feishu_user_id"] = request.feishu_user_id or None
        if "feishu_display_name" in mask:
            kw["feishu_display_name"] = request.feishu_display_name or None
        try:
            return _acct_pb(self.svc.update(request.account_id, **kw))
        except ValueError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))

    def Delete(self, request, context):
        self.svc.delete(request.account_id)
        return common_pb2.Empty()

    def VerifyPassword(self, request, context):
        return common_pb2.BoolValue(value=self.svc.verify_password(request.username, request.password))

    def FindByApiKey(self, request, context):
        return _acct_pb(self.svc.find_by_api_key(request.api_key))

    def CountAdmins(self, request, context):
        return common_pb2.CountValue(value=self.svc.count_admins())

    def FindByFeishuUserId(self, request, context):
        return _acct_pb(self.svc.find_by_feishu_user_id(request.feishu_user_id))

    def HasUsers(self, request, context):
        return common_pb2.BoolValue(value=self.svc.has_users())


class _BindingServicer(binding_pb2_grpc.BindingServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Bind(self, request, context):
        # session_uuid "" → NULL (detached): "" is not NULL, so it would collide on
        # the partial unique index the moment a second account also detaches.
        return _binding_pb(self.svc.bind(
            request.account_id, request.session_uuid or None, request.feishu_chat_id or None))

    def Rebind(self, request, context):
        return _binding_pb(self.svc.rebind(
            request.account_id, request.session_uuid or None, request.feishu_chat_id or None))

    def ClaimFirstRunIM(self, request, context):
        return common_pb2.BoolValue(value=self.svc.claim_first_run_im(request.binding_id))

    def GetBinding(self, request, context):
        return _binding_pb(self.svc.get_binding(request.binding_id))

    def ListBindings(self, request, context):
        return binding_pb2.BindingList(bindings=[_binding_pb(b) for b in self.svc.list_bindings(request.account_id)])


class _QuotaServicer(quota_pb2_grpc.QuotaServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Get(self, request, context):
        return _quota_pb(self.svc.get(request.account_id))

    def Set(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "tier" in mask:
            kw["tier"] = request.tier
        if "max_concurrent_sessions" in mask:
            kw["max_concurrent_sessions"] = request.max_concurrent_sessions
        if "idle_grace_seconds" in mask:
            kw["idle_grace_seconds"] = request.idle_grace_seconds
        return _quota_pb(self.svc.set(request.account_id, **kw))


class _AdminServicer(admin_pb2_grpc.AdminServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Healthz(self, request, context):
        return admin_pb2.Health(status=self.svc.healthz())

    def Readyz(self, request, context):
        ok, detail = self.svc.readyz()
        return admin_pb2.Ready(ready=ok, detail=detail)

    def Stats(self, request, context):
        s = self.svc.stats()
        return admin_pb2.StatsResponse(accounts=s.get("accounts", 0), jobs=s.get("jobs", 0),
                                       runs=s.get("runs", 0), backend=s.get("backend", ""))


class _ResourceSpecServicer(resource_spec_pb2_grpc.ResourceSpecServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Get(self, request, context):
        return _rspec_pb(self.svc.get(request.account_id))

    def Set(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "cpu_cores" in mask:
            kw["cpu_cores"] = request.cpu_cores
        if "memory_mb" in mask:
            kw["memory_mb"] = request.memory_mb
        if "volume_gb" in mask:
            kw["volume_gb"] = request.volume_gb
        return _rspec_pb(self.svc.set(request.account_id, **kw))

    def List(self, request, context):
        return resource_spec_pb2.ResourceSpecList(specs=[_rspec_pb(r) for r in self.svc.list()])


class _FeishuChannelConfigServicer(
        feishu_channel_config_pb2_grpc.FeishuChannelConfigServiceServicer):
    def __init__(self, svc, platform_svc):
        self.svc = svc
        self.platform_svc = platform_svc  # channel_platform singleton (same proto service)

    def Get(self, request, context):
        return _feishu_pb(self.svc.get(request.account_id))

    def SetUser(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "app_id" in mask:
            kw["app_id"] = request.app_id
        if "app_secret" in mask:
            kw["app_secret"] = request.app_secret        # "" => clear, non-empty => set/rotate
        if "user_enabled" in mask:
            kw["user_enabled"] = request.user_enabled
        if "single_chat_access_mode" in mask:
            kw["single_chat_access_mode"] = request.single_chat_access_mode
        if "allowed_union_ids" in mask:
            kw["allowed_union_ids"] = request.allowed_union_ids
        if "welcome_message" in mask:
            kw["welcome_message"] = request.welcome_message
        if "reject_message" in mask:
            kw["reject_message"] = request.reject_message
        if "model" in mask:
            kw["model"] = request.model
        if "max_queue_size" in mask:
            kw["max_queue_size"] = request.max_queue_size
        if "enable_permission_feedback" in mask:
            kw["enable_permission_feedback"] = request.enable_permission_feedback
        if "feedback_timeout_seconds" in mask:
            kw["feedback_timeout_seconds"] = request.feedback_timeout_seconds
        if "domain" in mask:
            kw["domain"] = request.domain
        if "group_chat_enabled" in mask:
            kw["group_chat_enabled"] = request.group_chat_enabled
        return _feishu_pb(self.svc.set_user(request.account_id, updated_by=request.updated_by, **kw))

    def SetAdmin(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "admin_disabled" in mask:
            kw["admin_disabled"] = request.admin_disabled
        return _feishu_pb(self.svc.set_admin(request.account_id, updated_by=request.updated_by, **kw))

    def SetStatus(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "conn_status" in mask:
            kw["conn_status"] = request.conn_status
        if "last_error_code" in mask:
            kw["last_error_code"] = request.last_error_code
        if "last_error_message" in mask:
            kw["last_error_message"] = request.last_error_message
        if "last_connected_at" in mask:
            kw["last_connected_at"] = request.last_connected_at
        return _feishu_pb(self.svc.set_status(request.account_id, **kw))

    def List(self, request, context):
        return feishu_channel_config_pb2.FeishuChannelConfigList(
            configs=[_feishu_pb(r) for r in self.svc.list()])

    def ListEffective(self, request, context):
        return feishu_channel_config_pb2.FeishuChannelConfigList(
            configs=[_feishu_pb(r) for r in self.svc.list_effective()])

    def GetFeishuSecret(self, request, context):
        # Connector-only privileged read: decrypted plaintext app_secret.
        r = self.svc.get_secret(request.account_id)
        if r is None:
            return feishu_channel_config_pb2.FeishuSecret()  # empty account_id => no row
        return feishu_channel_config_pb2.FeishuSecret(
            account_id=r.account_id,
            app_id=r.app_id or "",
            app_secret=r.app_secret or "",
            domain=r.domain or "feishu",
        )

    def CreateLinkCode(self, request, context):
        code, expires = self.svc.create_link_code(request.account_id)
        return feishu_channel_config_pb2.LinkCode(code=code, expires_at=expires)

    def BindOwnerWithCode(self, request, context):
        ok = self.svc.bind_owner_with_code(
            request.account_id, request.code, request.union_id, request.open_id)
        return feishu_channel_config_pb2.BindOwnerResult(ok=bool(ok))

    def UnbindOwner(self, request, context):
        return _feishu_pb(self.svc.unbind_owner(
            request.account_id, updated_by=request.updated_by or ""))

    def GetPlatformConfig(self, request, context):
        return _channel_platform_pb(self.platform_svc.get())

    def SetPlatformConfig(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "group_chat_disabled" in mask:
            kw["group_chat_disabled"] = request.group_chat_disabled
        return _channel_platform_pb(
            self.platform_svc.set(updated_by=request.updated_by or "", **kw))


class _RunnerDefaultsServicer(runner_defaults_pb2_grpc.RunnerDefaultsServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Get(self, request, context):
        return _rdefaults_pb(self.svc.get())

    def Set(self, request, context):
        mask = set(request.update_mask)
        kw = {}
        if "idle_grace_seconds" in mask:
            kw["idle_grace_seconds"] = request.idle_grace_seconds
        if "min_alive_after_wake_seconds" in mask:
            kw["min_alive_after_wake_seconds"] = request.min_alive_after_wake_seconds
        if "cpu_cores" in mask:
            kw["cpu_cores"] = request.cpu_cores
        if "memory_mb" in mask:
            kw["memory_mb"] = request.memory_mb
        if "storage_gb" in mask:
            kw["storage_gb"] = request.storage_gb
        if "runner_image" in mask:
            kw["runner_image"] = request.runner_image
        if "terminal_resource_percent" in mask:
            kw["terminal_resource_percent"] = request.terminal_resource_percent
        if "terminal_max_sessions" in mask:
            kw["terminal_max_sessions"] = request.terminal_max_sessions
        if "terminal_idle_timeout_seconds" in mask:
            kw["terminal_idle_timeout_seconds"] = request.terminal_idle_timeout_seconds
        if "terminal_max_lifetime_seconds" in mask:
            kw["terminal_max_lifetime_seconds"] = request.terminal_max_lifetime_seconds
        if "terminal_scale_down_grace_seconds" in mask:
            kw["terminal_scale_down_grace_seconds"] = request.terminal_scale_down_grace_seconds
        return _rdefaults_pb(self.svc.set(**kw))


class _HookPolicyServicer(hook_policy_pb2_grpc.HookPolicyServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def List(self, request, context):
        return hook_policy_pb2.HookPolicyList(
            items=[_hook_policy_pb(p) for p in self.svc.list(request.enabled_only)])

    def Get(self, request, context):
        return _hook_policy_pb(self.svc.get(request.id))

    def Upsert(self, request, context):
        record = cv.hook_policy_from_pb(request.policy)
        if record is None:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "hook policy id is required")
        try:
            return _hook_policy_pb(self.svc.upsert(
                record, update_mask=list(request.update_mask), expect=request.expect))
        except ValueError as exc:
            context.abort(grpc.StatusCode.ALREADY_EXISTS, str(exc))
        except LookupError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))

    def Delete(self, request, context):
        try:
            self.svc.delete(request.id)
        except LookupError as exc:
            context.abort(grpc.StatusCode.NOT_FOUND, str(exc))
        except PermissionError as exc:
            context.abort(grpc.StatusCode.FAILED_PRECONDITION, str(exc))
        return common_pb2.Empty()


def _job_pb(d, account_id: str = "") -> scheduler_pb2.Job:
    if d is None:
        return scheduler_pb2.Job()  # empty job_id => not found
    return scheduler_pb2.Job(
        job_id=d.id,
        account_id=account_id,
        name=d.name,
        prompt=d.prompt or "",
        trigger=d.trigger.model_dump_json(),
        job_type=(d.job_config.job_type if d.job_config else "agent_run"),
        job_config=json.dumps(d.job_config.model_dump(mode="json")) if d.job_config else "",
        timezone=d.timezone,
        model=d.model or "",
        status=d.status,
        created_at=_s(d.created_at),
        updated_at=_s(d.updated_at),
    )


def _run_pb(r) -> scheduler_pb2.Run:
    if r is None:
        return scheduler_pb2.Run()  # empty run_id => not found
    return scheduler_pb2.Run(
        run_id=r.run_id,
        job_id=r.job_id or "",
        job_name=r.job_name,
        session_id=r.session_id or "",
        started_at=_s(r.started_at),
        finished_at=_s(r.finished_at),
        status=r.status,
        duration_ms=r.duration_ms or 0,
        is_error=r.is_error,
        error_message=r.error_message or "",
        num_turns=r.num_turns or 0,
        result_summary=r.result_summary or "",
    )


def _defn_from_req(request, job_id: str) -> ScheduledJobDefinition:
    return ScheduledJobDefinition.model_validate({
        "id": job_id,
        "name": request.name,
        "prompt": request.prompt,
        "trigger": json.loads(request.trigger),
        "timezone": request.timezone,
        "status": request.status or "active",
        "model": request.model or None,
        "job_config": json.loads(request.job_config) if request.job_config else None,
    })


class _SchedulerServicer(scheduler_pb2_grpc.SchedulerServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    # jobs
    def CreateJob(self, request, context):
        defn = _defn_from_req(request, request.job_id or uuid.uuid4().hex)
        return _job_pb(self.svc.create_job(request.account_id, defn), request.account_id)

    def GetJob(self, request, context):
        return _job_pb(self.svc.get_job(request.job_id))

    def UpdateJob(self, request, context):
        # Full-definition overwrite (parity with the in-process update_job);
        # update_mask is reserved.
        if self.svc.get_job(request.job_id) is None:
            return scheduler_pb2.Job()
        return _job_pb(self.svc.update_job(request.job_id, _defn_from_req(request, request.job_id)))

    def DeleteJob(self, request, context):
        return common_pb2.BoolValue(value=self.svc.delete_job(request.job_id))

    def ListJobs(self, request, context):
        return scheduler_pb2.JobList(
            jobs=[_job_pb(d, request.account_id) for d in self.svc.list_jobs(request.account_id)])

    def ListActiveJobs(self, request, context):
        return scheduler_pb2.JobList(
            jobs=[_job_pb(d, acct) for acct, d in self.svc.list_active_jobs()])

    def SetJobStatus(self, request, context):
        return _job_pb(self.svc.set_job_status(request.job_id, request.status))

    # runs
    def StartRun(self, request, context):
        kw = dict(
            run_id=request.run_id,
            job_id=request.job_id,
            job_name=request.job_name,
            username="",
            status=request.status or "running",
            session_id=request.session_id or None,
            error_message=request.error_message or None,
        )
        if request.started_at:
            kw["started_at"] = request.started_at
        return _run_pb(self.svc.start_run(request.account_id, JobRunRecord(**kw)))

    def FinishRun(self, request, context):
        rec = JobRunRecord(
            run_id=request.run_id,
            job_id="",
            job_name="",
            username="",
            finished_at=request.finished_at or None,
            status=request.status,
            duration_ms=request.duration_ms or None,
            is_error=request.is_error,
            error_message=request.error_message or None,
            num_turns=request.num_turns or None,
            result_summary=request.result_summary or None,
            session_id=request.session_id or None,
        )
        return _run_pb(self.svc.finish_run(rec))

    def RecordRun(self, request, context):
        rec = cv.run_from_pb(request)
        if rec is None:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "run_id is required")
        return _run_pb(self.svc.record_run(request.account_id, rec))

    def GetRun(self, request, context):
        return _run_pb(self.svc.get_run(request.account_id, request.run_id))

    def GetLatestRun(self, request, context):
        return _run_pb(self.svc.get_latest_run(request.account_id, request.job_id))

    def ListRuns(self, request, context):
        page = self.svc.list_runs(
            request.account_id,
            limit=request.limit or 50,
            before=request.before or None,
            after=request.after or None,
            job_id=request.job_id or None,
            status=request.status or None,
        )
        return scheduler_pb2.RunPage(
            runs=[_run_pb(r) for r in page.runs],
            next_cursor=page.next_cursor or "",
            prev_cursor=page.prev_cursor or "",
            total=-1 if page.total is None else page.total,
        )

    def DeleteRunsBefore(self, request, context):
        return scheduler_pb2.RunIdList(
            run_ids=self.svc.delete_runs_before(request.account_id, request.cutoff_date))

    # fires
    def ClaimJobFire(self, request, context):
        return scheduler_pb2.ClaimFireResponse(
            claimed=self.svc.claim_fire(request.job_id, request.fire_epoch, request.claimed_by))

    def PruneFiresBefore(self, request, context):
        return common_pb2.CountValue(value=self.svc.prune_fires_before(request.cutoff))


class _RegistrationServicer(registration_pb2_grpc.RegistrationServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def Create(self, request, context):
        return _pending_pb(self.svc.create(
            username=request.username,
            password_hash=request.password_hash,
            display_name=request.display_name or None,
            runner_type=request.runner_type or "auto_scale",
            cpu_cores=request.cpu_cores,
            memory_mb=request.memory_mb,
            volume_gb=request.volume_gb,
            note=request.note or None,
        ))

    def List(self, request, context):
        # List never returns the password_hash (RegistrationService.list omits it).
        return registration_pb2.PendingList(
            items=[_pending_pb(p) for p in self.svc.list(request.status or None)])

    def Get(self, request, context):
        return _pending_pb(self.svc.get(request.request_id))

    def SetStatus(self, request, context):
        return _pending_pb(self.svc.set_status(request.request_id, request.status))


def build_server(settings, max_workers: int = 16, repo=None) -> grpc.Server:
    # repo injection is for tests (lets the caller close the pool/file); prod
    # callers pass settings only.
    client = build_inprocess_client(repo if repo is not None else build_repo(settings), settings)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    account_pb2_grpc.add_AccountServiceServicer_to_server(_AccountServicer(client.accounts), server)
    binding_pb2_grpc.add_BindingServiceServicer_to_server(_BindingServicer(client.bindings), server)
    quota_pb2_grpc.add_QuotaServiceServicer_to_server(_QuotaServicer(client.quota), server)
    admin_pb2_grpc.add_AdminServiceServicer_to_server(_AdminServicer(client.admin), server)
    resource_spec_pb2_grpc.add_ResourceSpecServiceServicer_to_server(
        _ResourceSpecServicer(client.resource_specs), server)
    feishu_channel_config_pb2_grpc.add_FeishuChannelConfigServiceServicer_to_server(
        _FeishuChannelConfigServicer(client.feishu_configs, client.channel_platform), server)
    runner_defaults_pb2_grpc.add_RunnerDefaultsServiceServicer_to_server(
        _RunnerDefaultsServicer(client.runner_defaults), server)
    registration_pb2_grpc.add_RegistrationServiceServicer_to_server(
        _RegistrationServicer(client.registrations), server)
    hook_policy_pb2_grpc.add_HookPolicyServiceServicer_to_server(
        _HookPolicyServicer(client.hook_policies), server)
    scheduler_pb2_grpc.add_SchedulerServiceServicer_to_server(
        _SchedulerServicer(client.scheduler), server)
    return server


def serve(settings=None, host: str = "0.0.0.0", port: int = 50051) -> int:
    from priva_common.config import get_settings

    s = settings or get_settings()
    server = build_server(s)
    addr = f"{host}:{port}"
    server.add_insecure_port(addr)
    server.start()
    logger.info("data-spine gRPC serving on {} ({})", addr, describe_store(s))
    server.wait_for_termination()
    return 0
