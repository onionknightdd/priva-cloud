import { sandboxGet, sandboxRead, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// Catalog (admin hook policies visible to this user). Lean now — description
// only, no script body — so it rides the plain sandboxGet lane (well under the
// EPP cap). Each entry: { id, name, description, hook_type, events, matcher,
// enforced, default_on, enabled, predefined }.
export const fetchCatalog = () => sandboxGet('/hooks/catalog')

// Config. GET returns { scopes: [{scope, cwd, hooks}], admin: {event: [...]} } —
// user hooks are native across the user + project settings.json scopes.
export const fetchConfig = () => sandboxGet('/hooks/config')
// PUT writes ONE scope's hooks (user | project + cwd). Only that scope is touched.
export const updateConfig = (hooks, scope = 'project', cwd = null) => {
  const params = new URLSearchParams({ scope })
  if (cwd) params.set('cwd', cwd)
  return sandboxPut(`/hooks/config?${params}`, { hooks })
}

// Admin hooks are enforced-only and delivered natively (D6) — there is no
// per-user enable/disable endpoint; the catalog is read-only.

// Test (dry-run) — user custom command hooks
export const testHook = (eventType, handler, inputJson) =>
  sandboxPost('/hooks/test', { event_type: eventType, handler, input_json: inputJson })

// Logs (cursor-paginated)
// sandboxRead: pages carry up to 200 entries — can exceed the ~8KB EPP cap.
export const fetchLogs = ({ eventType = null, limit = 50, before = null, after = null } = {}) => {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (eventType) params.set('event_type', eventType)
  if (before) params.set('before', before)
  if (after) params.set('after', after)
  return sandboxRead(`/hooks/logs?${params}`)
}

// Script content — read a hook script file from the user's work dir
// sandboxRead: scripts run up to 512KB — far past the ~8KB EPP cap.
export const fetchScriptContent = (path) =>
  sandboxRead(`/hooks/script/content?path=${encodeURIComponent(path)}`)
