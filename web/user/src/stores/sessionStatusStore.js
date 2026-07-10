import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'

// Session-list dot state, keyed by runtime key (sessionId once assigned).
//
//   running   → purple  — a live stream is producing
//   attention → orange  — awaiting a permission decision / AskUserQuestion /
//                          plan approval
//   unseen    → green   — a run finished while the session was backgrounded
//   seen      → gray    — finished and the user has looked at it
//
// unseen/seen persist across refresh; running/attention are transport-bound
// (Phase 2 attach restores them from the backend's running-run registry).

const STORAGE_KEY = 'priva-session-status'
const PERSISTED = new Set(['unseen', 'seen'])
const MAX_PERSISTED = 300

function loadPersisted() {
  const parsed = safeStorage.getJSON(STORAGE_KEY)
  if (!parsed || typeof parsed !== 'object') return {}
  const out = {}
  for (const [key, value] of Object.entries(parsed)) {
    if (PERSISTED.has(value)) out[key] = value
  }
  return out
}

function persist(statuses) {
  const entries = Object.entries(statuses).filter(([, v]) => PERSISTED.has(v))
  safeStorage.setItem(STORAGE_KEY, JSON.stringify(Object.fromEntries(entries.slice(-MAX_PERSISTED))))
}

const useSessionStatusStore = create((set) => ({
  statuses: loadPersisted(),

  setStatus: (key, status) => {
    if (!key) return
    set((s) => {
      if (s.statuses[key] === status) return {}
      const statuses = { ...s.statuses, [key]: status }
      persist(statuses)
      return { statuses }
    })
  },

  rekey: (oldKey, newKey) => set((s) => {
    if (!oldKey || !newKey || oldKey === newKey || !(oldKey in s.statuses)) return {}
    const statuses = { ...s.statuses, [newKey]: s.statuses[oldKey] }
    delete statuses[oldKey]
    persist(statuses)
    return { statuses }
  }),

  // Selecting a session acknowledges a green dot (unseen → seen). Running /
  // attention are live states and stay put.
  markSeen: (key) => set((s) => {
    if (s.statuses[key] !== 'unseen') return {}
    const statuses = { ...s.statuses, [key]: 'seen' }
    persist(statuses)
    return { statuses }
  }),

  clear: (key) => set((s) => {
    if (!(key in s.statuses)) return {}
    const statuses = { ...s.statuses }
    delete statuses[key]
    persist(statuses)
    return { statuses }
  }),

  reset: () => {
    safeStorage.removeItem(STORAGE_KEY)
    set({ statuses: {} })
  },
}))

export default useSessionStatusStore
