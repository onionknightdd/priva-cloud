import { create, useStore } from 'zustand'

// Session runtime registry — the multi-session backbone.
//
// Each session (live or retained) owns a *runtime*: a lazily-created set of
// vanilla zustand slices (chat/tasks/fileOps/fileBrowser/workflow) plus UI
// metadata. Exactly one runtime is *active* (rendered); streams keep writing
// to their own runtime's slices whether or not it is active, so switching
// sessions never disturbs a running stream.
//
// Store modules register a slice factory here and export a facade
// (makeFacade) that always resolves to the ACTIVE runtime's slice — so the
// ~40 existing consumer files keep their `useXStore(selector)` /
// `.getState()` / `.setState()` / `.subscribe()` call patterns unchanged.
//
// This module must not import any of the five store modules (they import it).

const sliceFactories = {}
const runtimes = new Map() // key -> runtime; key = sessionId or 'draft-<n>'

// Former keys → canonical key. The CLI mints a NEW session id on every
// resume spawn (see agent-runner service.py: current_resume_id), so a
// conversation's id rotates per turn while sidebar rows / status entries /
// split channels may still hold an older id. Every lookup resolves through
// this map so a stale id always finds the live runtime.
const keyAliases = new Map()

// Monotonic — never reset, so a logout/reset always mints a fresh draft key
// and every facade subscription/hook sees an activeKey change to rebind on.
let draftCounter = 0

function createRuntimeObject(key) {
  return {
    key,
    sessionId: String(key).startsWith('draft-') ? null : key,
    slices: {},
    meta: {
      // Per-session UI snapshot ({canvasVisible, canvasMinimized,
      // activeCanvasTab, planContent, planFilePath}) captured on switch-away
      // and re-applied on switch-back. Background streams merge desired UI
      // state here instead of touching the live uiStore.
      ui: null,
      sidebarRowId: null,
      lastActiveAt: Date.now(),
    },
  }
}

const initialKey = `draft-${++draftCounter}`
runtimes.set(initialKey, createRuntimeObject(initialKey))

export const useRegistry = create(() => ({ activeKey: initialKey }))

export function registerSliceFactory(name, factory) {
  sliceFactories[name] = factory
}

// Follow rotated-id aliases to the canonical runtime key. Returns the input
// unchanged when no alias exists (so it is safe to call on anything).
export function resolveKey(key) {
  let current = key
  for (let hops = 0; hops < 8; hops++) {
    const next = keyAliases.get(current)
    if (next === undefined || next === current) return current
    current = next
  }
  return current
}

export function hasRuntime(key) {
  return runtimes.has(resolveKey(key))
}

export function getRuntime(key) {
  return runtimes.get(resolveKey(key)) || null
}

export function listRuntimes() {
  return [...runtimes.values()]
}

export function ensureRuntime(key) {
  const canonical = resolveKey(key)
  let rt = runtimes.get(canonical)
  if (!rt) {
    rt = createRuntimeObject(canonical)
    runtimes.set(canonical, rt)
  }
  return rt
}

export function getSlice(key, name) {
  const rt = ensureRuntime(key)
  let slice = rt.slices[name]
  if (!slice) {
    const factory = sliceFactories[name]
    if (!factory) throw new Error(`[registry] no slice factory registered: ${name}`)
    // getSibling closes over the runtime OBJECT (rt.key is live), so a
    // draft→sessionId rekey mid-stream keeps sibling lookups on this runtime.
    const getSibling = (siblingName) => getSlice(rt.key, siblingName)
    slice = factory(getSibling)
    rt.slices[name] = slice
  }
  return slice
}

export function getActiveKey() {
  return useRegistry.getState().activeKey
}

export function useActiveKey() {
  return useRegistry((s) => s.activeKey)
}

export function setActiveKey(key) {
  const rt = ensureRuntime(key)
  rt.meta.lastActiveAt = Date.now()
  useRegistry.setState({ activeKey: rt.key })
}

export function newDraftRuntime() {
  const key = `draft-${++draftCounter}`
  runtimes.set(key, createRuntimeObject(key))
  setActiveKey(key)
  return key
}

// Draft got its real session id (system.init / result), or a resume rotated
// the id (the CLI mints a new one per spawn). Moves the runtime under the
// new key and leaves an alias behind so sidebar rows / status entries still
// holding the former id keep resolving to this live runtime.
export function rekeyRuntime(oldKey, sessionId) {
  const rt = runtimes.get(resolveKey(oldKey))
  if (!rt || !sessionId || rt.key === sessionId) return rt || null
  const fromKey = rt.key
  // A stale retained runtime already parked under the target id (old
  // snapshot of the same session) is replaced by the live one.
  runtimes.delete(sessionId)
  runtimes.delete(fromKey)
  rt.key = sessionId
  rt.sessionId = sessionId
  runtimes.set(sessionId, rt)
  // Re-point every alias chain ending at the former key, then alias it.
  for (const [alias, target] of keyAliases) {
    if (target === fromKey) keyAliases.set(alias, sessionId)
  }
  keyAliases.set(fromKey, sessionId)
  keyAliases.delete(sessionId) // the canonical key must never alias away
  if (useRegistry.getState().activeKey === fromKey) {
    useRegistry.setState({ activeKey: sessionId })
  }
  return rt
}

// Remove a runtime (delete-session flow). Removing the active one activates
// a fresh draft so the facades always have a live slice to resolve to.
export function removeRuntime(key) {
  const canonical = resolveKey(key)
  if (!runtimes.has(canonical)) return
  runtimes.delete(canonical)
  for (const [alias, target] of keyAliases) {
    if (target === canonical) keyAliases.delete(alias)
  }
  if (useRegistry.getState().activeKey === canonical) {
    newDraftRuntime()
  }
}

const RETAINED_RUNTIME_CAP = 8

// Evict least-recently-active runtimes over the cap. Never evicts the active
// runtime, a streaming one, or one awaiting a permission/question decision —
// their state is not reconstructable from the session JSONL.
export function evictIfNeeded(cap = RETAINED_RUNTIME_CAP) {
  if (runtimes.size <= cap) return
  const active = getActiveKey()
  const candidates = [...runtimes.values()]
    .filter((rt) => rt.key !== active)
    .filter((rt) => {
      const chat = rt.slices.chat?.getState?.()
      if (!chat) return true
      if (chat.isStreaming) return false
      return !(chat.pendingPermission || chat.pendingAskUser || chat.pendingPlanApproval
        || (chat.permissionQueue || []).length > 0)
    })
    .sort((a, b) => a.meta.lastActiveAt - b.meta.lastActiveAt)
  for (const rt of candidates) {
    if (runtimes.size <= cap) break
    runtimes.delete(rt.key)
    for (const [alias, target] of keyAliases) {
      if (target === rt.key) keyAliases.delete(alias)
    }
  }
}

// Logout: abort every live stream, drop every runtime, start a fresh draft.
export function resetAllRuntimes() {
  for (const rt of runtimes.values()) {
    try {
      rt.slices.chat?.getState?.().streamAbort?.()
    } catch { /* socket already gone */ }
  }
  runtimes.clear()
  keyAliases.clear()
  newDraftRuntime()
}

// Facade factory: a drop-in replacement for the old module-global store hook.
export function makeFacade(sliceName) {
  function useFacade(selector) {
    const activeKey = useStore(useRegistry, (s) => s.activeKey)
    return useStore(getSlice(activeKey, sliceName), selector)
  }
  useFacade.getState = () => getSlice(getActiveKey(), sliceName).getState()
  useFacade.setState = (partial, replace) => getSlice(getActiveKey(), sliceName).setState(partial, replace)
  // Dynamic rebinding: subscribers (split-pane snapshot mirroring) follow the
  // active slice across session switches, with one synthetic fire on rebind
  // so they resync to the newly active session's state.
  useFacade.subscribe = (listener) => {
    let current = getSlice(getActiveKey(), sliceName)
    let unsubSlice = current.subscribe(listener)
    const unsubRegistry = useRegistry.subscribe((regState, prevRegState) => {
      if (regState.activeKey === prevRegState.activeKey) return
      const next = getSlice(regState.activeKey, sliceName)
      if (next === current) return
      unsubSlice()
      current = next
      unsubSlice = next.subscribe(listener)
      const state = next.getState()
      listener(state, state)
    })
    return () => {
      unsubSlice()
      unsubRegistry()
    }
  }
  return useFacade
}
