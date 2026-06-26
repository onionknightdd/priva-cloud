"""data-spine gRPC server — exposes the in-process services over the wire.

When CP / agent-runner / operator run as separate pods (no shared filesystem),
they reach durable state through this single-writer server instead of composing
SQLite in-process. Sync grpc server over a thread pool: the SqliteRepo already
serializes writes behind one lock, so a thread pool is correct and simple.

Builds proto messages FROM the boundary records (the mirror of dataplane.converters).
scheduler is not served this phase (deferred); its stubs return UNIMPLEMENTED.
"""

from __future__ import annotations

import json
from concurrent import futures

import grpc

from priva_common.dataplane.v1 import (
    account_pb2,
    account_pb2_grpc,
    admin_pb2,
    admin_pb2_grpc,
    binding_pb2,
    binding_pb2_grpc,
    common_pb2,
    quota_pb2,
    quota_pb2_grpc,
    registration_pb2,
    registration_pb2_grpc,
    resource_spec_pb2,
    resource_spec_pb2_grpc,
    runner_defaults_pb2,
    runner_defaults_pb2_grpc,
    secret_pb2,
    secret_pb2_grpc,
)
from priva_common.logging import get_app_logger

from .service import build_inprocess_client, build_repo

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
        session_uuid=b.session_uuid,
        first_run_done=b.first_run_done,
        feishu_chat_id=b.feishu_chat_id or "",
        bound_at=b.bound_at or "",
        rebound_at=b.rebound_at or "",
    )


def _secret_pb(rec) -> secret_pb2.Secret:
    if rec is None:
        return secret_pb2.Secret()
    return secret_pb2.Secret(
        account_id=rec.account_id,
        bundle=json.dumps(rec.bundle),
        generation=rec.generation,
        updated_at=rec.updated_at or "",
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


def _rdefaults_pb(r) -> runner_defaults_pb2.RunnerDefaults:
    return runner_defaults_pb2.RunnerDefaults(
        idle_grace_seconds=r.idle_grace_seconds,
        min_alive_after_wake_seconds=r.min_alive_after_wake_seconds,
        cpu_cores=r.cpu_cores,
        memory_mb=r.memory_mb,
        storage_gb=r.storage_gb,
        runner_image=r.runner_image,
        updated_at=r.updated_at or "",
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
        return _binding_pb(self.svc.bind(request.account_id, request.session_uuid, request.feishu_chat_id or None))

    def Rebind(self, request, context):
        return _binding_pb(self.svc.rebind(request.account_id, request.session_uuid, request.feishu_chat_id or None))

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
        return admin_pb2.StatsResponse(accounts=s.get("accounts", 0), jobs=s.get("jobs", 0), runs=s.get("runs", 0))


class _SecretServicer(secret_pb2_grpc.SecretServiceServicer):
    def __init__(self, svc):
        self.svc = svc

    def PutSecret(self, request, context):
        try:
            bundle = json.loads(request.bundle) if request.bundle else {}
        except (ValueError, TypeError):
            bundle = {}
        return _secret_pb(self.svc.put(request.account_id, bundle))

    def GetSecret(self, request, context):
        return _secret_pb(self.svc.get(request.account_id))

    def ListSecrets(self, request, context):
        return secret_pb2.SecretAccountList(account_ids=self.svc.list_account_ids())


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
        return _rdefaults_pb(self.svc.set(**kw))


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


def build_server(settings, max_workers: int = 16) -> grpc.Server:
    client = build_inprocess_client(build_repo(settings), settings)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    account_pb2_grpc.add_AccountServiceServicer_to_server(_AccountServicer(client.accounts), server)
    binding_pb2_grpc.add_BindingServiceServicer_to_server(_BindingServicer(client.bindings), server)
    quota_pb2_grpc.add_QuotaServiceServicer_to_server(_QuotaServicer(client.quota), server)
    admin_pb2_grpc.add_AdminServiceServicer_to_server(_AdminServicer(client.admin), server)
    secret_pb2_grpc.add_SecretServiceServicer_to_server(_SecretServicer(client.secrets), server)
    resource_spec_pb2_grpc.add_ResourceSpecServiceServicer_to_server(
        _ResourceSpecServicer(client.resource_specs), server)
    runner_defaults_pb2_grpc.add_RunnerDefaultsServiceServicer_to_server(
        _RunnerDefaultsServicer(client.runner_defaults), server)
    registration_pb2_grpc.add_RegistrationServiceServicer_to_server(
        _RegistrationServicer(client.registrations), server)
    return server


def serve(settings=None, host: str = "0.0.0.0", port: int = 50051) -> int:
    from priva_common.config import get_settings

    s = settings or get_settings()
    server = build_server(s)
    addr = f"{host}:{port}"
    server.add_insecure_port(addr)
    server.start()
    logger.info("data-spine gRPC serving on {} (sqlite={})", addr, s.dataspine.sqlite_path)
    server.wait_for_termination()
    return 0
