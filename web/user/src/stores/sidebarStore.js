import { create } from 'zustand'
import {
  fetchSessionsGrouped,
  fetchCwdSessions,
  pinSession,
  archiveSession,
  pinWorkdir,
  archiveWorkdir,
} from '../api/sessions'
import { UnauthorizedError } from '@shared/api/client'
import safeStorage from '@shared/utils/safeStorage'

const STORAGE_KEY_WIDTH = 'sidebar-width'
const STORAGE_KEY_COLLAPSED = 'sidebar-collapsed'
const GROUP_PAGE_SIZE = 20 // per "more in this dir" page

const getStoredWidth = () => safeStorage.getNumber(STORAGE_KEY_WIDTH, 240, { min: 180, max: 480 })

const getStoredCollapsed = () => safeStorage.getBoolean(STORAGE_KEY_COLLAPSED)

let _widthSaveTimer = null
const persistWidth = (width) => {
  if (_widthSaveTimer) clearTimeout(_widthSaveTimer)
  _widthSaveTimer = setTimeout(() => {
    safeStorage.setItem(STORAGE_KEY_WIDTH, String(width))
    _widthSaveTimer = null
  }, 200)
}

function mapSession(s) {
  return {
    id: s.session_id,
    sessionId: s.session_id,
    name: s.custom_title || s.first_prompt || s.summary || s.session_id,
    customTitle: s.custom_title || null,
    createdAt: s.last_modified,
    summary: s.summary,
    firstPrompt: s.first_prompt,
    gitBranch: s.git_branch,
    cwd: s.cwd,
    fileSize: s.file_size,
    sessionSource: s.session_source || 'project',
    tag: s.tag || null,
    pinned: s.pinned || false,
    archived: s.archived || false,
    parentSessionId: s.parent_session_id || null,
    parentMessageUuid: s.parent_message_uuid || null,
    forkCount: s.fork_count || 0,
  }
}

const useSidebarStore = create((set, get) => ({
  width: getStoredWidth(),
  collapsed: getStoredCollapsed(),
  // Flat union of every loaded session (across all cwd groups). Kept flat so
  // ChatPanel/FileBrowserPanel can find the active session's cwd and so
  // rename/tag/delete/updateSession stay simple. The sidebar render groups it.
  sessions: [],
  // Group metadata only: [{ cwd, total }] in display order (active cwd pinned
  // first by the backend). `total` drives the per-group "more in this dir".
  groups: [],
  activeCwd: null,
  recentActivities: [],
  expandedCwds: {}, // { [cwd]: bool } — active cwd defaults open, others closed
  activeSessionId: null,
  sessionsLoading: false,   // initial grouped load
  groupLoadingCwd: null,    // a single cwd's "more" load in flight
  // Tag filter (null = show all)
  activeTag: null,
  setActiveTag: (tag) => set({ activeTag: tag }),

  setWidth: (width) => {
    set({ width })
    persistWidth(width)
  },

  setCollapsed: (collapsed) => {
    safeStorage.setItem(STORAGE_KEY_COLLAPSED, String(collapsed))
    set({ collapsed })
  },

  toggleCollapsed: () => set((s) => {
    const collapsed = !s.collapsed
    safeStorage.setItem(STORAGE_KEY_COLLAPSED, String(collapsed))
    return { collapsed }
  }),

  setActiveSessionId: (id) => set({ activeSessionId: id }),

  toggleGroup: (cwd) => set((s) => ({
    expandedCwds: { ...s.expandedCwds, [cwd]: !s.expandedCwds[cwd] },
  })),

  // Expand or collapse every workdir group at once (PROJECT header toggle).
  setAllGroupsExpanded: (expanded) => set((s) => {
    const next = {}
    for (const g of s.groups) next[g.cwd] = expanded
    // Preserve cwds not yet in `groups` (e.g. a freshly created session).
    for (const cwd of Object.keys(s.expandedCwds)) {
      if (!(cwd in next)) next[cwd] = expanded
    }
    return { expandedCwds: next }
  }),

  updateSession: (id, data) => set((s) => ({
    sessions: s.sessions.map((sess) => (sess.id === id ? { ...sess, ...data } : sess)),
  })),

  // --- Pin / archive (API-first, then patch local state — no refetch, so
  // expansion + already-loaded "more" pages are preserved). Render derives the
  // visual order from these flags, matching the backend's ordering.

  togglePinSession: async (id) => {
    const sess = get().sessions.find((s) => s.id === id)
    if (!sess) return
    const pinned = !sess.pinned
    try {
      await pinSession(id, pinned)
      set((s) => ({
        sessions: s.sessions.map((x) => (x.id === id ? { ...x, pinned } : x)),
      }))
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to pin session:', err)
    }
  },

  archiveSessionLocal: async (id) => {
    const sess = get().sessions.find((s) => s.id === id)
    if (!sess) return
    try {
      await archiveSession(id, true)
      set((s) => {
        const sessions = s.sessions.filter((x) => x.id !== id)
        const groups = s.groups
          .map((g) => (g.cwd === sess.cwd ? { ...g, total: Math.max(0, g.total - 1) } : g))
          // A workdir whose last visible session is archived disappears.
          .filter((g) => sessions.some((x) => x.cwd === g.cwd))
        return { sessions, groups }
      })
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to archive session:', err)
    }
  },

  togglePinWorkdir: async (cwd) => {
    const group = get().groups.find((g) => g.cwd === cwd)
    if (!group) return
    const pinned = !group.pinned
    try {
      await pinWorkdir(cwd, pinned)
      set((s) => ({
        groups: s.groups.map((g) => (g.cwd === cwd ? { ...g, pinned } : g)),
      }))
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to pin workdir:', err)
    }
  },

  archiveWorkdirLocal: async (cwd) => {
    try {
      await archiveWorkdir(cwd)
      set((s) => ({
        sessions: s.sessions.filter((x) => x.cwd !== cwd),
        groups: s.groups.filter((g) => g.cwd !== cwd),
      }))
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to archive workdir:', err)
    }
  },

  fetchSessions: async () => {
    set({ sessionsLoading: true })
    try {
      const data = await fetchSessionsGrouped()
      const rawGroups = data.groups || []
      const groups = rawGroups.map((g) => ({ cwd: g.cwd, total: g.total, pinned: g.pinned || false }))
      const flat = []
      const seen = new Set()
      for (const g of rawGroups) {
        for (const s of g.sessions || []) {
          if (seen.has(s.session_id)) continue
          seen.add(s.session_id)
          flat.push(mapSession(s))
        }
      }
      const activeCwd = data.active_cwd || null
      set((s) => {
        // Default expand: active cwd open, others collapsed. Preserve a user's
        // existing toggle for groups that were already present.
        const expandedCwds = {}
        for (const g of groups) {
          expandedCwds[g.cwd] = (g.cwd in s.expandedCwds)
            ? s.expandedCwds[g.cwd]
            : g.cwd === activeCwd
        }
        return { sessions: flat, groups, activeCwd, expandedCwds }
      })
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to fetch sessions:', err)
    } finally {
      set({ sessionsLoading: false })
    }
  },

  fetchMoreInGroup: async (cwd) => {
    if (get().groupLoadingCwd) return
    const loaded = get().sessions.filter((s) => s.cwd === cwd).length
    set({ groupLoadingCwd: cwd })
    try {
      const data = await fetchCwdSessions(cwd, GROUP_PAGE_SIZE, loaded)
      const incoming = (data.sessions || []).map(mapSession)
      set((s) => {
        const have = new Set(s.sessions.map((x) => x.id))
        const merged = [...s.sessions, ...incoming.filter((x) => !have.has(x.id))]
        const groups = s.groups.map((g) => (g.cwd === cwd ? { ...g, total: data.total ?? g.total } : g))
        return { sessions: merged, groups }
      })
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to load more sessions:', err)
    } finally {
      set({ groupLoadingCwd: null })
    }
  },

  reset: () => set({
    sessions: [], groups: [], activeCwd: null, recentActivities: [], expandedCwds: {},
    activeSessionId: null, sessionsLoading: false, groupLoadingCwd: null,
  }),
}))

export default useSidebarStore
