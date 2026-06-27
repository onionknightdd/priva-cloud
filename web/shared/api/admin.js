import { getJSON, postJSON, putJSON, deleteJSON } from './client'

export const listUsers = () => getJSON('/admin/users')
export const createUser = (data) => postJSON('/admin/users', data)
export const updateUser = (username, data) => putJSON(`/admin/users/${encodeURIComponent(username)}`, data)
export const deleteUser = (username) => deleteJSON(`/admin/users/${encodeURIComponent(username)}`)

export const getPendingRegistrations = () => getJSON('/admin/pending-registrations')
export const approvePendingUser = (requestId) => postJSON(`/admin/pending-registrations/${encodeURIComponent(requestId)}/approve`)
export const rejectPendingUser = (requestId) => postJSON(`/admin/pending-registrations/${encodeURIComponent(requestId)}/reject`)

export const getFleet = () => getJSON('/admin/fleet')
export const getGatewayMetrics = () => getJSON('/admin/gateway-metrics')
export const getResourceUsage = () => getJSON('/admin/resource-usage')
export const getSystemHealth = () => getJSON('/admin/system-health')
export const getAuditLog = (params = {}) => {
  const query = new URLSearchParams()
  if (params.limit != null) query.set('limit', params.limit)
  if (params.before) query.set('before', params.before)
  if (params.after) query.set('after', params.after)
  if (params.action) query.set('action', params.action)
  if (params.actor) query.set('actor', params.actor)
  if (params.target) query.set('target', params.target)
  if (params.start) query.set('start', params.start)
  if (params.end) query.set('end', params.end)
  if (params.session_id) query.set('session_id', params.session_id)
  return getJSON(`/admin/audit?${query}`)
}

export const getPresetPrompt = () => getJSON('/admin/presetprompt')
export const updatePresetPrompt = (data) => putJSON('/admin/presetprompt', data)

export const getCliPath = () => getJSON('/admin/clipath')
export const updateCliPath = (data) => putJSON('/admin/clipath', data)

export const getHistoryRetention = () => getJSON('/admin/history-retention')
export const updateHistoryRetention = (data) => putJSON('/admin/history-retention', data)

export const getRetryableTools = () => getJSON('/admin/retryable-tools')
export const updateRetryableTools = (data) => putJSON('/admin/retryable-tools', data)

export const getRiskyTools = () => getJSON('/admin/risky-tools')
export const updateRiskyTools = (data) => putJSON('/admin/risky-tools', data)

export const getSensitivePatterns = () => getJSON('/admin/sensitive-patterns')
export const updateSensitivePatterns = (data) => putJSON('/admin/sensitive-patterns', data)

export const getPlugins = () => getJSON('/admin/system/plugin')
export const updatePlugin = (id, data) => putJSON(`/admin/system/plugin/${encodeURIComponent(id)}`, data)

// Agent Runner Sandbox: platform-wide global defaults every account inherits unless
// it has a per-account override. CPU crosses the wire as millicores (digit-only UI).
export const getRunnerDefaults = () => getJSON('/admin/runner-defaults')
export const updateRunnerDefaults = (data) => putJSON('/admin/runner-defaults', data)
export const getRunnerImages = () => getJSON('/admin/runner-images')

// Per-account: served by the account's own agent-runner pod (gateway routes the
// /api/pty prefix to it). Not admin-gated — each user configures their own pod.
export const getPtyConfig = () => getJSON('/pty/config')
export const updatePtyConfig = (data) => putJSON('/pty/config', data)
export const getPtyFeature = () => getJSON('/pty/feature')
