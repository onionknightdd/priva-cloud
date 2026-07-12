import { create } from 'zustand'
import * as hooksApi from '../api/hooks'
import safeStorage from '@shared/utils/safeStorage'

const DEFAULT_DETAIL_WIDTH = 380

// Pull one scope's hooks dict out of the GET /config `scopes` array.
function hooksForScope(scopes, scope, cwd) {
  const match = (scopes || []).find(
    (s) => s.scope === scope && (s.cwd || null) === (cwd || null),
  )
  return match ? (match.hooks || {}) : {}
}

const useHooksStore = create((set, get) => ({
  selectedHookId: null,
  activeDetailTab: 'config',
  // configuredHooks = the ACTIVE scope's hooks (what the per-event UI renders).
  configuredHooks: {},
  configuredScopes: [],   // raw GET /config scopes: [{scope, cwd, hooks}]
  adminHooks: {},         // admin hooks active per event (virtual, read-only)
  activeScope: 'user',    // 'user' | 'project'
  activeCwd: null,        // absolute cwd when activeScope === 'project'
  detailWidth: safeStorage.getNumber('hooks-detail-width', DEFAULT_DETAIL_WIDTH, { min: 280, max: 600 }),
  listWidth: safeStorage.getNumber('hooks-list-width', 240, { min: 220, max: 420 }),

  // Config state
  configLoading: false,
  savingConfig: false,

  // Catalog state
  catalog: [],
  catalogLoading: false,

  // Test state
  testResult: null,
  testRunning: false,

  // Logs state (cursor-paginated)
  logs: [],
  logsTotal: null,
  logsLoading: false,
  logsFilter: null,
  // Cursor stack: stack[-1] is the `before` cursor for the current page.
  logsCursorStack: [null],
  logsNextCursor: null,

  // Handler form state
  handlerFormOpen: false,
  editingHandler: null, // { eventType, index, entry } or null (= new)

  // --- Navigation ---
  selectHook: (id) => {
    set({ selectedHookId: id, activeDetailTab: 'config' })
    const s = get()
    if (!s._configLoaded) s.loadConfig()
    if (!s._catalogLoaded) s.loadCatalog()
  },
  clearSelection: () => set({ selectedHookId: null }),
  setDetailTab: (tab) => set({ activeDetailTab: tab }),
  setDetailWidth: (w) => {
    safeStorage.setItem('hooks-detail-width', String(w))
    set({ detailWidth: w })
  },
  setListWidth: (w) => {
    safeStorage.setItem('hooks-list-width', String(w))
    set({ listWidth: w })
  },

  // --- Config ---
  _configLoaded: false,
  loadConfig: async () => {
    set({ configLoading: true })
    try {
      const data = await hooksApi.fetchConfig()
      const scopes = Array.isArray(data.scopes) ? data.scopes : []
      // Default to the first scope that actually has hooks, else User.
      const withHooks = scopes.find((s) => Object.keys(s.hooks || {}).length > 0)
      const active = withHooks || scopes.find((s) => s.scope === 'user') || { scope: 'user', cwd: null }
      set({
        configuredScopes: scopes,
        adminHooks: data.admin || {},
        activeScope: active.scope,
        activeCwd: active.cwd || null,
        configuredHooks: hooksForScope(scopes, active.scope, active.cwd || null),
        _configLoaded: true,
      })
    } catch (e) {
      console.error('Failed to load hook config:', e)
    } finally {
      set({ configLoading: false })
    }
  },

  // Switch which settings.json scope the Config tab shows/edits.
  setActiveScope: (scope, cwd) => {
    const { configuredScopes } = get()
    set({
      activeScope: scope,
      activeCwd: cwd || null,
      configuredHooks: hooksForScope(configuredScopes, scope, cwd || null),
    })
  },

  saveConfig: async (hooks) => {
    const { activeScope, activeCwd } = get()
    set({ savingConfig: true })
    try {
      const data = await hooksApi.updateConfig(hooks, activeScope, activeCwd)
      const savedHooks = data.hooks || hooks
      // Reflect the write back into the active scope entry (insert if new).
      const scopes = [...get().configuredScopes]
      const idx = scopes.findIndex(
        (s) => s.scope === activeScope && (s.cwd || null) === (activeCwd || null),
      )
      const entry = { scope: activeScope, cwd: activeCwd || null, hooks: savedHooks }
      if (idx >= 0) scopes[idx] = entry
      else scopes.push(entry)
      set({ configuredHooks: savedHooks, configuredScopes: scopes })
    } catch (e) {
      console.error('Failed to save hook config:', e)
    } finally {
      set({ savingConfig: false })
    }
  },

  addHandler: async (eventType, entry) => {
    const hooks = { ...get().configuredHooks }
    if (!hooks[eventType]) hooks[eventType] = []
    hooks[eventType] = [...hooks[eventType], entry]
    await get().saveConfig(hooks)
  },

  updateHandler: async (eventType, idx, entry) => {
    const hooks = { ...get().configuredHooks }
    const list = [...(hooks[eventType] || [])]
    list[idx] = entry
    hooks[eventType] = list
    await get().saveConfig(hooks)
  },

  removeHandler: async (eventType, idx) => {
    const hooks = { ...get().configuredHooks }
    const list = [...(hooks[eventType] || [])]
    list.splice(idx, 1)
    hooks[eventType] = list.length ? list : undefined
    // Clean up empty keys
    if (!hooks[eventType]) delete hooks[eventType]
    await get().saveConfig(hooks)
  },

  // --- Catalog ---
  _catalogLoaded: false,
  loadCatalog: async () => {
    set({ catalogLoading: true })
    try {
      const data = await hooksApi.fetchCatalog()
      set({ catalog: Array.isArray(data) ? data : [], _catalogLoaded: true })
    } catch (e) {
      console.error('Failed to load hook catalog:', e)
    } finally {
      set({ catalogLoading: false })
    }
  },

  // Admin hooks are enforced-only and read-only (D6) — no enable/disable action.

  // --- Test ---
  runTest: async (eventType, handler, inputJson) => {
    set({ testRunning: true, testResult: null })
    try {
      const result = await hooksApi.testHook(eventType, handler, inputJson)
      set({ testResult: result })
    } catch (e) {
      set({ testResult: { exit_code: -1, stdout: '', stderr: e.message, duration_ms: 0 } })
    } finally {
      set({ testRunning: false })
    }
  },
  clearTestResult: () => set({ testResult: null }),

  // --- Logs ---
  loadLogs: async (eventType, limit = 50) => {
    const cursor = get().logsCursorStack.at(-1)
    set({ logsLoading: true })
    try {
      const data = await hooksApi.fetchLogs({ eventType, limit, before: cursor })
      set({
        logs: data.entries || [],
        logsTotal: data.total ?? null,
        logsFilter: eventType || null,
        logsNextCursor: data.next_cursor || null,
      })
    } catch (e) {
      console.error('Failed to load hook logs:', e)
    } finally {
      set({ logsLoading: false })
    }
  },
  setLogsFilter: (eventType) => {
    set({
      logsFilter: eventType || null,
      logsCursorStack: [null],
      logsNextCursor: null,
    })
  },
  logsNext: () => {
    const { logsNextCursor, logsCursorStack } = get()
    if (!logsNextCursor) return
    set({ logsCursorStack: [...logsCursorStack, logsNextCursor] })
  },
  logsPrev: () => {
    const { logsCursorStack } = get()
    if (logsCursorStack.length <= 1) return
    set({ logsCursorStack: logsCursorStack.slice(0, -1) })
  },

  // --- Handler form ---
  openHandlerForm: (handler) => set({
    handlerFormOpen: true,
    editingHandler: handler || null,
  }),
  closeHandlerForm: () => set({
    handlerFormOpen: false,
    editingHandler: null,
  }),

  // --- Reset ---
  reset: () => set({
    selectedHookId: null,
    activeDetailTab: 'config',
    configuredHooks: {},
    configuredScopes: [],
    adminHooks: {},
    activeScope: 'user',
    activeCwd: null,
    configLoading: false,
    savingConfig: false,
    catalog: [],
    catalogLoading: false,
    testResult: null,
    testRunning: false,
    logs: [],
    logsTotal: null,
    logsLoading: false,
    logsFilter: null,
    logsCursorStack: [null],
    logsNextCursor: null,
    handlerFormOpen: false,
    editingHandler: null,
    _configLoaded: false,
    _catalogLoaded: false,
  }),
}))

export default useHooksStore
