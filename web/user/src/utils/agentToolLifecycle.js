const AGENT_TOOL_NAMES = new Set(['Agent', 'Task'])

const ASYNC_LAUNCH_STATUSES = new Set([
  'async_launched',
  'remote_launched',
  'teammate_spawned',
])

const RUNNING_STATUSES = new Set([
  'running',
  'pending',
  'queued',
  'in_progress',
  ...ASYNC_LAUNCH_STATUSES,
])

const TERMINATED_STATUSES = new Set([
  'killed',
  'stopped',
  'cancelled',
  'canceled',
  'aborted',
])

const FAILED_STATUSES = new Set(['failed', 'error'])

function normalizedStatus(value) {
  return String(value || '').trim().toLowerCase()
}

function resultText(result) {
  const content = result?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((item) => {
      if (typeof item === 'string') return item
      if (typeof item?.text === 'string') return item.text
      if (typeof item?.content === 'string') return item.content
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

function structuredResults(result, toolUseResult = null) {
  return [
    toolUseResult,
    result?.tool_use_result,
    result?.toolUseResult,
  ].filter((value) => value && typeof value === 'object')
}

export function isAgentToolName(name) {
  return AGENT_TOOL_NAMES.has(name)
}

export function agentLifecycleFromStatus(status) {
  const normalized = normalizedStatus(status)
  if (normalized === 'completed' || normalized === 'complete' || normalized === 'success' || normalized === 'done') {
    return 'completed'
  }
  if (TERMINATED_STATUSES.has(normalized)) return 'terminated'
  if (FAILED_STATUSES.has(normalized)) return 'failed'
  if (RUNNING_STATUSES.has(normalized)) return 'running'
  return null
}

export function normalizeAgentTaskNotification(notification) {
  if (!notification || typeof notification !== 'object') return null
  return {
    toolUseId: notification.toolUseId || notification.tool_use_id || null,
    taskId: notification.taskId || notification.task_id || null,
    status: normalizedStatus(notification.status) || 'completed',
    summary: notification.summary || '',
    timestamp: notification.timestamp || notification.endTime || notification.end_time || null,
  }
}

export function getAgentResultInfo(result, toolUseResult = null) {
  const structured = structuredResults(result, toolUseResult)
  const text = resultText(result)
  const structuredStatus = structured
    .map((value) => normalizedStatus(value.status || value.taskStatus || value.task_status))
    .find(Boolean) || ''
  const parsedAgentId = text.match(/\b(?:agentId|agent_id)\s*:\s*([A-Za-z0-9_-]+)/i)?.[1] || ''
  const agentId = structured
    .map((value) => value.agentId || value.agent_id || value.taskId || value.task_id)
    .find((value) => typeof value === 'string' && value.trim()) || parsedAgentId || ''
  const isAsync = structured.some((value) => value.isAsync === true || value.is_async === true)
    || ASYNC_LAUNCH_STATUSES.has(structuredStatus)
    || /async agent launched successfully|agent is working in the background/i.test(text)
  const isError = result?.is_error === true
    || structured.some((value) => value.is_error === true || value.isError === true)
    || FAILED_STATUSES.has(structuredStatus)

  return {
    agentId: String(agentId || '').trim(),
    launchStatus: structuredStatus,
    isAsync,
    isError,
  }
}

export function getAgentLifecycle(block) {
  const explicit = agentLifecycleFromStatus(block?.agentTaskStatus)
  if (explicit) return explicit

  const resultInfo = getAgentResultInfo(block?.result, block?.toolUseResult)
  if (resultInfo.isError || block?.result?.is_error === true) return 'failed'
  // The Agent tool_result confirms only that a background agent launched. It
  // must not turn the row into a completed state before task-notification.
  if (resultInfo.isAsync) return 'running'

  const blockLifecycle = agentLifecycleFromStatus(block?.status)
  if (blockLifecycle) return blockLifecycle
  if (block?.result) return 'completed'
  return 'running'
}

export function getAgentDisplayId(block) {
  if (typeof block?.agentId === 'string' && block.agentId.trim()) return block.agentId.trim()
  return getAgentResultInfo(block?.result, block?.toolUseResult).agentId
}

export function getAgentDisplayName(block) {
  const input = block?.input || {}
  return input.name || input.description || input.subagent_type || input.agent_type || 'Agent'
}

export function agentLifecycleToBlockStatus(lifecycle) {
  if (lifecycle === 'running') return 'running'
  if (lifecycle === 'failed') return 'error'
  if (lifecycle === 'terminated') return 'stopped'
  return 'success'
}
