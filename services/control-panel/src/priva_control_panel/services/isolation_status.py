"""Live tenant-isolation state, read from the cluster.

The panel this feeds used to state, as present-tense fact, that Terminal could
not reach data-spine, PostgreSQL, Redis or Runner pods — while the four policies
that would have made it true were not installed, and the control was rendered
greyed out (which reads as "fixed platform behaviour", the strongest possible
framing). There was no channel at all between the sentence and the cluster: the
value was a client-side literal that was never fetched and never saved.

So everything here is measured, and each fact is reported separately, because
they fail independently:

  desired   what the admin set (data-spine)
  applied   whether the operator actually wrote the object (drift shows an
            operator that is down, or missing RBAC — otherwise invisible)
  enforced  whether the CNI drops the packet. NOT knowable from any API; it has
            to be measured by sending one, so it is cached by
            deploy/checks/networkpolicy-cni.sh and reported with its timestamp.
"""

from __future__ import annotations

import hashlib
import json

from priva_common import network_isolation as ni
from priva_common.config import get_settings
from priva_common.logging import get_app_logger

logger = get_app_logger(__name__)

UNKNOWN = "unknown"
_UNAVAILABLE = object()


def _networking():
    from kubernetes import client

    from ..provisioner import _load
    _load()
    return client.NetworkingV1Api()


def _present_policies(
    namespace: str, expected_intent: str
) -> tuple[dict[str, bool], list[str]] | None:
    """Applied policy map plus additional policies which can widen the union.

    None is deliberately distinct from the empty set: "no policies" and "we are
    not allowed to ask" must not render identically, or a missing RBAC rule shows
    up as a confident claim that nothing is in force.
    """
    from kubernetes import client
    try:
        policies = _networking().list_namespaced_network_policy(namespace).items
        out: dict[str, bool] = {}
        managed = {}
        conflicts: list[str] = []
        operator_names = {
            ni.RUNNER_EGRESS,
            ni.TERMINAL_EGRESS,
            ni.RUNNER_INGRESS,
            ni.TERMINAL_INGRESS,
            ni.PROXY_POLICY,
        }
        for policy in policies:
            labels = policy.metadata.labels or {}
            name = policy.metadata.name
            out[name] = False
            spec = policy.spec
            if not isinstance(spec, dict):
                spec = client.ApiClient().sanitize_for_serialization(spec)
            if name in operator_names and all(
                labels.get(k) == v for k, v in ni.MANAGED_LABELS.items()
            ):
                managed[name] = (policy, spec)
            elif (
                name not in operator_names
                and name not in ni.LEGACY_POLICIES
                and ni.policy_may_widen_tenant_runtime(spec)
            ):
                conflicts.append(name)

        # The operator stamps a digest of the COMPLETE desired spec set on every
        # member. Recompute it from the API response: merely finding the right
        # name plus a non-empty annotation would report an out-of-band edited
        # policy as applied forever while the operator is down.
        if managed:
            actual_digest = hashlib.sha256(
                json.dumps(
                    [managed[name][1] for name in sorted(managed)],
                    sort_keys=True,
                ).encode()
            ).hexdigest()[:16]
            for name, (policy, _) in managed.items():
                out[name] = (
                    (policy.metadata.annotations or {}).get(
                        ni.POLICY_SET_DIGEST_ANNOTATION
                    )
                    == actual_digest
                    and (policy.metadata.annotations or {}).get(
                        ni.ISOLATION_INTENT_ANNOTATION
                    )
                    == expected_intent
                )
        return out, sorted(conflicts)
    except client.ApiException as exc:
        logger.warning("cannot list NetworkPolicies ({}) — isolation status degraded",
                       exc.status)
        return None
    except Exception:
        logger.warning("cannot list NetworkPolicies", exc_info=True)
        return None


def _proxy_health(namespace: str, expected_intent: str) -> dict:
    from kubernetes import client

    from ..provisioner import _apps, _core

    try:
        cm = _core().read_namespaced_config_map(
            f"{ni.PROXY_DEPLOYMENT}-config", namespace
        )
    except client.ApiException as exc:
        if exc.status == 404:
            cm = None
        else:
            cm = _UNAVAILABLE
    except Exception:
        cm = _UNAVAILABLE

    try:
        d = _apps().read_namespaced_deployment(ni.PROXY_DEPLOYMENT, namespace)
    except client.ApiException as exc:
        if exc.status == 404:
            return {
                "present": False,
                "ready": 0,
                "desired": 0,
                "applied": False,
            }
        return {
            "present": None,
            "ready": 0,
            "desired": 0,
            "applied": None,
        }
    except Exception:
        return {
            "present": None,
            "ready": 0,
            "desired": 0,
            "applied": None,
        }
    desired = int(d.spec.replicas or 0)
    generation = int(d.metadata.generation or 0)
    observed = int(d.status.observed_generation or 0)
    ready = min(
        int(d.status.ready_replicas or 0),
        int(d.status.updated_replicas or 0),
        int(d.status.available_replicas or 0),
    ) if observed >= generation else 0

    applied: bool | None
    if cm is _UNAVAILABLE:
        applied = None
    elif cm is None:
        applied = False
    else:
        conf = (cm.data or {}).get("squid.conf") or ""
        actual_sha = hashlib.sha256(conf.encode()).hexdigest()
        cm_annotations = cm.metadata.annotations or {}
        template_annotations = d.spec.template.metadata.annotations or {}
        revision = str(cm.metadata.resource_version or "")
        applied = bool(
            conf
            and revision
            and cm_annotations.get(ni.PROXY_CONFIG_SHA256_ANNOTATION)
            == actual_sha
            and cm_annotations.get(ni.ISOLATION_INTENT_ANNOTATION)
            == expected_intent
            and template_annotations.get(ni.PROXY_CONFIG_SHA256_ANNOTATION)
            == actual_sha
            and template_annotations.get(ni.PROXY_CONFIG_REVISION_ANNOTATION)
            == revision
            and template_annotations.get(ni.ISOLATION_INTENT_ANNOTATION)
            == expected_intent
        )
    return {
        "present": True,
        # "Ready" in this status means serving the desired generation. A
        # physically Ready pod with a stale/wider subPath config must be red in
        # the panel, not reported as healthy.
        "ready": ready if applied else 0,
        "desired": desired,
        "applied": applied,
    }


def _enforcement(namespace: str) -> dict:
    """The cached verdict of the functional probe. Never guessed from the CNI's
    name — that guess is what previously judged an enforcing kindnet as
    non-enforcing and drove everyone onto the escape hatch that deleted the
    policies."""
    from kubernetes import client

    from ..provisioner import _core
    api = _core()
    try:
        cm = api.read_namespaced_config_map(ni.FACTS_CONFIG_MAP, namespace)
    except client.ApiException as exc:
        if exc.status != 404:
            logger.warning("cannot read {} ({})", ni.FACTS_CONFIG_MAP, exc.status)
        return {"state": UNKNOWN, "checked_at": None, "cni": None}
    except Exception:
        return {"state": UNKNOWN, "checked_at": None, "cni": None}
    data = cm.data or {}
    checked_at = data.get(ni.FACT_CHECKED_AT) or None
    address_family = data.get(ni.FACT_ADDRESS_FAMILY) or None
    recorded_cluster_uid = data.get(ni.FACT_CLUSTER_UID) or None
    try:
        current_cluster_uid = str(
            getattr(api.read_namespace("kube-system").metadata, "uid", "") or ""
        )
    except Exception:
        current_cluster_uid = ""
    aggregate = data.get(ni.FACT_ENFORCED, UNKNOWN)
    ingress = data.get("networkPolicyIngressEnforced")
    egress = data.get("networkPolicyEgressEnforced")
    fresh = ni.probe_fact_is_fresh(
        data,
        int(get_settings().kubernetes.network_policy_probe_max_age_seconds),
    )
    identity_matches = bool(
        current_cluster_uid
        and recorded_cluster_uid == current_cluster_uid
    )
    if (
        data.get(ni.FACT_PROBE_VERSION) != ni.PROBE_VERSION
        or address_family != "ipv4"
        or not identity_matches
        or not fresh
    ):
        state = UNKNOWN
    elif aggregate == "true" and ingress == "true" and egress == "true":
        state = "true"
    elif aggregate == "false" or ingress == "false" or egress == "false":
        state = "false"
    else:
        state = UNKNOWN
    return {
        "state": state if state in ("true", "false") else UNKNOWN,
        "checked_at": checked_at,
        "cni": data.get(ni.FACT_CNI) or None,
        "address_family": address_family,
        "cluster_uid": recorded_cluster_uid,
        "stale": bool(checked_at and not fresh),
    }


# key -> (policy names that must ALL be present for the boundary to be in force)
_BOUNDARY_POLICIES = {
    "runner_deny_internal": (ni.RUNNER_EGRESS,),
    "terminal_deny_internal": (ni.TERMINAL_EGRESS,),
    "deny_tenant_peers": (ni.RUNNER_INGRESS, ni.TERMINAL_INGRESS),
}


def collect(record) -> dict:
    """Live status for the admin Isolation panel, given the desired settings."""
    settings = get_settings()
    namespace = settings.kubernetes.namespace_tenants
    expected_intent = ni.isolation_intent_digest(record, settings)
    snapshot = _present_policies(namespace, expected_intent)
    if snapshot is None:
        present = None
        conflicts = None
    elif isinstance(snapshot, tuple):
        present, conflicts = snapshot
    else:
        # Compatibility for lightweight callers which monkeypatch the older
        # mapping-only helper.
        present = snapshot
        conflicts = []
    proxy = _proxy_health(namespace, expected_intent)
    restricted_egress = record.egress_mode != "unrestricted"
    has_conflict = bool(conflicts)

    def applied(names, *, managed=True) -> bool | None:
        if present is None:
            return None
        if isinstance(present, set):  # compatibility with lightweight callers/tests
            return all(n in present for n in names)
        return all(n in present and (not managed or present[n]) for n in names)

    boundaries = []
    for key, names in _BOUNDARY_POLICIES.items():
        desired = bool(getattr(record, key))
        # The egress policies double as the allowlist enforcement point, so a
        # restricted mode renders them even with the deny flag off. Reporting
        # "applied" for a boundary the admin did not ask for would be misleading.
        boundaries.append({
            "key": key,
            "desired": desired,
            "applied": (
                False if desired and has_conflict
                else applied(names) if desired
                else None
            ),
        })

    boundaries.append({
        "key": "postgres",
        "desired": True,          # hand-applied, not switchable from here
        "applied": applied((ni.POSTGRES_POLICY,), managed=False),
    })
    boundaries.append({
        "key": "egress",
        "desired": restricted_egress,
        "applied": (
            (False if has_conflict else (
                applied((
                    ni.RUNNER_EGRESS,
                    ni.TERMINAL_EGRESS,
                    ni.PROXY_POLICY,
                ))
                and proxy["applied"] is True
            ))
            if restricted_egress
            else None
        ),
    })

    return {
        "enforcement": _enforcement(namespace),
        "boundaries": boundaries,
        "proxy": {**proxy, "required": True},
        "legacy_present": (None if present is None
                           else sorted(n for n in ni.LEGACY_POLICIES if n in present)),
        "conflicting_policies": conflicts,
    }
