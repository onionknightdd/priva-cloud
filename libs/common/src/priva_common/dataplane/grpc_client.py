"""gRPC transport — a DataplaneClient backed by the generated v1 stubs.

Selected when ``dataspine.transport == 'grpc'`` (factory.get_client). Each store
wraps a stub on one shared insecure channel (alpha: plaintext in-cluster; mTLS
deferred) and converts proto ↔ DTO so callers see the same Protocols as the
in-process transport. The client is cached per-DSN so get_client() is cheap.

scheduler is intentionally NOT served over gRPC this phase (deferred to Phase 4);
its store raises NotImplementedError. accounts/quota/admin/bindings/secrets are full.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from priva_common.dataplane import converters as cv
from priva_common.dataplane.client import UNSET, DataplaneClient

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
        quota_pb2,
        quota_pb2_grpc,
        registration_pb2,
        registration_pb2_grpc,
        resource_spec_pb2,
        resource_spec_pb2_grpc,
        secret_pb2,
        secret_pb2_grpc,
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
                binding_pb2.BindRequest(account_id=account_id, session_uuid=session_uuid,
                                        feishu_chat_id=feishu_chat_id or "")))

        def rebind(self, account_id, session_uuid, feishu_chat_id=None):
            return cv.binding_from_pb(self._s.Rebind(
                binding_pb2.RebindRequest(account_id=account_id, session_uuid=session_uuid,
                                          feishu_chat_id=feishu_chat_id or "")))

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
            return {"accounts": r.accounts, "jobs": r.jobs, "runs": r.runs}

    class _Secrets:
        def __init__(self):
            self._s = secret_pb2_grpc.SecretServiceStub(channel)

        def put(self, account_id, bundle):
            import json
            return cv.secret_from_pb(self._s.PutSecret(
                secret_pb2.PutSecretRequest(account_id=account_id, bundle=json.dumps(bundle or {}))))

        def get(self, account_id):
            return cv.secret_from_pb(self._s.GetSecret(secret_pb2.GetSecretRequest(account_id=account_id)))

        def list_account_ids(self):
            return list(self._s.ListSecrets(common_pb2.Empty()).account_ids)

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

    class _SchedulerDeferred:
        """scheduler is not served over gRPC this phase (Phase 4)."""

        def __getattr__(self, name):
            def _unsupported(*_a, **_k):
                raise NotImplementedError(f"scheduler.{name} over gRPC is deferred to Phase 4")
            return _unsupported

    client = DataplaneClient(
        accounts=_Accounts(),
        bindings=_Bindings(),
        quota=_Quota(),
        scheduler=_SchedulerDeferred(),
        admin=_Admin(),
        secrets=_Secrets(),
        resource_specs=_ResourceSpecs(),
        registrations=_Registrations(),
    )
    _cache[dsn] = client
    return client
