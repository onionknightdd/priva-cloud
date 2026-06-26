#!/usr/bin/env bash
# One-time migration for the "global runner defaults" model (admin Sandbox panel).
#
# Before this feature the control-panel STAMPED image/resources/storageGb/idle into every
# AgentTenant CR at creation, so an inherited value and an admin-chosen override looked
# identical. The operator now reads "field absent on the CR" as "inherit the global
# default". This strips those four inheritable fields from existing CRs so they fall back
# to the defaults — i.e. become inheriting accounts.
#
# ASSUMPTION (true for the current dev accounts, all created with the env defaults): none
# of the existing CRs carry a *genuine* per-account override. If one did, re-set it
# afterwards from the admin Accounts view. Idempotent — re-running is harmless.
#
# JSON merge patch (RFC 7386): a key set to null is REMOVED from the object.
set -euo pipefail
NS="${NS:-priva-cloud}"

names=$(kubectl -n "$NS" get agenttenants -o jsonpath='{.items[*].metadata.name}' 2>/dev/null || true)
if [ -z "$names" ]; then
  echo "no AgentTenants in namespace $NS — nothing to migrate"
  exit 0
fi

for name in $names; do
  echo "==> stripping inheritable fields from agenttenant/$name"
  kubectl -n "$NS" patch agenttenant "$name" --type=merge \
    -p '{"spec":{"image":null,"resources":null,"storageGb":null,"idle":null}}'
done

echo "==> done. Accounts now inherit the global runner defaults; per-account overrides"
echo "    can be re-applied from the admin Accounts view."
