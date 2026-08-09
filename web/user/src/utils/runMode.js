export const RUN_MODES = new Set(['agent', 'code'])

export function normalizeRunMode(value, fallback = 'agent') {
  if (RUN_MODES.has(value)) return value
  return RUN_MODES.has(fallback) ? fallback : 'agent'
}

export function effectiveRunMode(chatState, draftRunMode) {
  return normalizeRunMode(chatState?.runMode, normalizeRunMode(draftRunMode))
}

export function isRunModeLocked(chatState) {
  return chatState?.runModeLocked === true || Boolean(chatState?.sessionId)
}
