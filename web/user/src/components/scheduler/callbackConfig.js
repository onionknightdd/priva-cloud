export function hasFeishuCallback(jobConfig) {
  return jobConfig?.callback?.type === 'feishu'
}

export function isFeishuCallbackReady(config) {
  return Boolean(config?.owner_bound && config?.effective_enabled)
}

export function canSetFeishuCallback(nextEnabled, config) {
  // An unavailable bot may never be selected, but an existing callback must
  // remain removable if the user disables or unbinds their bot later.
  return !nextEnabled || isFeishuCallbackReady(config)
}

export function buildFeishuCallback(enabled) {
  return enabled ? { type: 'feishu' } : null
}
