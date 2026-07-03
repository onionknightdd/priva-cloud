import { getJSON, sandboxGet, sandboxRead } from '@shared/api/client'

// Per-account readiness + first-page bootstrap (returns { workspace, username }).
// Served by the agent-runner via the gateway; cold-starts wake the sandbox, which
// sandboxGet's fetchWithWake surfaces as the "waking"/"ready" toasts.
export const getAgentHealth = () => sandboxGet('/health')

// Per-user usage overview (stats/heatmap/streaks/model usage). Agent-runtime
// state served by the agent-runner from the account's /workspace PVC.
// sandboxRead: the 183-day heatmap + daily model-token series run past the ~8KB EPP cap.
export const getUserOverview = () => sandboxRead('/user/overview')

export const getUserStats = () => sandboxGet('/user/stats')

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
// sandboxRead: pages carry up to 200 entries with details — can exceed the ~8KB EPP cap.
export const getUserAuditLog = (params = {}) => sandboxRead(`/user/audit?${buildAuditQuery(params)}`)

// Control-plane audit (login/auth/user-mgmt) — served by the control-panel from
// its own store. Merged with the agent-runtime feed client-side so no history is
// lost when both views are shown together.
export const getControlPlaneAudit = (params = {}) => getJSON(`/auth/audit?${buildAuditQuery(params)}`)

// sandboxRead: the timeline carries up to 500 audit entries — well past the ~8KB EPP cap.
export const getUserAnalytics = (params) => {
  const query = new URLSearchParams()
  if (params?.start) query.set('start', params.start)
  if (params?.end) query.set('end', params.end)
  return sandboxRead(`/user/analytics?${query}`)
}
