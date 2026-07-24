"""gRPC transport — a DataplaneClient backed by the generated v1 stubs.

Selected when ``dataspine.transport == 'grpc'`` (factory.get_client). Each store
wraps a stub on one shared insecure channel (alpha: plaintext in-cluster; mTLS
deferred) and converts proto ↔ DTO so callers see the same Protocols as the
in-process transport. The client is cached per-DSN so get_client() is cheap.

All nine domains are full, scheduler included (Phase 4a).
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from priva_common.dataplane import converters as cv
from priva_common.dataplane.client import UNSET, DataplaneClient, RunPage

if TYPE_CHECKING:
    from priva_common.config import Settings

_cache: dict[str, DataplaneClient] = {}


def build_grpc_client(settings: "Settings") -> DataplaneClient:
    dsn = settings.dataspine.grpc_dsn or "127.0.0.1:50051"
    cached = _cache.get(dsn)
    if cached is not None:
        return cached

    import grpc

    from priva_common.dataplane.v1 import (
        account_pb2,
        account_pb2_grpc,
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

    channel = grpc.insecure_channel(dsn)

    class _Accounts:
        def __init__(self):
            self._s = account_pb2_grpc.AccountServiceStub(channel)

        def get(self, account_id):
            return cv.user_from_pb(self._s.Get(common_pb2.AccountRef(account_id=account_id)))

        def get_by_username(self, username):
            return cv.user_from_pb(self._s.GetByUsername(account_pb2.UsernameRef(username=username)))

        def list(self):
            return [cv.user_from_pb(a) for a in self._s.List(common_pb2.Empty()).accounts]

        def create(self, username, password="", role="user", agent_runner_type="auto_scale",
                   password_hash=None):
            try:
                return cv.user_from_pb(
                    self._s.Create(account_pb2.CreateAccountRequest(
                        username=username, password=password, role=role,
                        agent_runner_type=agent_runner_type or "auto_scale",
                        password_hash=password_hash or ""))
                )
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                    raise ValueError(exc.details()) from exc
                raise

        def update(self, account_id, *, password=None, role=None, api_key=UNSET,
                   status=None, agent_runner_type=None, feishu_user_id=UNSET, feishu_display_name=UNSET):
            req = account_pb2.UpdateAccountRequest(account_id=account_id)
            mask: list[str] = []
            if password is not None:
                req.password = password
                mask.append("password")
            if role is not None:
                req.role = role
                mask.append("role")
            if status is not None:
                req.status = status
                mask.append("status")
            if agent_runner_type is not None:
                req.agent_runner_type = agent_runner_type
                mask.append("agent_runner_type")
            if api_key is not UNSET:
                req.api_key = api_key or ""  # "" => clear
                mask.append("api_key")
            if feishu_user_id is not UNSET:
                req.feishu_user_id = feishu_user_id or ""
                mask.append("feishu_user_id")
            if feishu_display_name is not UNSET:
                req.feishu_display_name = feishu_display_name or ""
                mask.append("feishu_display_name")
            req.update_mask.extend(mask)
            try:
                return cv.user_from_pb(self._s.Update(req))
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    raise ValueError(exc.details()) from exc
                raise

        def delete(self, account_id):
            self._s.Delete(common_pb2.AccountRef(account_id=account_id))

        def verify_password(self, username, password):
            return self._s.VerifyPassword(
                account_pb2.VerifyPasswordRequest(username=username, password=password)
            ).value

        def find_by_api_key(self, api_key):
            if not api_key:
                return None
            return cv.user_from_pb(self._s.FindByApiKey(account_pb2.ApiKeyRequest(api_key=api_key)))

        def count_admins(self):
            return self._s.CountAdmins(common_pb2.Empty()).value

        def find_by_feishu_user_id(self, feishu_user_id):
            return cv.user_from_pb(self._s.FindByFeishuUserId(account_pb2.FeishuRef(feishu_user_id=feishu_user_id)))

        def has_users(self):
            return self._s.HasUsers(common_pb2.Empty()).value

    class _Quota:
        def __init__(self):
            self._s = quota_pb2_grpc.QuotaServiceStub(channel)

        def get(self, account_id):
            return cv.quota_from_pb(self._s.Get(common_pb2.AccountRef(account_id=account_id)))

        def ensure(self, account_id):
            # Set with an empty mask ensures the row exists and returns it.
            return cv.quota_from_pb(self._s.Set(quota_pb2.SetQuotaRequest(account_id=account_id)))

        def set(self, account_id, *, tier=None, max_concurrent_sessions=None, idle_grace_seconds=None):
            req = quota_pb2.SetQuotaRequest(account_id=account_id)
            mask: list[str] = []
            if tier is not None:
                req.tier = tier
                mask.append("tier")
            if max_concurrent_sessions is not None:
                req.max_concurrent_sessions = max_concurrent_sessions
                mask.append("max_concurrent_sessions")
            if idle_grace_seconds is not None:
                req.idle_grace_seconds = idle_grace_seconds
                mask.append("idle_grace_seconds")
            req.update_mask.extend(mask)
            return cv.quota_from_pb(self._s.Set(req))

    class _Bindings:
        def __init__(self):
            self._s = binding_pb2_grpc.BindingServiceStub(channel)

        def bind(self, account_id, session_uuid, feishu_chat_id=None):
            return cv.binding_from_pb(self._s.Bind(
                binding_pb2.BindRequest(account_id=account_id, session_uuid=session_uuid or "",
                                        feishu_chat_id=feishu_chat_id or "")))

        def rebind(self, account_id, session_uuid, feishu_chat_id=None):
            return cv.binding_from_pb(self._s.Rebind(
                binding_pb2.RebindRequest(account_id=account_id, session_uuid=session_uuid or "",
                                          feishu_chat_id=feishu_chat_id or "")))

        def set_display(self, account_id, feishu_chat_id, *, chat_type="", chat_name=""):
            return cv.binding_from_pb(self._s.SetDisplay(
                binding_pb2.SetBindingDisplayRequest(
                    account_id=account_id, feishu_chat_id=feishu_chat_id or "",
                    chat_type=chat_type or "", chat_name=chat_name or "")))

        def claim_first_run_im(self, binding_id):
            return self._s.ClaimFirstRunIM(binding_pb2.BindingRef(binding_id=binding_id)).value

        def get_binding(self, binding_id):
            return cv.binding_from_pb(self._s.GetBinding(binding_pb2.BindingRef(binding_id=binding_id)))

        def list_bindings(self, account_id):
            return [cv.binding_from_pb(b) for b in
                    self._s.ListBindings(common_pb2.AccountRef(account_id=account_id)).bindings]

    class _Admin:
        def __init__(self):
            self._s = admin_pb2_grpc.AdminServiceStub(channel)

        def healthz(self):
            return self._s.Healthz(common_pb2.Empty()).status

        def readyz(self):
            r = self._s.Readyz(common_pb2.Empty())
            return r.ready, r.detail

        def stats(self):
            r = self._s.Stats(common_pb2.Empty())
            return {"accounts": r.accounts, "jobs": r.jobs, "runs": r.runs, "backend": r.backend}

    class _ResourceSpecs:
        def __init__(self):
            self._s = resource_spec_pb2_grpc.ResourceSpecServiceStub(channel)

        def get(self, account_id):
            return cv.resource_spec_from_pb(self._s.Get(common_pb2.AccountRef(account_id=account_id)))

        def set(self, account_id, *, cpu_cores=None, memory_mb=None, volume_gb=None):
            req = resource_spec_pb2.SetResourceSpecRequest(account_id=account_id)
            mask: list[str] = []
            if cpu_cores is not None:
                req.cpu_cores = cpu_cores
                mask.append("cpu_cores")
            if memory_mb is not None:
                req.memory_mb = memory_mb
                mask.append("memory_mb")
            if volume_gb is not None:
                req.volume_gb = volume_gb
                mask.append("volume_gb")
            req.update_mask.extend(mask)
            return cv.resource_spec_from_pb(self._s.Set(req))

        def list(self):
            return [cv.resource_spec_from_pb(r) for r in self._s.List(common_pb2.Empty()).specs]

    class _FeishuConfigs:
        def __init__(self):
            self._s = feishu_channel_config_pb2_grpc.FeishuChannelConfigServiceStub(channel)

        def get(self, account_id):
            return cv.feishu_config_from_pb(
                self._s.Get(common_pb2.AccountRef(account_id=account_id)))

        def set_user(self, account_id, *, app_id=None, app_secret=UNSET, user_enabled=None,
                     single_chat_access_mode=None, allowed_union_ids=None, welcome_message=None,
                     reject_message=None, model=None, max_queue_size=None,
                     enable_permission_feedback=None, feedback_timeout_seconds=None,
                     domain=None, group_chat_enabled=None, updated_by=""):
            req = feishu_channel_config_pb2.SetFeishuUserConfigRequest(
                account_id=account_id, updated_by=updated_by)
            mask: list[str] = []
            if app_id is not None:
                req.app_id = app_id
                mask.append("app_id")
            if app_secret is not UNSET:
                req.app_secret = app_secret or ""  # "" (in mask) => clear
                mask.append("app_secret")
            if user_enabled is not None:
                req.user_enabled = user_enabled
                mask.append("user_enabled")
            if single_chat_access_mode is not None:
                req.single_chat_access_mode = single_chat_access_mode
                mask.append("single_chat_access_mode")
            if allowed_union_ids is not None:
                req.allowed_union_ids = allowed_union_ids
                mask.append("allowed_union_ids")
            if welcome_message is not None:
                req.welcome_message = welcome_message
                mask.append("welcome_message")
            if reject_message is not None:
                req.reject_message = reject_message
                mask.append("reject_message")
            if model is not None:
                req.model = model
                mask.append("model")
            if max_queue_size is not None:
                req.max_queue_size = max_queue_size
                mask.append("max_queue_size")
            if enable_permission_feedback is not None:
                req.enable_permission_feedback = enable_permission_feedback
                mask.append("enable_permission_feedback")
            if feedback_timeout_seconds is not None:
                req.feedback_timeout_seconds = feedback_timeout_seconds
                mask.append("feedback_timeout_seconds")
            if domain is not None:
                req.domain = domain
                mask.append("domain")
            if group_chat_enabled is not None:
                req.group_chat_enabled = group_chat_enabled
                mask.append("group_chat_enabled")
            req.update_mask.extend(mask)
            return cv.feishu_config_from_pb(self._s.SetUser(req))

        def set_admin(self, account_id, *, admin_disabled=None, updated_by=""):
            req = feishu_channel_config_pb2.SetFeishuAdminConfigRequest(
                account_id=account_id, updated_by=updated_by)
            mask: list[str] = []
            if admin_disabled is not None:
                req.admin_disabled = admin_disabled
                mask.append("admin_disabled")
            req.update_mask.extend(mask)
            return cv.feishu_config_from_pb(self._s.SetAdmin(req))

        def set_status(self, account_id, *, conn_status=None, last_error_code=None,
                       last_error_message=None, last_connected_at=None):
            req = feishu_channel_config_pb2.SetFeishuStatusRequest(account_id=account_id)
            mask: list[str] = []
            if conn_status is not None:
                req.conn_status = conn_status
                mask.append("conn_status")
            if last_error_code is not None:
                req.last_error_code = last_error_code
                mask.append("last_error_code")
            if last_error_message is not None:
                req.last_error_message = last_error_message
                mask.append("last_error_message")
            if last_connected_at is not None:
                req.last_connected_at = last_connected_at
                mask.append("last_connected_at")
            req.update_mask.extend(mask)
            return cv.feishu_config_from_pb(self._s.SetStatus(req))

        def list(self):
            return [cv.feishu_config_from_pb(r)
                    for r in self._s.List(common_pb2.Empty()).configs]

        def list_effective(self):
            return [cv.feishu_config_from_pb(r)
                    for r in self._s.ListEffective(common_pb2.Empty()).configs]

        def get_secret(self, account_id):
            return cv.feishu_secret_from_pb(
                self._s.GetFeishuSecret(common_pb2.AccountRef(account_id=account_id)))

        def create_link_code(self, account_id):
            r = self._s.CreateLinkCode(common_pb2.AccountRef(account_id=account_id))
            return r.code, r.expires_at

        def bind_owner_with_code(self, account_id, code, union_id, open_id):
            return self._s.BindOwnerWithCode(feishu_channel_config_pb2.BindOwnerRequest(
                account_id=account_id, code=code, union_id=union_id, open_id=open_id)).ok

        def unbind_owner(self, account_id, *, updated_by=""):
            return cv.feishu_config_from_pb(self._s.UnbindOwner(feishu_channel_config_pb2.UnbindOwnerRequest(
                account_id=account_id, updated_by=updated_by)))

    class _ChannelPlatform:
        def __init__(self):
            self._s = feishu_channel_config_pb2_grpc.FeishuChannelConfigServiceStub(channel)

        def get(self):
            return cv.channel_platform_from_pb(self._s.GetPlatformConfig(common_pb2.Empty()))

        def set(self, *, group_chat_disabled=None, updated_by=""):
            req = feishu_channel_config_pb2.SetChannelPlatformConfigRequest(updated_by=updated_by)
            mask: list[str] = []
            if group_chat_disabled is not None:
                req.group_chat_disabled = group_chat_disabled
                mask.append("group_chat_disabled")
            req.update_mask.extend(mask)
            return cv.channel_platform_from_pb(self._s.SetPlatformConfig(req))

    class _RunnerDefaults:
        def __init__(self):
            self._s = runner_defaults_pb2_grpc.RunnerDefaultsServiceStub(channel)

        def get(self):
            return cv.runner_defaults_from_pb(self._s.Get(common_pb2.Empty()))

        def set(self, *, idle_grace_seconds=None, min_alive_after_wake_seconds=None,
                cpu_cores=None, memory_mb=None, storage_gb=None,
                terminal_resource_percent=None, terminal_max_sessions=None,
                terminal_idle_timeout_seconds=None, terminal_max_lifetime_seconds=None,
                terminal_scale_down_grace_seconds=None):
            req = runner_defaults_pb2.SetRunnerDefaultsRequest()
            mask: list[str] = []
            if idle_grace_seconds is not None:
                req.idle_grace_seconds = idle_grace_seconds
                mask.append("idle_grace_seconds")
            if min_alive_after_wake_seconds is not None:
                req.min_alive_after_wake_seconds = min_alive_after_wake_seconds
                mask.append("min_alive_after_wake_seconds")
            if cpu_cores is not None:
                req.cpu_cores = cpu_cores
                mask.append("cpu_cores")
            if memory_mb is not None:
                req.memory_mb = memory_mb
                mask.append("memory_mb")
            if storage_gb is not None:
                req.storage_gb = storage_gb
                mask.append("storage_gb")
            if terminal_resource_percent is not None:
                req.terminal_resource_percent = terminal_resource_percent
                mask.append("terminal_resource_percent")
            if terminal_max_sessions is not None:
                req.terminal_max_sessions = terminal_max_sessions
                mask.append("terminal_max_sessions")
            if terminal_idle_timeout_seconds is not None:
                req.terminal_idle_timeout_seconds = terminal_idle_timeout_seconds
                mask.append("terminal_idle_timeout_seconds")
            if terminal_max_lifetime_seconds is not None:
                req.terminal_max_lifetime_seconds = terminal_max_lifetime_seconds
                mask.append("terminal_max_lifetime_seconds")
            if terminal_scale_down_grace_seconds is not None:
                req.terminal_scale_down_grace_seconds = terminal_scale_down_grace_seconds
                mask.append("terminal_scale_down_grace_seconds")
            req.update_mask.extend(mask)
            return cv.runner_defaults_from_pb(self._s.Set(req))

    class _Registrations:
        def __init__(self):
            self._s = registration_pb2_grpc.RegistrationServiceStub(channel)

        def create(self, *, username, password_hash, display_name=None, runner_type="auto_scale",
                   cpu_cores=1.0, memory_mb=2048, volume_gb=1, note=None):
            return cv.pending_from_pb(self._s.Create(registration_pb2.CreatePendingRequest(
                username=username, password_hash=password_hash, display_name=display_name or "",
                runner_type=runner_type or "auto_scale", cpu_cores=cpu_cores, memory_mb=memory_mb,
                volume_gb=volume_gb, note=note or "")))

        def get_open_by_username(self, username):
            # Server has no by-username RPC; filter the pending list (small set).
            for p in self._s.List(registration_pb2.StatusRef(status="pending")).items:
                if p.username == username:
                    return cv.pending_from_pb(p)
            return None

        def list(self, status=None):
            return [cv.pending_from_pb(p) for p in
                    self._s.List(registration_pb2.StatusRef(status=status or "")).items]

        def get(self, request_id):
            return cv.pending_from_pb(self._s.Get(registration_pb2.PendingRef(request_id=request_id)))

        def set_status(self, request_id, status):
            return cv.pending_from_pb(self._s.SetStatus(
                registration_pb2.SetStatusRequest(request_id=request_id, status=status)))

    class _HookPolicies:
        def __init__(self):
            self._s = hook_policy_pb2_grpc.HookPolicyServiceStub(channel)

        @staticmethod
        def _to_pb(p):
            return hook_policy_pb2.HookPolicy(
                id=p.id, hook_type=p.hook_type, name=p.name, description=p.description,
                events=list(p.events), matcher=p.matcher, timeout_seconds=p.timeout_seconds,
                interpreter=p.interpreter, script_body=p.script_body,
                content_hash=p.content_hash, url=p.url, headers_json=p.headers_json,
                allowed_env_vars=list(p.allowed_env_vars), mcp_server=p.mcp_server,
                mcp_tool=p.mcp_tool, enabled=p.enabled, enforced=p.enforced,
                default_on=p.default_on, predefined=p.predefined,
                seed_version=p.seed_version, target=p.target, updated_by=p.updated_by,
                enforced_events=list(p.enforced_events),
            )

        def list(self, enabled_only=False):
            resp = self._s.List(hook_policy_pb2.ListHookPoliciesRequest(enabled_only=enabled_only))
            return [cv.hook_policy_from_pb(p) for p in resp.items]

        def get(self, policy_id):
            return cv.hook_policy_from_pb(self._s.Get(hook_policy_pb2.HookPolicyRef(id=policy_id)))

        def upsert(self, policy, *, update_mask=None, expect=""):
            req = hook_policy_pb2.UpsertHookPolicyRequest(
                policy=self._to_pb(policy), update_mask=update_mask or [], expect=expect)
            try:
                return cv.hook_policy_from_pb(self._s.Upsert(req))
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.ALREADY_EXISTS:
                    raise ValueError(exc.details()) from exc
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    raise LookupError(exc.details()) from exc
                raise

        def delete(self, policy_id):
            try:
                self._s.Delete(hook_policy_pb2.HookPolicyRef(id=policy_id))
            except grpc.RpcError as exc:
                if exc.code() == grpc.StatusCode.NOT_FOUND:
                    raise LookupError(exc.details()) from exc
                if exc.code() == grpc.StatusCode.FAILED_PRECONDITION:
                    raise PermissionError(exc.details()) from exc
                raise

    def _dt(v) -> str:
        # datetime | ISO str | None → wire string
        if v is None:
            return ""
        return v if isinstance(v, str) else v.isoformat()

    class _Scheduler:
        def __init__(self):
            self._s = scheduler_pb2_grpc.SchedulerServiceStub(channel)

        # jobs -----------------------------------------------------------
        @staticmethod
        def _defn_fields(defn) -> dict:
            return dict(
                name=defn.name,
                prompt=defn.prompt or "",
                trigger=defn.trigger.model_dump_json(),
                job_type=(defn.job_config.job_type if defn.job_config else "agent_run"),
                job_config=json.dumps(defn.job_config.model_dump(mode="json")) if defn.job_config else "",
                timezone=defn.timezone,
                model=defn.model or "",
                status=defn.status,
            )

        def create_job(self, account_id, defn):
            return cv.job_from_pb(self._s.CreateJob(scheduler_pb2.CreateJobRequest(
                account_id=account_id, job_id=defn.id, **self._defn_fields(defn))))

        def get_job(self, job_id):
            return cv.job_from_pb(self._s.GetJob(scheduler_pb2.JobRef(job_id=job_id)))

        def update_job(self, job_id, defn):
            return cv.job_from_pb(self._s.UpdateJob(scheduler_pb2.UpdateJobRequest(
                job_id=job_id, **self._defn_fields(defn))))

        def delete_job(self, job_id):
            return self._s.DeleteJob(scheduler_pb2.JobRef(job_id=job_id)).value

        def list_jobs(self, account_id):
            return [cv.job_from_pb(j) for j in
                    self._s.ListJobs(common_pb2.AccountRef(account_id=account_id)).jobs]

        def list_active_jobs(self):
            return [(j.account_id, cv.job_from_pb(j)) for j in
                    self._s.ListActiveJobs(common_pb2.Empty()).jobs]

        def set_job_status(self, job_id, status):
            return cv.job_from_pb(self._s.SetJobStatus(
                scheduler_pb2.SetJobStatusRequest(job_id=job_id, status=status)))

        # runs -----------------------------------------------------------
        def start_run(self, account_id, record):
            return cv.run_from_pb(self._s.StartRun(scheduler_pb2.StartRunRequest(
                run_id=record.run_id, job_id=record.job_id or "", job_name=record.job_name,
                account_id=account_id, session_id=record.session_id or "",
                started_at=_dt(record.started_at), status=record.status or "running",
                error_message=record.error_message or "")))

        def finish_run(self, record):
            return cv.run_from_pb(self._s.FinishRun(scheduler_pb2.FinishRunRequest(
                run_id=record.run_id, finished_at=_dt(record.finished_at),
                status=record.status, duration_ms=record.duration_ms or 0,
                is_error=record.is_error, error_message=record.error_message or "",
                num_turns=record.num_turns or 0, result_summary=record.result_summary or "",
                session_id=record.session_id or "")))

        def record_run(self, account_id, record):
            return cv.run_from_pb(self._s.RecordRun(scheduler_pb2.Run(
                run_id=record.run_id, job_id=record.job_id or "", job_name=record.job_name,
                account_id=account_id, session_id=record.session_id or "",
                started_at=_dt(record.started_at), finished_at=_dt(record.finished_at),
                status=record.status, duration_ms=record.duration_ms or 0,
                is_error=record.is_error, error_message=record.error_message or "",
                num_turns=record.num_turns or 0, result_summary=record.result_summary or "")))

        def get_run(self, account_id, run_id):
            return cv.run_from_pb(self._s.GetRun(
                scheduler_pb2.RunRef(account_id=account_id, run_id=run_id)))

        def get_latest_run(self, account_id, job_id):
            return cv.run_from_pb(self._s.GetLatestRun(
                scheduler_pb2.LatestRunRef(account_id=account_id, job_id=job_id)))

        def list_runs(self, account_id, *, limit=50, before=None, after=None,
                      job_id=None, status=None):
            resp = self._s.ListRuns(scheduler_pb2.ListRunsRequest(
                account_id=account_id, limit=limit, before=before or "",
                after=after or "", job_id=job_id or "", status=status or ""))
            return RunPage(
                runs=[cv.run_from_pb(r) for r in resp.runs],
                next_cursor=resp.next_cursor or None,
                prev_cursor=resp.prev_cursor or None,
                total=None if resp.total < 0 else resp.total,
            )

        def delete_runs_before(self, account_id, cutoff_date):
            return list(self._s.DeleteRunsBefore(scheduler_pb2.DeleteRunsBeforeRequest(
                account_id=account_id, cutoff_date=cutoff_date)).run_ids)

        # fires ----------------------------------------------------------
        def claim_fire(self, job_id, fire_epoch, claimed_by):
            return self._s.ClaimJobFire(scheduler_pb2.ClaimFireRequest(
                job_id=job_id, fire_epoch=int(fire_epoch), claimed_by=claimed_by)).claimed

        def prune_fires_before(self, cutoff):
            return self._s.PruneFiresBefore(scheduler_pb2.PruneFiresRequest(cutoff=cutoff)).value

    client = DataplaneClient(
        accounts=_Accounts(),
        bindings=_Bindings(),
        quota=_Quota(),
        scheduler=_Scheduler(),
        admin=_Admin(),
        resource_specs=_ResourceSpecs(),
        runner_defaults=_RunnerDefaults(),
        registrations=_Registrations(),
        hook_policies=_HookPolicies(),
        feishu_configs=_FeishuConfigs(),
        channel_platform=_ChannelPlatform(),
    )
    _cache[dsn] = client
    return client
