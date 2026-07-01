import { create } from 'zustand'

const makePaneId = () => `pane-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`

const emptyState = {
  panes: [],
  layout: 'single',
  activePaneId: null,
  draggingSession: null,
}

function normalizePanes(panes) {
  return (panes || []).filter((pane) => pane?.sessionId || pane?.local).slice(0, 4)
}

function layoutForCount(count, previous = 'single') {
  if (count <= 1) return 'single'
  if (count === 2) return previous === 'two-rows' ? 'two-rows' : 'two-columns'
  if (count === 3) return previous === 'three-right' ? 'three-right' : 'three-left'
  return 'four'
}

const useSplitStore = create((set, get) => ({
  ...emptyState,

  reset: () => set(emptyState),

  setActivePane: (paneId) => set((s) => ({
    activePaneId: s.panes.some((pane) => pane.id === paneId) ? paneId : s.activePaneId,
  })),

  setLayout: (layout) => set((s) => ({
    layout: layoutForCount(normalizePanes(s.panes).length, layout),
  })),

  beginSessionDrag: (session) => set({ draggingSession: session || null }),
  endSessionDrag: () => set({ draggingSession: null }),

  ensureSessionView: (sessionId) => set((s) => {
    if (!sessionId) return {}
    const panes = normalizePanes(s.panes)
    if (panes.length > 0) return {}
    const pane = { id: makePaneId(), sessionId, local: true }
    return { panes: [pane], layout: 'single', activePaneId: pane.id }
  }),

  openSessionInActivePane: (sessionId) => set((s) => {
    if (!sessionId) return {}
    const panes = normalizePanes(s.panes)
    if (panes.length === 0) {
      const pane = { id: makePaneId(), sessionId, local: true }
      return { panes: [pane], layout: 'single', activePaneId: pane.id }
    }
    const activePaneId = s.activePaneId || panes[0].id
    return {
      panes: panes.map((pane) => (
        pane.id === activePaneId ? { ...pane, sessionId } : pane
      )),
      activePaneId,
    }
  }),

  addSessionWithLayout: (sessionId, layout, currentSessionId = null) => set((s) => {
    if (!sessionId) return {}
    let panes = normalizePanes(s.panes)
    if (panes.length === 0 && currentSessionId) {
      panes = [{ id: makePaneId(), sessionId: currentSessionId, local: true }]
    }
    if (panes.length === 0) {
      const mainPane = { id: makePaneId(), sessionId: null, local: true }
      const pane = { id: makePaneId(), sessionId }
      return {
        panes: [mainPane, pane],
        layout: layout === 'two-rows' ? 'two-rows' : 'two-columns',
        activePaneId: mainPane.id,
      }
    }
    if (panes.length >= 4) {
      const activePaneId = s.activePaneId || panes[0].id
      return {
        panes: panes.map((pane) => (
          pane.id === activePaneId ? { ...pane, sessionId } : pane
        )),
        activePaneId,
        layout: 'four',
      }
    }
    const pane = { id: makePaneId(), sessionId }
    const nextPanes = [...panes, pane]
    const nextLayout = layout || layoutForCount(nextPanes.length, s.layout)
    return {
      panes: nextPanes,
      layout: nextLayout,
      activePaneId: pane.id,
    }
  }),

  closePane: (paneId) => set((s) => {
    const panes = normalizePanes(s.panes).filter((pane) => pane.id !== paneId)
    const activePaneId = panes.some((pane) => pane.id === s.activePaneId)
      ? s.activePaneId
      : panes[0]?.id || null
    return {
      panes,
      activePaneId,
      layout: layoutForCount(panes.length, s.layout),
    }
  }),

  replacePaneSession: (paneId, sessionId) => set((s) => {
    if (!paneId || !sessionId) return {}
    return {
      panes: normalizePanes(s.panes).map((pane) => (
        pane.id === paneId ? { ...pane, sessionId } : pane
      )),
      activePaneId: paneId,
    }
  }),

  getActivePane: () => {
    const { panes, activePaneId } = get()
    return normalizePanes(panes).find((pane) => pane.id === activePaneId) || normalizePanes(panes)[0] || null
  },
}))

export default useSplitStore
