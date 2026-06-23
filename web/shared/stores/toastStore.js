import { create } from 'zustand'

const AUTO_DISMISS_MS = {
  // Every level auto-dismisses now — an API error toast must never linger
  // forever (a transient cold-start 503 used to stick until manually closed).
  error: 10000,
  warning: 8000,
  info: 4000,
  success: 4000,
}

let nextId = 1

const useToastStore = create((set, get) => ({
  toasts: [],
  pushToast: (toast) => {
    const id = toast?.id || `t-${nextId++}`
    const level = toast?.level || 'info'
    const dismissAfterMs = toast?.dismissAfterMs ?? AUTO_DISMISS_MS[level] ?? null
    const createdAt = Date.now()
    const entry = {
      id,
      level,
      title: toast?.title || '',
      body: toast?.body || null,
      action: toast?.action || null,
      dismissAfterMs,
      createdAt,
    }
    // Dedupe by id: pushing the same id (e.g. a repeated "waking" notice while
    // retrying) replaces/refreshes the existing toast instead of stacking copies.
    set((s) => ({ toasts: [...s.toasts.filter((t) => t.id !== id), entry] }))
    if (dismissAfterMs) {
      setTimeout(() => {
        // Generation guard: only dismiss if this exact entry is still showing.
        // A refresh (same id, newer createdAt) installs its own timer; this one
        // must not dismiss the refreshed toast early.
        const cur = get().toasts.find((t) => t.id === id)
        if (cur && cur.createdAt === createdAt) get().dismissToast(id)
      }, dismissAfterMs)
    }
    return id
  },
  dismissToast: (id) => set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),
  clearToasts: () => set({ toasts: [] }),
}))

export default useToastStore
