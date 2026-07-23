import { controlPlaneGet, getJSON, postJSON, putJSON, deleteJSON } from './client'

export const listUsers = () => getJSON('/admin/users')
export const createUser = (data) => postJSON('/admin/users', data)
export const updateUser = (username, data) => putJSON(`/admin/users/${encodeURIComponent(username)}`, data)
export const deleteUser = (username) => deleteJSON(`/admin/users/${encodeURIComponent(username)}`)

// Feishu bot config — admin can READ status + toggle the kill-switch (admin_disabled)
// only; credentials are edited by the user themselves. The secret is never returned.
export const getUserFeishuConfig = (username) => getJSON(`/admin/users/${encodeURIComponent(username)}/feishu-config`)
export const updateUserFeishuConfig = (username, data) => putJSON(`/admin/users/${encodeURIComponent(username)}/feishu-config`, data)

export const getPendingRegistrations = () => getJSON('/admin/pending-registrations')
export const approvePendingUser = (requestId) => postJSON(`/admin/pending-registrations/${encodeURIComponent(requestId)}/approve`)
export const rejectPendingUser = (requestId) => postJSON(`/admin/pending-registrations/${encodeURIComponent(requestId)}/reject`)

export const getFleet = () => getJSON('/admin/fleet')
export const restartAccountPod = (accountId) => postJSON(`/admin/accounts/${encodeURIComponent(accountId)}/restart-pod`)
export const shutdownAccountRunner = (accountId) => postJSON(`/admin/accounts/${encodeURIComponent(accountId)}/shutdown`)
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

// Configurations ▸ Channels: platform-wide channel settings — today the global
// group-chat kill switch (composes with each user's own opt-in).
export const getChannelPlatformConfig = () => getJSON('/admin/channel-platform')
export const updateChannelPlatformConfig = (data) => putJSON('/admin/channel-platform', data)

// Hook Policy (Agent Runner Sandbox → Runtime): admin-stored hooks delivered to
// every account's agent-runner at its next session build. create saves disabled;
// deleting a predefined (seeded) row is rejected (409). validate is compile-only.
export const listHookPolicies = () => getJSON('/admin/hook-policy')
export const createHookPolicy = (data) => postJSON('/admin/hook-policy', data)
export const updateHookPolicy = (id, data) => putJSON(`/admin/hook-policy/${encodeURIComponent(id)}`, data)
export const deleteHookPolicy = (id) => deleteJSON(`/admin/hook-policy/${encodeURIComponent(id)}`)
export const validateHookPolicy = (data) => postJSON('/admin/hook-policy/validate', data)
export const getHookPolicySeed = (id) => getJSON(`/admin/hook-policy/${encodeURIComponent(id)}/seed`)

// Wake-free control-plane capability. The exact HTTPRoute stays on Control Panel;
// only /api/terminal/ws enters the independent Terminal InferencePool.
export const getTerminalCapability = () => controlPlaneGet('/terminal/capability')

// --- Scheduler oversight (D12: per-account drill-down) ---
export const getSchedulerJobs = (accountId) =>
  getJSON(`/admin/scheduler/accounts/${encodeURIComponent(accountId)}/jobs`)
export const getSchedulerRuns = (accountId, params = {}) => {
  const q = new URLSearchParams()
  if (params.jobId) q.set('job_id', params.jobId)
  if (params.status) q.set('status', params.status)
  if (params.before) q.set('before', params.before)
  q.set('limit', String(params.limit || 30))
  return getJSON(`/admin/scheduler/accounts/${encodeURIComponent(accountId)}/runs?${q}`)
}
export const pauseAllSchedulerJobs = (accountId) =>
  postJSON(`/admin/scheduler/accounts/${encodeURIComponent(accountId)}/pause-all`)
export const triggerSchedulerJob = (jobId) =>
  postJSON(`/admin/scheduler/jobs/${encodeURIComponent(jobId)}/trigger`)
