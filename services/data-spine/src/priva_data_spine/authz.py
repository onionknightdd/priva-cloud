"""gRPC authentication + per-RPC authorization for data-spine.

Before this, the server bound only ``add_insecure_port`` with no interceptors and
every servicer trusted ``request.account_id`` verbatim. Any pod that could reach
:50051 — which included every tenant agent-runner — could enumerate all tenants'
plaintext API keys, flip its own role to ``admin``, read Feishu app secrets,
overwrite another tenant's scheduled job, or ``HookPolicy.Upsert`` an enforced
hook that executes inside other runners.

Every call now carries a signed ``service`` token (priva_common.service_token).
Because only control-plane pods hold the signing key, a caller cannot invent a
role or widen its own scope.

Two trust tiers:

* **control plane** (control-panel / operator / scheduler / channel-connector)
  — each workload has its own explicit method allowlist. Merely holding a signed
  control-plane token does not grant the complete cross-tenant surface.
* **tenant** (agent-runner) — a narrow allowlist, every entry scoped to the one
  account named in the token. Anything absent from the allowlist is denied.

Scoping is enforced per method because the wire shape differs: most requests
carry ``account_id``, but job-addressed RPCs carry only ``job_id`` (and
``FinishRun`` only ``run_id``), so ownership there is resolved by lookup — the
exact gap that made cross-tenant job overwrite possible.

Note this is authorization, not transport security: the channel is still
plaintext, so it remains replayable by anything already on the pod network. mTLS
is the follow-up; a stolen tenant token still only grants that tenant's own
scope, which is the property that matters here.
"""

from __future__ import annotations

import contextvars

import grpc

from priva_common.logging import get_app_logger
from priva_common.service_token import ServicePrincipal, verify_service

logger = get_app_logger(__name__)

_METADATA_KEY = "authorization"
_BEARER = "bearer "

# The authenticated caller, for servicers that want it. Set per-call in the
# interceptor and reset in a finally: gRPC's sync server runs each RPC on a
# thread-pool thread, and the handler runs inline in that same thread, so the
# ContextVar is visible to the servicer without leaking to the next RPC.
current_principal: contextvars.ContextVar[ServicePrincipal | None] = contextvars.ContextVar(
    "current_principal", default=None
)

# Scope kinds for the tenant allowlist.
_NONE = "none"                        # global read; any authenticated caller
_REQ_ACCOUNT = "request_account"      # request.account_id must equal the token's
_RESP_ACCOUNT = "response_account"    # response.account_id must equal the token's
_JOB = "job"                          # request.job_id must belong to the token's account
_RUN = "run"                          # request.run_id must belong to the token's account
_ENSURE_ONLY = "ensure_only"          # own account AND an empty update_mask (read-ish)

# What an agent-runner may call. Derived from the runner's real call sites
# (routers/scheduler_jobs.py, services/scheduled_runs/*, services/hooks/policy.py,
# app.py readyz, deps.py account resolution). Everything else is denied — notably
# AccountService List/Update/Delete/VerifyPassword/FindByApiKey, all of
# FeishuChannelConfigService and RegistrationService, HookPolicy writes, and the
# cross-tenant scheduler surface (ListActiveJobs / ClaimJobFire / StartRun).
TENANT_ACL: dict[str, str] = {
    # health/readiness probes
    "AdminService/Healthz": _NONE,
    "AdminService/Readyz": _NONE,
    # own account resolution (deps.py -> user_store, mcp_tools username lookup)
    "AccountService/Get": _REQ_ACCOUNT,
    "AccountService/GetByUsername": _RESP_ACCOUNT,
    # own quota. Set is allowed ONLY with an empty update_mask, which is how
    # quota.ensure() creates-or-returns the row (services/scheduled_runs/
    # executor.py runs it per scheduled run). A populated mask would let a
    # tenant raise its own tier / concurrency ceiling.
    "QuotaService/Get": _REQ_ACCOUNT,
    "QuotaService/Set": _ENSURE_ONLY,
    # global read-only config the runner renders locally
    "HookPolicyService/List": _NONE,
    "RunnerDefaultsService/Get": _NONE,
    # own scheduled jobs
    "SchedulerService/ListJobs": _REQ_ACCOUNT,
    "SchedulerService/CreateJob": _REQ_ACCOUNT,
    "SchedulerService/GetJob": _JOB,
    "SchedulerService/UpdateJob": _JOB,
    "SchedulerService/DeleteJob": _JOB,
    "SchedulerService/SetJobStatus": _JOB,
    # own run history
    "SchedulerService/ListRuns": _REQ_ACCOUNT,
    "SchedulerService/GetRun": _REQ_ACCOUNT,
    "SchedulerService/GetLatestRun": _REQ_ACCOUNT,
    "SchedulerService/FinishRun": _RUN,
}

# Per-workload control-plane surface, derived from the shipped call sites. This
# is intentionally default-deny: a future RPC or a new caller must be added here
# alongside its call-site tests before data-spine serves it.
CONTROL_PLANE_ACL: dict[str, frozenset[str]] = {
    # Authentication, admin APIs, provisioning and status views under
    # services/control-panel/src/priva_control_panel.
    "control-panel": frozenset({
        "AccountService/GetByUsername",
        "AccountService/List",
        "AccountService/Create",
        "AccountService/Update",
        "AccountService/Delete",
        "AccountService/VerifyPassword",
        "AccountService/FindByApiKey",
        "AccountService/HasUsers",
        "AdminService/Readyz",
        "AdminService/Stats",
        "BindingService/ListBindings",
        "ResourceSpecService/Get",
        "ResourceSpecService/Set",
        "ResourceSpecService/List",
        "RunnerDefaultsService/Get",
        "RunnerDefaultsService/Set",
        "NetworkIsolationService/Get",
        "NetworkIsolationService/Set",
        "RegistrationService/Create",
        "RegistrationService/List",
        "RegistrationService/Get",
        "RegistrationService/SetStatus",
        "HookPolicyService/List",
        "HookPolicyService/Get",
        "HookPolicyService/Upsert",
        "HookPolicyService/Delete",
        "FeishuChannelConfigService/Get",
        "FeishuChannelConfigService/SetUser",
        "FeishuChannelConfigService/SetAdmin",
        "FeishuChannelConfigService/ListEffective",
        "FeishuChannelConfigService/CreateLinkCode",
        "FeishuChannelConfigService/UnbindOwner",
        "FeishuChannelConfigService/GetPlatformConfig",
        "FeishuChannelConfigService/SetPlatformConfig",
        "SchedulerService/ListJobs",
        "SchedulerService/SetJobStatus",
        "SchedulerService/ListRuns",
    }),
    # Network/policy convergence under services/operator/src/priva_operator.
    "operator": frozenset({
        "HookPolicyService/List",
        "RunnerDefaultsService/Get",
        "NetworkIsolationService/Get",
    }),
    # Cross-tenant dispatch and orphan reconciliation under
    # services/scheduler/src/priva_scheduler.
    "scheduler": frozenset({
        "AccountService/Get",
        "AccountService/List",
        "SchedulerService/GetJob",
        "SchedulerService/ListJobs",
        "SchedulerService/ListActiveJobs",
        "SchedulerService/StartRun",
        "SchedulerService/FinishRun",
        "SchedulerService/RecordRun",
        "SchedulerService/GetLatestRun",
        "SchedulerService/ListRuns",
        "SchedulerService/ClaimJobFire",
        "SchedulerService/PruneFiresBefore",
    }),
    # Per-account Feishu connection and chat binding reconciliation under
    # services/channel-connector/src/priva_channel_connector.
    "channel-connector": frozenset({
        "AccountService/Get",
        "BindingService/Bind",
        "BindingService/Rebind",
        "BindingService/ListBindings",
        "BindingService/SetDisplay",
        "FeishuChannelConfigService/Get",
        "FeishuChannelConfigService/SetStatus",
        "FeishuChannelConfigService/ListEffective",
        "FeishuChannelConfigService/GetFeishuSecret",
        "FeishuChannelConfigService/BindOwnerWithCode",
    }),
}


def _short(method: str) -> str:
    """``/priva.dataplane.v1.AccountService/Get`` -> ``AccountService/Get``."""
    return method.lstrip("/").split(".")[-1]


class AuthzInterceptor(grpc.ServerInterceptor):
    def __init__(self, client):
        # The in-process client the servicers use — reused here to resolve
        # ownership for job/run-addressed RPCs.
        self._client = client

    # --- authentication ---------------------------------------------------
    def _principal(self, context) -> ServicePrincipal:
        md = dict(context.invocation_metadata() or ())
        raw = md.get(_METADATA_KEY, "")
        if raw.lower().startswith(_BEARER):
            raw = raw[len(_BEARER):]
        if not raw:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, "missing service token")
        try:
            return verify_service(raw)
        except ValueError as exc:
            context.abort(grpc.StatusCode.UNAUTHENTICATED, f"invalid service token: {exc}")

    # --- authorization ----------------------------------------------------
    def _owns_job(self, account_id: str, job_id: str) -> bool:
        # get_job() does not return the owner, so membership in the caller's own
        # job list IS the ownership test (small n: jobs are per-account).
        try:
            return any(j.id == job_id for j in self._client.scheduler.list_jobs(account_id))
        except Exception:
            logger.warning("job ownership lookup failed for {}/{}", account_id, job_id, exc_info=True)
            return False

    def _owns_run(self, account_id: str, run_id: str) -> bool:
        try:
            return self._client.scheduler.get_run(account_id, run_id) is not None
        except Exception:
            logger.warning("run ownership lookup failed for {}/{}", account_id, run_id, exc_info=True)
            return False

    def _authorize(self, short: str, principal: ServicePrincipal, request, context) -> str:
        """Returns the scope kind so the caller knows whether to check the response."""
        if principal.is_control_plane:
            allowed_methods = CONTROL_PLANE_ACL.get(principal.svc, frozenset())
            if short not in allowed_methods:
                logger.warning("DENY {} for {} (not in workload ACL)", short, principal)
                context.abort(
                    grpc.StatusCode.PERMISSION_DENIED,
                    f"{short} not permitted for this workload",
                )
            return _NONE

        scope = TENANT_ACL.get(short)
        if scope is None:
            logger.warning("DENY {} for {} (not in tenant ACL)", short, principal)
            context.abort(grpc.StatusCode.PERMISSION_DENIED, f"{short} not permitted for this workload")

        account = principal.account_id  # verify_service guarantees this is set
        if scope == _ENSURE_ONLY:
            if getattr(request, "account_id", "") != account:
                logger.warning("DENY {} for {} (account scope)", short, principal)
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "account scope mismatch")
            if list(getattr(request, "update_mask", []) or []):
                logger.warning("DENY {} for {} (write mask not permitted)", short, principal)
                context.abort(grpc.StatusCode.PERMISSION_DENIED,
                              f"{short} may only be called with an empty update_mask")
        elif scope == _REQ_ACCOUNT:
            asked = getattr(request, "account_id", "")
            if asked != account:
                logger.warning("DENY {} for {} (account scope {!r})", short, principal, asked)
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "account scope mismatch")
        elif scope == _JOB:
            if not self._owns_job(account, getattr(request, "job_id", "")):
                logger.warning("DENY {} for {} (job not owned)", short, principal)
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "job does not belong to this account")
        elif scope == _RUN:
            if not self._owns_run(account, getattr(request, "run_id", "")):
                logger.warning("DENY {} for {} (run not owned)", short, principal)
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "run does not belong to this account")
        return scope

    # --- wiring -----------------------------------------------------------
    def intercept_service(self, continuation, handler_call_details):
        handler = continuation(handler_call_details)
        if handler is None:
            return None  # unknown method — leave gRPC's UNIMPLEMENTED path alone

        short = _short(handler_call_details.method)

        if handler.unary_unary is None:
            # The whole v1 surface is unary-unary. A streaming rpc would slip
            # past the wrapper below, so refuse it rather than serve it
            # unauthorized: a future .proto addition must come here first.
            logger.error("refusing streaming rpc {} — not covered by authz", short)

            def _refuse(request, context):
                context.abort(grpc.StatusCode.UNIMPLEMENTED,
                              f"{short} is not served (streaming rpcs are not authorized)")

            return grpc.unary_unary_rpc_method_handler(_refuse)

        behavior = handler.unary_unary

        def wrapper(request, context):
            principal = self._principal(context)
            scope = self._authorize(short, principal, request, context)
            token = current_principal.set(principal)
            try:
                response = behavior(request, context)
            finally:
                current_principal.reset(token)
            # GetByUsername is addressed by name, so ownership can only be
            # judged from what comes back.
            # An all-default reply means "not found" and leaks nothing; aborting
            # on it turned an unknown username into PERMISSION_DENIED, which the
            # runner has no handler for (it expects None) and so 500s on the auth
            # hot path instead of returning a clean 403.
            replied = getattr(response, "account_id", "")
            if scope == _RESP_ACCOUNT and replied and replied != principal.account_id:
                logger.warning("DENY {} for {} (response account mismatch)", short, principal)
                context.abort(grpc.StatusCode.PERMISSION_DENIED, "account scope mismatch")
            return response

        return grpc.unary_unary_rpc_method_handler(
            wrapper,
            request_deserializer=handler.request_deserializer,
            response_serializer=handler.response_serializer,
        )


__all__ = [
    "AuthzInterceptor",
    "CONTROL_PLANE_ACL",
    "TENANT_ACL",
    "current_principal",
]
