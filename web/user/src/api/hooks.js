import { sandboxGet, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// Catalog (built-in hooks)
export const fetchCatalog = () => sandboxGet('/hooks/catalog')

// Config (merged: admin + project + local)
export const fetchConfig = () => sandboxGet('/hooks/config')
export const updateConfig = (hooks) => sandboxPut('/hooks/config', { hooks })

// Enable/disable a built-in hook
export const enableBuiltInHook = (hookId) =>
  sandboxPost(`/hooks/catalog/${encodeURIComponent(hookId)}/enable`, {})
export const disableBuiltInHook = (hookId) =>
  sandboxPost(`/hooks/catalog/${encodeURIComponent(hookId)}/disable`, {})

// Test (dry-run) — user custom command hooks
export const testHook = (eventType, handler, inputJson) =>
  sandboxPost('/hooks/test', { event_type: eventType, handler, input_json: inputJson })

// Test — built-in hooks
export const testBuiltInHook = (hookId, eventType, inputJson) =>
  sandboxPost('/hooks/test/builtin', { hook_id: hookId, event_type: eventType, input_json: inputJson })

// Logs (cursor-paginated)
export const fetchLogs = ({ eventType = null, limit = 50, before = null, after = null } = {}) => {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (eventType) params.set('event_type', eventType)
  if (before) params.set('before', before)
  if (after) params.set('after', after)
  return sandboxGet(`/hooks/logs?${params}`)
}

// Script content — read a hook script file from the user's work dir
export const fetchScriptContent = (path) =>
  sandboxGet(`/hooks/script/content?path=${encodeURIComponent(path)}`)
