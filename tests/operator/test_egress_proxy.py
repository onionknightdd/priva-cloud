"""Squid egress proxy: rendered config, injected env, and the converge ordering.

The recurring hazard in this file is that every failure mode of a misconfigured
egress path looks the same to a user — the agent stops producing output and never
returns. The claude CLI has no proxy timeout (measured: `HTTPS_PROXY` pointed at a
dead address gives exit 124 from an external `timeout`, with empty stdout AND empty
stderr for 30s). So a test that only checks "did we write a policy" is not enough;
these check the orderings and selectors that decide whether a live agent hangs.
"""

from __future__ import annotations

import os
import re
import subprocess
from types import SimpleNamespace

import pytest

from priva_common.network_isolation import normalize_egress_domain
from priva_operator import egress_proxy, kube, netpol

NS = "priva-cloud"
_REAL_PROXY_READY = kube.egress_proxy_ready


def _settings():
    return SimpleNamespace(
        kubernetes=SimpleNamespace(
            egress_internal_cidrs=[
                "10.0.0.0/8",
                "172.16.0.0/12",
                "192.168.0.0/16",
                "169.254.0.0/16",
                "127.0.0.0/8",
            ],
            egress_proxy_host="priva-egress-proxy.priva-cloud.svc",
            egress_proxy_port=3128,
            egress_proxy_image="ubuntu/squid:latest",
            egress_no_proxy="localhost,127.0.0.1",
            gateway_name="priva-gateway",
            runner_service_port=8091,
            terminal_service_port=8092,
            wake_timeout_seconds=60,
            runner_image_pull_policy="IfNotPresent",
        ),
        dataspine=SimpleNamespace(
            grpc_dsn="data-spine.priva-cloud.svc.cluster.local:50051"
        ),
        scheduler=SimpleNamespace(api_port=8082),
    )


def _entry(host, port=443):
    return SimpleNamespace(host=host, port=port)


def _iso(mode="allowlist", allowlist=None, **kw):
    base = dict(
        runner_deny_internal=False,
        terminal_deny_internal=False,
        deny_tenant_peers=False,
        egress_mode=mode,
        egress_allowlist=allowlist
        if allowlist is not None
        else [_entry(".anthropic.com"), _entry("pypi.org")],
    )
    base.update(kw)
    return SimpleNamespace(**base)


class _AttrDict(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def _object(value):
    if isinstance(value, dict):
        return _AttrDict({key: _object(item) for key, item in value.items()})
    if isinstance(value, list):
        return [_object(item) for item in value]
    return value


# --- squid.conf ---------------------------------------------------------------


def test_each_entry_gets_its_own_domain_and_port_acl():
    conf = egress_proxy.render_squid_conf(
        _iso(
            allowlist=[
                _entry("api.anthropic.com", 443),
                _entry("mirror.internal", 8443),
            ]
        ),
        _settings(),
    )
    # A single shared port list would be looser than what the UI shows: it would
    # open :8443 for api.anthropic.com too.
    assert "acl priva_dom_0 dstdomain -n api.anthropic.com" in conf
    assert "acl priva_prt_0 port 443" in conf
    assert "http_access allow priva_dom_0 priva_prt_0" in conf
    assert "acl priva_dom_1 dstdomain -n mirror.internal" in conf
    assert "http_access allow priva_dom_1 priva_prt_1" in conf


@pytest.mark.parametrize("port", [0, -1, 65536])
def test_invalid_allowlist_port_is_skipped_instead_of_broadening_access(port):
    conf = egress_proxy.render_squid_conf(
        _iso(allowlist=[_entry("example.com", port)]), _settings()
    )
    assert "# skipped invalid allowlist entry 0" in conf
    assert "http_access allow priva_dom_0" not in conf
    assert "priva_prt_0" not in conf


@pytest.mark.parametrize("port", [1, 65535])
def test_allowlist_port_boundaries_are_rendered(port):
    conf = egress_proxy.render_squid_conf(
        _iso(allowlist=[_entry("example.com", port)]), _settings()
    )
    assert f"acl priva_prt_0 port {port}" in conf
    assert "http_access allow priva_dom_0 priva_prt_0" in conf


def test_domain_acl_disables_ptr_lookup_for_raw_ip_connects():
    conf = egress_proxy.render_squid_conf(
        _iso(allowlist=[_entry(".one.one.one.one", 443)]), _settings()
    )
    # Squid otherwise reverse-resolves CONNECT 1.1.1.1:443 and grants it
    # because that address currently has a matching one.one.one.one PTR.
    assert "acl priva_dom_0 dstdomain -n .one.one.one.one" in conf


def test_empty_allowlist_denies_everything():
    conf = egress_proxy.render_squid_conf(_iso(allowlist=[]), _settings())
    assert "http_access deny all" in conf
    assert "http_access allow" not in conf


def test_private_destination_deny_precedes_every_mode_specific_allow():
    # Squid takes the FIRST matching http_access rule. The private destination
    # deny is deliberately first (DNS-rebinding/confused-deputy defense), while
    # the catch-all deny must remain last.
    conf = egress_proxy.render_squid_conf(
        _iso(allowlist=[_entry("a.example"), _entry("b.example")]), _settings()
    )
    rules = [ln for ln in conf.splitlines() if ln.startswith("http_access")]
    blocked = rules.index("http_access deny priva_blocked_dst")
    first_allow = min(
        i for i, ln in enumerate(rules) if ln.startswith("http_access allow")
    )
    assert blocked < first_allow, rules
    assert rules[-1] == "http_access deny all"


def test_ipv6_literal_is_denied_before_every_mode_specific_allow():
    conf = egress_proxy.render_squid_conf(
        _iso(mode="unrestricted"), _settings()
    )
    rules = [ln for ln in conf.splitlines() if ln.startswith("http_access")]
    ipv6_deny = rules.index("http_access deny priva_ipv6_literal")
    unrestricted_allow = rules.index("http_access allow all")
    acl = next(
        ln for ln in conf.splitlines()
        if ln.startswith("acl priva_ipv6_literal url_regex -i ")
    )
    authority_regex = acl.split(" -i ", 1)[1]
    assert re.search(authority_regex, "[::1]:443")
    assert re.search(authority_regex, "http://[2001:db8::1]:8080/path")
    assert not re.search(
        authority_regex, "https://example.com/path?target=[::1]:443"
    )
    assert ipv6_deny < unrestricted_allow


@pytest.mark.parametrize(
    "mode",
    ["unrestricted", "allowlist", "deny_all", "future-mode"],
)
def test_cache_manager_is_denied_before_every_mode_specific_allow(mode):
    conf = egress_proxy.render_squid_conf(_iso(mode=mode), _settings())
    assert "acl priva_cache_manager proto cache_object" in conf
    assert (
        "acl priva_internal_manager urlpath_regex -i "
        "^/squid-internal-mgr(/|$)"
    ) in conf
    rules = [ln for ln in conf.splitlines() if ln.startswith("http_access")]
    cache_deny = rules.index("http_access deny priva_cache_manager")
    internal_deny = rules.index("http_access deny priva_internal_manager")
    allow_indexes = [
        i for i, rule in enumerate(rules) if rule.startswith("http_access allow")
    ]
    assert all(cache_deny < i and internal_deny < i for i in allow_indexes)


def test_blank_hosts_do_not_emit_a_rule():
    # An empty row in the admin UI must not render `dstdomain ` with no argument —
    # squid refuses to start on that, which takes every agent's egress with it.
    conf = egress_proxy.render_squid_conf(
        _iso(allowlist=[_entry("  "), _entry("pypi.org")]), _settings()
    )
    domain_rules = [
        line for line in conf.splitlines()
        if " dstdomain " in line
    ]
    assert domain_rules == ["acl priva_dom_1 dstdomain -n pypi.org"]


def test_domain_normalizer_rejects_a_newline_at_a_label_boundary():
    # ``re.Pattern.match`` considers ``$`` satisfied immediately before a
    # newline. This payload therefore distinguishes full-label validation from
    # a prefix match while surviving the whole-input ``strip()``.
    with pytest.raises(ValueError, match="not a domain"):
        normalize_egress_domain("good.example\n.evil")


def test_corrupt_allowlist_row_cannot_inject_squid_configuration():
    conf = egress_proxy.render_squid_conf(
        _iso(
            allowlist=[
                _entry("good.example\nhttp_access allow all"),
                _entry("ok.example", 70000),
                SimpleNamespace(host="missing-port.example", port=None),
            ]
        ),
        _settings(),
    )
    assert "good.example" not in conf
    assert "ok.example" not in conf
    assert "missing-port.example" not in conf
    assert "http_access allow all" not in conf
    assert conf.count("skipped invalid allowlist entry") == 3


def test_all_modes_keep_private_destinations_closed():
    for mode in ("unrestricted", "allowlist", "deny_all", "future-mode"):
        conf = egress_proxy.render_squid_conf(_iso(mode=mode), _settings())
        rules = [ln for ln in conf.splitlines() if ln.startswith("http_access")]
        # The invariant is ordering, not a literal first line: nothing may be
        # allowed before private destinations are denied.
        blocked_at = rules.index("http_access deny priva_blocked_dst")
        assert not any(
            rule.startswith("http_access allow") for rule in rules[:blocked_at]
        )
        assert rules[-1] == "http_access deny all"
        if mode == "unrestricted":
            assert "http_access allow all" in rules
        elif mode != "allowlist":
            assert not any(rule.startswith("http_access allow") for rule in rules)


def test_deny_all_refuses_before_any_rule_that_needs_dns():
    """deny_all strips the proxy's own egress, DNS included. A `dst` ACL makes
    Squid resolve the requested name first, so if one runs before the blanket
    deny the client hangs until its own timeout instead of getting a 403 —
    observed on the dev cluster as CONNECT with no response at all.
    """
    conf = egress_proxy.render_squid_conf(_iso(mode="deny_all"), _settings())
    rules = [ln for ln in conf.splitlines() if ln.startswith("http_access")]
    assert rules[0] == "http_access deny all"
    # `dst` is the only ACL class here that forces name resolution.
    resolving = [ln for ln in conf.splitlines() if ln.startswith("acl priva_blocked_dst")]
    assert resolving, "expected the dst ACLs to still be rendered as defence in depth"
    for other in ("unrestricted", "allowlist"):
        other_rules = [
            ln for ln in egress_proxy.render_squid_conf(
                _iso(mode=other), _settings()
            ).splitlines() if ln.startswith("http_access")
        ]
        assert other_rules[0] == "http_access deny priva_blocked_dst", (
            "only deny_all short-circuits; the other modes must still evaluate "
            "the private-destination deny first"
        )


def test_builtin_and_configured_internal_destinations_are_blocked():
    settings = _settings()
    settings.kubernetes.cluster_pod_cidrs = ["10.244.0.0/16"]
    settings.kubernetes.cluster_service_cidrs = ["10.96.0.0/12"]
    settings.kubernetes.cluster_node_cidrs = ["203.10.0.0/16"]
    settings.kubernetes.egress_blocked_cidrs = ["198.18.0.0/15"]
    conf = egress_proxy.render_squid_conf(_iso(mode="unrestricted"), settings)
    for cidr in (
        "10.0.0.0/8",  # RFC1918 / configured legacy field
        "100.64.0.0/10",  # CGNAT + Volcengine metadata 100.96.0.96
        "127.0.0.0/8",  # loopback
        "169.254.0.0/16",  # link-local / common cloud metadata
        "203.10.0.0/16",  # configured node/VPC range, even if publicly numbered
        "198.18.0.0/15",  # opt-in where no synthetic-DNS mapping uses it
        "::/0",  # IPv6 is fail-closed until NetworkPolicy is dual-stack
    ):
        assert f"acl priva_blocked_dst dst {cidr}" in conf


def test_invalid_configured_cidr_fails_render_instead_of_being_ignored():
    settings = _settings()
    settings.kubernetes.cluster_node_cidrs = ["not-a-cidr"]
    with pytest.raises(ValueError, match="invalid blocked egress CIDR"):
        egress_proxy.render_squid_conf(_iso(), settings)


def test_config_is_complete_and_replaces_the_images_own():
    """Regression for three hazards measured against ubuntu/squid:6.13.

    Shipping this as a conf.d FRAGMENT silently turns the proxy into an open
    forward proxy: /etc/squid/conf.d/debian.conf contains `http_access allow
    localnet`, conf.d is included alphabetically so it wins over priva.conf, and
    every pod IP is RFC1918 — i.e. localnet. Nothing errors; the allowlist just
    stops applying.
    """
    conf = egress_proxy.render_squid_conf(_iso(), _settings())
    assert egress_proxy.CONFIG_MOUNT == "/etc/squid/squid.conf"
    # A complete config declares its own listener; a fragment cannot (the base
    # already declares one, and a duplicate is a conflict).
    assert "http_port 3128" in conf
    assert "host_verify_strict on" in conf
    assert "forwarded_for delete" in conf
    assert "via off" in conf
    # Redirecting logs to the container's stdout is FATAL at startup — squid
    # drops to the `proxy` user and cannot open it. Measured: the pod CrashLoops,
    # which presents as every agent hanging with no output.
    assert "/dev/stdout" not in conf and "/dev/stderr" not in conf
    # The pod runs as uid 13 under runAsNonRoot and /run is root-owned 755, so
    # squid's default pid file is FATAL. Measured as CrashLoopBackOff in-cluster.
    assert "pid_filename none" in conf
    assert "cache_mem 32 MB" in conf
    assert "pinger_enable off" in conf


def test_config_digest_rides_on_the_pod_template():
    conf = egress_proxy.render_squid_conf(_iso(), _settings())
    body = egress_proxy.deployment_body(NS, conf, _settings())
    annotations = body["spec"]["template"]["metadata"]["annotations"]
    # On the TEMPLATE, not just the ConfigMap: squid re-reads its config only on
    # reconfigure, so a ConfigMap-only update leaves the running process on the
    # old allowlist while everything reports success.
    digest = annotations[egress_proxy.CONFIG_DIGEST_ANNOTATION]
    assert len(digest) == 64
    # Security tightening must not retain old unrestricted Ready pods when a
    # replacement image/config fails. Recreate trades a short outage for a
    # fail-closed transition.
    assert body["spec"]["strategy"] == {"type": "Recreate"}
    assert body["spec"]["replicas"] >= 2

    # The operator's fast path compares this one annotation. It must therefore
    # cover more than squid.conf, otherwise an image/security manifest update is
    # silently skipped.
    changed = _settings()
    changed.kubernetes.egress_proxy_image = "ubuntu/squid:6.13-patched"
    changed_body = egress_proxy.deployment_body(NS, conf, changed)
    assert (
        changed_body["spec"]["template"]["metadata"]["annotations"][
            egress_proxy.CONFIG_DIGEST_ANNOTATION
        ]
        != digest
    )


def test_proxy_pod_does_not_receive_cluster_credentials_and_has_read_only_root():
    conf = egress_proxy.render_squid_conf(_iso(), _settings())
    pod = egress_proxy.deployment_body(NS, conf, _settings())["spec"]["template"][
        "spec"
    ]
    assert pod["automountServiceAccountToken"] is False
    assert pod["enableServiceLinks"] is False
    assert pod["serviceAccountName"] == "default"
    assert pod["hostNetwork"] is False
    assert pod["hostPID"] is False
    assert pod["hostIPC"] is False
    assert pod["shareProcessNamespace"] is False
    anti_affinity = pod["affinity"]["podAntiAffinity"][
        "preferredDuringSchedulingIgnoredDuringExecution"
    ]
    assert anti_affinity == [{
        "weight": 100,
        "podAffinityTerm": {
            "labelSelector": {"matchLabels": {"app": "egress-proxy"}},
            "topologyKey": "kubernetes.io/hostname",
        },
    }]
    assert pod["securityContext"]["seccompProfile"] == {"type": "RuntimeDefault"}
    container = pod["containers"][0]
    assert container["securityContext"]["allowPrivilegeEscalation"] is False
    assert container["securityContext"]["privileged"] is False
    assert container["securityContext"]["readOnlyRootFilesystem"] is True
    assert container["securityContext"]["capabilities"]["drop"] == ["ALL"]
    startup_command = container["startupProbe"]["exec"]["command"]
    assert "/proc/net/if_inet6" in startup_command[-1]
    assert "command -v awk" in startup_command[-1]
    assert '[ -r "$ipv6_file" ] || exit 1' in startup_command[-1]
    assert '$6 != "lo"' in startup_command[-1]
    assert "addr !~ /^fe[89ab]/" in startup_command[-1]
    assert "addr !~ /^ff/" in startup_command[-1]
    mounts = {mount["mountPath"] for mount in container["volumeMounts"]}
    assert {"/var/log/squid", "/var/spool/squid"} <= mounts
    for volume in pod["volumes"]:
        if volume["name"] in {"logs", "spool"}:
            assert volume["emptyDir"]["sizeLimit"] == "64Mi"


def _ipv6_startup_gate_script() -> str:
    conf = egress_proxy.render_squid_conf(_iso(), _settings())
    container = egress_proxy.deployment_body(NS, conf, _settings())["spec"][
        "template"
    ]["spec"]["containers"][0]
    return container["startupProbe"]["exec"]["command"][-1]


def _run_ipv6_startup_gate(address_file, *, env=None):
    return subprocess.run(
        [
            "/bin/sh",
            "-ec",
            _ipv6_startup_gate_script(),
            "priva-ipv6-gate",
            str(address_file),
        ],
        check=False,
        capture_output=True,
        text=True,
        env=env,
    )


@pytest.mark.parametrize(
    ("addresses", "should_pass"),
    [
        ("", True),
        (
            "00000000000000000000000000000001 01 80 10 80 lo\n",
            True,
        ),
        (
            "fe80000000000000020c29fffe9c409e 02 40 20 80 eth0\n"
            "ff020000000000000000000000000001 02 08 00 80 eth0\n",
            True,
        ),
        (
            "20010db8000000000000000000000001 02 40 00 80 eth0\n",
            False,
        ),
        (
            "fd000000000000000000000000000001 02 40 00 80 eth0\n",
            False,
        ),
    ],
)
def test_ipv6_startup_gate_checks_actual_non_link_local_addresses(
    tmp_path, addresses, should_pass
):
    address_file = tmp_path / "if_inet6"
    address_file.write_text(addresses)
    completed = _run_ipv6_startup_gate(address_file)
    assert (completed.returncode == 0) is should_pass, completed.stderr


def test_ipv6_startup_gate_allows_an_absent_proc_file(tmp_path):
    address_file = tmp_path / "missing-if_inet6"
    assert not address_file.exists()
    assert _run_ipv6_startup_gate(address_file).returncode == 0


def test_ipv6_startup_gate_rejects_an_existing_unreadable_proc_file(tmp_path):
    address_file = tmp_path / "if_inet6"
    address_file.write_text("")
    address_file.chmod(0)
    try:
        if os.access(address_file, os.R_OK):
            pytest.skip("root can bypass the unreadable test fixture's mode bits")
        assert _run_ipv6_startup_gate(address_file).returncode != 0
    finally:
        address_file.chmod(0o600)


def test_ipv6_startup_gate_fails_when_awk_is_missing(tmp_path):
    address_file = tmp_path / "if_inet6"
    address_file.write_text("")
    completed = _run_ipv6_startup_gate(address_file, env={"PATH": ""})
    assert completed.returncode != 0


def test_live_proxy_spec_drift_is_not_hidden_by_a_preserved_digest():
    settings = _settings()
    conf = egress_proxy.render_squid_conf(_iso(), settings)
    desired = egress_proxy.deployment_body(NS, conf, settings)
    digest = desired["spec"]["template"]["metadata"]["annotations"][
        egress_proxy.CONFIG_DIGEST_ANNOTATION
    ]
    assert kube._proxy_deployment_matches(desired, desired, digest)

    # Keep the exact annotation while adding a capability. A digest-presence
    # fast path alone would leave this privilege escalation in place forever.
    drifted = egress_proxy.deployment_body(NS, conf, settings)
    drifted["spec"]["template"]["spec"]["containers"][0]["securityContext"][
        "capabilities"
    ]["add"] = ["NET_ADMIN"]
    assert not kube._proxy_deployment_matches(drifted, desired, digest)

    # Extra executable containers/credential volumes are also owned exactly.
    drifted = egress_proxy.deployment_body(NS, conf, settings)
    drifted["spec"]["template"]["spec"]["containers"].append({
        "name": "sidecar",
        "image": "busybox",
    })
    assert not kube._proxy_deployment_matches(drifted, desired, digest)


def test_repairing_subpath_configmap_forces_a_proxy_replacement(monkeypatch):
    """A ConfigMap rewrite alone never reaches an existing subPath mount."""
    settings = _settings()
    iso = _iso(mode="allowlist")
    conf = egress_proxy.render_squid_conf(iso, settings)
    from priva_common.network_isolation import isolation_intent_digest

    intent = isolation_intent_digest(iso, settings)
    desired_cm = egress_proxy.config_map_body(
        NS, conf, intent_digest=intent
    )
    cm = SimpleNamespace(
        # Simulate an out-of-band wider edit after revision 7. Its annotations
        # remain stale, exactly as they do under `kubectl edit`.
        data={"squid.conf": "http_access allow all\n"},
        metadata=SimpleNamespace(
            resource_version="8",
            labels=dict(desired_cm["metadata"]["labels"]),
            annotations=dict(desired_cm["metadata"]["annotations"]),
        ),
    )

    class Core:
        def read_namespaced_config_map(self, *_, **__):
            return cm

        def replace_namespaced_config_map(self, name, namespace, body, **_):
            cm.data = dict(body["data"])
            cm.metadata.labels = dict(body["metadata"]["labels"])
            cm.metadata.annotations = dict(body["metadata"]["annotations"])
            cm.metadata.resource_version = "9"

    old_body = egress_proxy.deployment_body(
        NS,
        conf,
        settings,
        config_revision="7",
        intent_digest=intent,
    )

    class Existing(dict):
        metadata = SimpleNamespace(resource_version="deployment-rv")

    existing = Existing(old_body)
    replacements = []

    class Apps:
        def read_namespaced_deployment(self, *_, **__):
            return existing

        def replace_namespaced_deployment(self, name, namespace, body, **_):
            replacements.append(body)

    monkeypatch.setattr(kube, "core", lambda: Core())
    monkeypatch.setattr(kube, "apps", lambda: Apps())
    monkeypatch.setattr(kube, "_apply_service", lambda *a: False)

    assert kube.ensure_egress_proxy(
        NS, strict=True, iso=iso, settings=settings
    )
    assert len(replacements) == 1
    annotations = replacements[0]["spec"]["template"]["metadata"][
        "annotations"
    ]
    assert annotations["priva.io/egress-proxy-config-revision"] == "9"


@pytest.mark.parametrize("existing", [False, True])
def test_proxy_deployment_conflict_is_not_treated_as_applied(
    monkeypatch, existing
):
    settings = _settings()
    iso = _iso(mode="allowlist")
    conf = egress_proxy.render_squid_conf(iso, settings)

    class Existing(dict):
        metadata = SimpleNamespace(resource_version="stale-rv")

    stale = Existing(
        egress_proxy.deployment_body(
            NS, conf, settings, config_revision="stale-revision"
        )
    )

    class Apps:
        def read_namespaced_deployment(self, *_, **__):
            if not existing:
                raise kube.client.ApiException(status=404)
            return stale

        def create_namespaced_deployment(self, *_, **__):
            raise kube.client.ApiException(status=409)

        def replace_namespaced_deployment(self, *_, **__):
            raise kube.client.ApiException(status=409)

    monkeypatch.setattr(kube, "apps", lambda: Apps())
    monkeypatch.setattr(kube, "_apply_cm", lambda *a: True)
    monkeypatch.setattr(
        kube, "_verified_config_map_revision", lambda *a: "desired-revision"
    )
    monkeypatch.setattr(kube, "_apply_service", lambda *a: False)

    with pytest.raises(kube.client.ApiException) as conflict:
        kube.ensure_egress_proxy(
            NS, strict=True, iso=iso, settings=settings
        )
    assert conflict.value.status == 409


def test_proxy_service_create_conflict_is_not_treated_as_repaired(monkeypatch):
    class Core:
        def read_namespaced_service(self, *_, **__):
            raise kube.client.ApiException(status=404)

        def create_namespaced_service(self, *_, **__):
            raise kube.client.ApiException(status=409)

    monkeypatch.setattr(kube, "core", lambda: Core())
    with pytest.raises(kube.client.ApiException) as conflict:
        kube._apply_service(
            NS, egress_proxy.service_body(NS, _settings())
        )
    assert conflict.value.status == 409


def test_proxy_reuses_private_registry_pull_secret_when_configured():
    settings = _settings()
    settings.kubernetes.runner_image_pull_secret = "registry-credentials"
    conf = egress_proxy.render_squid_conf(_iso(), settings)
    body = egress_proxy.deployment_body(NS, conf, settings)
    pod = body["spec"]["template"]["spec"]
    assert pod["imagePullSecrets"] == [{"name": "registry-credentials"}]
    without_secret = _settings()
    without_secret_digest = egress_proxy.deployment_body(NS, conf, without_secret)[
        "spec"
    ]["template"]["metadata"]["annotations"][egress_proxy.CONFIG_DIGEST_ANNOTATION]
    assert (
        body["spec"]["template"]["metadata"]["annotations"][
            egress_proxy.CONFIG_DIGEST_ANNOTATION
        ]
        != without_secret_digest
    )


# --- injected env -------------------------------------------------------------


def test_proxy_env_is_stable_in_every_mode():
    rendered = [
        netpol.proxy_env(_iso(mode=mode), _settings())
        for mode in ("unrestricted", "allowlist", "deny_all")
    ]
    assert rendered[0] == rendered[1] == rendered[2]
    env = {e["name"]: e["value"] for e in rendered[0]}
    assert env["HTTPS_PROXY"] == "http://priva-egress-proxy.priva-cloud.svc:3128"
    assert env["https_proxy"] == env["HTTPS_PROXY"]  # clients disagree on case


def test_no_proxy_does_not_grant_a_broad_cluster_dns_bypass():
    env = {e["name"]: e["value"] for e in netpol.proxy_env(_iso(), _settings())}
    # Platform gRPC/scheduler clients opt out explicitly. Tenant tools therefore
    # do not inherit `.svc`/`.cluster.local` as a proxy bypass.
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert env["no_proxy"] == env["NO_PROXY"]


def test_proxy_env_is_deterministic_across_renders():
    # The operator re-renders and replaces the Deployment on every converge; a
    # value that varied per render would restart every dormant runner every 10s.
    s = _settings()
    assert netpol.proxy_env(_iso(), s) == netpol.proxy_env(_iso(), s)


# --- the lazy rollout ---------------------------------------------------------


def test_pod_template_carries_stable_proxy_env_without_a_dead_marker_label():
    base = {"app": "agent-runner"}
    labels, env = kube._proxy_template_bits(_iso(), _settings(), base)
    assert labels == base and env
    labels, env = kube._proxy_template_bits(
        _iso(mode="unrestricted"), _settings(), base
    )
    assert labels == base and env
    # Must not mutate the caller's dict — it is the Deployment's immutable selector.
    assert base == {"app": "agent-runner"}


def test_egress_policy_selector_is_stable_across_modes():
    pols = {
        p["metadata"]["name"]: p
        for p in netpol.build_policies(_iso(runner_deny_internal=True), _settings(), NS)
    }
    selector = pols[netpol.RUNNER_EGRESS]["spec"]["podSelector"]["matchLabels"]
    assert selector == {"app": "agent-runner"}

    pols = {
        p["metadata"]["name"]: p
        for p in netpol.build_policies(
            _iso(mode="unrestricted", runner_deny_internal=True), _settings(), NS
        )
    }
    assert pols[netpol.RUNNER_EGRESS]["spec"]["podSelector"]["matchLabels"] == selector


def test_proxy_cannot_be_used_to_reach_the_cluster():
    from tests.operator.test_network_policies import _permits

    pol = {
        p["metadata"]["name"]: p for p in netpol.build_policies(_iso(), _settings(), NS)
    }[netpol.PROXY_POLICY]
    # The proxy is the one pod allowed out, so it must not also be a way back in:
    # otherwise a tenant just asks it to CONNECT to data-spine.
    assert not _permits(pol, "egress", ip="10.96.0.1", port=443)
    assert not _permits(pol, "egress", ip="10.244.1.7", port=50051)
    assert _permits(pol, "egress", ip="140.82.121.4", port=443)
    assert _permits(pol, "ingress", labels={"app": "agent-runner"}, port=3128)
    assert not _permits(pol, "ingress", labels={"app": "control-panel"}, port=3128)


def _runner_body(iso):
    s = _settings()
    s.kubernetes.runner_uid = 10001
    s.kubernetes.runner_gid = 10001
    s.kubernetes.runner_image = "priva/agent-runner:test"
    s.kubernetes.runner_image_pull_secret = ""
    s.kubernetes.runner_cpu_cores = 2.0
    s.kubernetes.runner_memory_mb = 2048
    s.kubernetes.runner_storage_gb = 10
    s.kubernetes.terminal_resource_percent = 0
    s.kubernetes.runner_tmp_size_limit = "512Mi"
    return kube._deployment_body(
        namespace=NS,
        account_id="acc",
        username="t",
        image="priva/agent-runner:test",
        pull_policy="IfNotPresent",
        settings=s,
        owner={"uid": "x"},
        spec={},
        mount_info=kube.MountInfo(
            kind="shared_pvc_subpath", claim="priva-export", sub_path="acc"
        ),
        defaults=SimpleNamespace(
            cpu_cores=2.0, memory_mb=2048, storage_gb=10, terminal_resource_percent=0
        ),
        iso=iso,
    )


def test_runner_deployment_gets_proxy_env_without_a_dead_marker_label():
    body = _runner_body(_iso())
    env = {
        e["name"]: e.get("value", "")
        for e in body["spec"]["template"]["spec"]["containers"][0]["env"]
    }
    assert env["HTTPS_PROXY"].startswith("http://priva-egress-proxy")
    assert env["NO_PROXY"] == "localhost,127.0.0.1"
    assert "priva.io/egress" not in body["spec"]["template"]["metadata"]["labels"]
    assert "priva.io/egress" not in body["spec"]["selector"]["matchLabels"]
    assert "priva.io/egress" not in body["metadata"]["labels"]


def test_runner_deployment_keeps_proxy_topology_when_mode_is_unrestricted():
    body = _runner_body(_iso(mode="unrestricted"))
    env = {e["name"] for e in body["spec"]["template"]["spec"]["containers"][0]["env"]}
    assert "HTTPS_PROXY" in env
    assert "priva.io/egress" not in body["spec"]["template"]["metadata"]["labels"]


def test_allocation_hash_ignores_the_proxy_so_a_toggle_does_not_restart_everyone():
    # The proxy env is ordinary template state (lands on next restart). Folding it
    # into the hash would invalidate every dormant runner's generation at once, and
    # allocation_hash has eight call sites — one that forgot to pass iso would
    # produce a permanent desired!=applied mismatch, i.e. a restart loop.
    assert (
        _runner_body(_iso())["metadata"]["annotations"]["priva.io/allocation-hash"]
        == _runner_body(_iso(mode="unrestricted"))["metadata"]["annotations"][
            "priva.io/allocation-hash"
        ]
    )


# --- converge ordering --------------------------------------------------------


class _Recorder:
    def __init__(self):
        self.calls: list[str] = []


@pytest.fixture
def ordered(monkeypatch):
    def _wire(iso):
        rec = _Recorder()
        monkeypatch.setattr(
            kube,
            "ensure_egress_proxy",
            lambda ns, **kw: rec.calls.append("proxy") or False,
        )
        monkeypatch.setattr(
            kube,
            "ensure_network_policies",
            lambda ns, **kw: rec.calls.append("policy") or False,
        )
        monkeypatch.setattr("priva_common.config.get_settings", lambda: _settings())
        monkeypatch.setattr(
            "priva_common.dataplane.get_client",
            lambda: SimpleNamespace(network_isolation=SimpleNamespace(get=lambda: iso)),
        )
        return rec

    return _wire


def test_converge_starts_proxy_before_forcing_traffic_at_it(ordered):
    rec = ordered(_iso(mode="allowlist"))
    kube.ensure_isolation(NS)
    assert rec.calls == ["proxy", "policy"]


def test_unrestricted_mode_also_keeps_proxy_before_policy_order(ordered):
    rec = ordered(_iso(mode="unrestricted"))
    kube.ensure_isolation(NS)
    assert rec.calls == ["proxy", "policy"]


def test_proxy_change_quarantines_public_egress_until_exact_rollout_is_ready(
    monkeypatch,
):
    iso = _iso(mode="allowlist")
    settings = _settings()
    calls = []

    monkeypatch.setattr(kube, "egress_proxy_ready", lambda *a, **k: False)
    monkeypatch.setattr(
        kube,
        "quiesce_egress_proxy",
        lambda *a, **k: calls.append("stop") or True,
    )
    monkeypatch.setattr(
        kube,
        "ensure_network_policies",
        lambda _ns, **kw: calls.append(
            f"policy:{kw['iso'].egress_mode}"
        )
        or False,
    )
    monkeypatch.setattr(
        kube,
        "ensure_egress_proxy",
        lambda _ns, **kw: calls.append(
            f"proxy:{kw['iso'].egress_mode}"
        )
        or True,
    )
    monkeypatch.setattr(
        kube,
        "wait_egress_proxy_ready",
        lambda *a, **k: calls.append("wait") or True,
    )

    assert kube.ensure_isolation(NS, strict=True, iso=iso, settings=settings)
    assert calls == [
        "stop",
        "policy:deny_all",
        "proxy:allowlist",
        "wait",
        "policy:allowlist",
    ]


def test_proxy_deployment_write_failure_leaves_quarantine_installed(monkeypatch):
    iso = _iso(mode="allowlist")
    settings = _settings()
    applied_modes = []

    monkeypatch.setattr(kube, "egress_proxy_ready", lambda *a, **k: False)
    monkeypatch.setattr(kube, "quiesce_egress_proxy", lambda *a, **k: True)
    monkeypatch.setattr(
        kube,
        "ensure_network_policies",
        lambda _ns, **kw: applied_modes.append(kw["iso"].egress_mode)
        or True,
    )
    monkeypatch.setattr(
        kube,
        "ensure_egress_proxy",
        lambda *_a, **_k: (_ for _ in ()).throw(
            RuntimeError("deployment update failed")
        ),
    )

    with pytest.raises(RuntimeError, match="deployment update failed"):
        kube.ensure_isolation(NS, strict=True, iso=iso, settings=settings)
    assert applied_modes == ["deny_all"]


def test_proxy_rollout_timeout_leaves_quarantine_installed(monkeypatch):
    iso = _iso(mode="unrestricted")
    settings = _settings()
    applied_modes = []

    monkeypatch.setattr(kube, "egress_proxy_ready", lambda *a, **k: False)
    monkeypatch.setattr(kube, "quiesce_egress_proxy", lambda *a, **k: True)
    monkeypatch.setattr(
        kube,
        "ensure_network_policies",
        lambda _ns, **kw: applied_modes.append(kw["iso"].egress_mode)
        or True,
    )
    monkeypatch.setattr(kube, "ensure_egress_proxy", lambda *a, **k: True)
    monkeypatch.setattr(
        kube, "wait_egress_proxy_ready", lambda *a, **k: False
    )

    with pytest.raises(RuntimeError, match="did not become Ready"):
        kube.ensure_isolation(NS, strict=True, iso=iso, settings=settings)
    assert applied_modes == ["deny_all"]


def test_proxy_readiness_binds_configmap_revision_sha_intent_and_live_spec(monkeypatch):
    settings = _settings()
    conf = egress_proxy.render_squid_conf(_iso(), settings)
    sha = egress_proxy.config_sha256(conf)
    intent = "intent-1"
    cm = SimpleNamespace(
        data={egress_proxy.CONFIG_KEY: conf},
        metadata=SimpleNamespace(
            resource_version="revision-1",
            annotations={
                egress_proxy.PROXY_CONFIG_SHA256_ANNOTATION: sha,
                egress_proxy.ISOLATION_INTENT_ANNOTATION: intent,
            },
        ),
    )
    deployment_body = egress_proxy.deployment_body(
        NS,
        conf,
        settings,
        config_revision="revision-1",
        intent_digest=intent,
    )
    deployment_body["metadata"]["generation"] = 2
    deployment_body["status"] = {
        "observed_generation": 2,
        "updated_replicas": 2,
        "ready_replicas": 2,
        "available_replicas": 2,
    }
    deployment = _object(deployment_body)
    service = egress_proxy.service_body(NS, settings)
    monkeypatch.setattr(
        kube,
        "apps",
        lambda: SimpleNamespace(
            read_namespaced_deployment=lambda *_, **__: deployment
        ),
    )
    monkeypatch.setattr(
        kube,
        "core",
        lambda: SimpleNamespace(
            read_namespaced_config_map=lambda *_, **__: cm,
            read_namespaced_service=lambda *_, **__: service,
        ),
    )

    assert _REAL_PROXY_READY(
        NS,
        expected_intent=intent,
        expected_config_sha=sha,
        settings=settings,
    )
    # An out-of-band image change retains every generation annotation. Readiness
    # must still close the wake gate until the operator repairs the workload.
    deployment.spec.template.spec.containers[0].image = "attacker/proxy:latest"
    assert not _REAL_PROXY_READY(
        NS,
        expected_intent=intent,
        expected_config_sha=sha,
        settings=settings,
    )
    deployment.spec.template.spec.containers[0].image = (
        settings.kubernetes.egress_proxy_image
    )
    service["spec"]["ports"][0]["nodePort"] = 31280
    assert not _REAL_PROXY_READY(
        NS,
        expected_intent=intent,
        expected_config_sha=sha,
        settings=settings,
    )
    service["spec"]["ports"][0].pop("nodePort")
    cm.data[egress_proxy.CONFIG_KEY] += "\nhttp_access allow all\n"
    assert not _REAL_PROXY_READY(
        NS,
        expected_intent=intent,
        expected_config_sha=sha,
        settings=settings,
    )


def test_policy_quarantine_failure_never_restarts_or_reconfigures_proxy(monkeypatch):
    calls = []
    monkeypatch.setattr(kube, "egress_proxy_ready", lambda *a, **k: False)
    monkeypatch.setattr(
        kube,
        "quiesce_egress_proxy",
        lambda *a, **k: calls.append("stop") or True,
    )
    monkeypatch.setattr(
        kube,
        "ensure_network_policies",
        lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("second policy write failed")
        ),
    )
    monkeypatch.setattr(
        kube,
        "ensure_egress_proxy",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("proxy must remain stopped")
        ),
    )

    with pytest.raises(RuntimeError, match="second policy write failed"):
        kube.ensure_isolation(
            NS, strict=True, iso=_iso(), settings=_settings()
        )
    assert calls == ["stop"]


def test_quiesce_proxy_scales_to_zero_and_waits_until_old_pods_are_gone(
    monkeypatch,
):
    calls = []
    deployment = SimpleNamespace(spec=SimpleNamespace(replicas=2))

    class Apps:
        def read_namespaced_deployment(self, *_, **__):
            return deployment

        def patch_namespaced_deployment(self, name, namespace, body, **_):
            calls.append(("scale", name, namespace, body))
            deployment.spec.replicas = 0

    pod_snapshots = [
        [SimpleNamespace(metadata=SimpleNamespace(name="proxy-old"))],
        [],
    ]

    class Core:
        def list_namespaced_pod(self, namespace, *, label_selector, **_):
            calls.append(("pods", namespace, label_selector))
            return SimpleNamespace(items=pod_snapshots.pop(0))

        def delete_namespaced_pod(self, name, namespace, **kwargs):
            calls.append(("delete", name, namespace, kwargs))

    monkeypatch.setattr(kube, "apps", lambda: Apps())
    monkeypatch.setattr(kube, "core", lambda: Core())
    monkeypatch.setattr(kube.time, "sleep", lambda *_: None)

    assert kube.quiesce_egress_proxy(NS, timeout=1) is True
    assert calls[0] == (
        "scale",
        egress_proxy.NAME,
        NS,
        {"spec": {"replicas": 0}},
    )
    assert ("delete", "proxy-old", NS, {
        "grace_period_seconds": 0,
        "_request_timeout": kube._KUBE_REQUEST_TIMEOUT,
    }) in calls
    assert calls[-1] == ("pods", NS, "app=egress-proxy")


def test_isolation_is_fail_soft_when_data_spine_is_down(monkeypatch):
    def boom():
        raise RuntimeError("unreachable")

    monkeypatch.setattr("priva_common.dataplane.get_client", boom)
    assert kube.ensure_isolation(NS) is False
    with pytest.raises(RuntimeError):
        kube.ensure_isolation(NS, strict=True)


# --- the in-cluster channel must not depend on the proxy env ------------------


def test_dataspine_channel_opts_out_of_proxy_env(monkeypatch):
    """The data-spine gRPC channel must be immune to the proxy variables.

    Measured in a runner pod: grpc's C-core reads only the LOWERCASE forms —
    `http_proxy` and `https_proxy` hijack the channel, `NO_PROXY` does NOT rescue
    it, only `no_proxy` does. So a tidy-up that keeps just the uppercase names
    (what most guides recommend) would kill data-spine for every tenant, and the
    symptom would look like a data-spine outage rather than a proxy misconfig.

    enable_http_proxy=0 removes the dependency: verified reachable with all four
    variables pointed at a dead address and no no_proxy at all.
    """
    import grpc

    from priva_common.dataplane import grpc_client

    captured = {}
    real = grpc.insecure_channel

    def spy(target, options=None, *a, **kw):
        captured["options"] = dict(options or [])
        return real(target, options=options, *a, **kw)

    monkeypatch.setattr(grpc, "insecure_channel", spy)
    grpc_client._cache.clear()
    grpc_client.build_grpc_client(
        SimpleNamespace(dataspine=SimpleNamespace(grpc_dsn="data-spine.example:50051"))
    )
    assert captured["options"].get("grpc.enable_http_proxy") == 0
