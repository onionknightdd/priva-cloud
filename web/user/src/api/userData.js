import { getJSON } from '@shared/api/client'

// Per-account readiness + first-page bootstrap (returns { workspace, username }).
// Served by the agent-runner via the gateway; cold-starts wake the sandbox, which
// getJSON's fetchWithWake surfaces as the "waking"/"ready" toasts.
export const getAgentHealth = () => getJSON('/health')

// Per-user usage overview (stats/heatmap/streaks/model usage). Agent-runtime
// state served by the agent-runner from the account's /workspace PVC.
export const getUserOverview = () => getJSON('/user/overview')

export const getUserStats = () => getJSON('/user/stats')

function buildAuditQuery(params = {}) {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', params.limit)
  if (params.before) query.set('before', params.before)
  if (params.after) query.set('after', params.after)
  if (params.action) query.set('action', params.action)
  if (params.target) query.set('target', params.target)
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.session_id) query.set('session_id', params.session_id)
  return query.toString()
}

// Agent-runtime audit (runs, skills, tools, hooks, sessions) — served by the
// agent-runner from the account's PVC.
export const getUserAuditLog = (params = {}) => getJSON(`/user/audit?${buildAuditQuery(params)}`)

// Control-plane audit (login/auth/user-mgmt) — served by the control-panel from
// its own store. Merged with the agent-runtime feed client-side so no history is
// lost when both views are shown together.
export const getControlPlaneAudit = (params = {}) => getJSON(`/auth/audit?${buildAuditQuery(params)}`)

export const getUserAnalytics = (params) => {
  const query = new URLSearchParams()
  if (params?.start) query.set('start', params.start)
  if (params?.end) query.set('end', params.end)
  return getJSON(`/user/analytics?${query}`)
}
