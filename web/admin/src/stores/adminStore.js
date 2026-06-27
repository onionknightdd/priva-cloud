import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import * as adminApi from '@shared/api/admin'

// Largest selectable gateway trailing window (15m). The sample ring buffer is capped
// to a little past this so any window's count is always derivable, with no unbounded growth.
const GATEWAY_MAX_WINDOW_SEC = 900

const useAdminStore = create((set, get) => ({
  // User management
  users: [],
  usersLoading: true,
  selectedUser: null,
  drawerOpen: false,

  fetchUsers: async () => {
    set({ usersLoading: true })
    try {
      const users = await adminApi.listUsers()
      set({ users, usersLoading: false })
    } catch {
      set({ usersLoading: false })
    }
  },

  openUserDrawer: (username) => set({ selectedUser: username, drawerOpen: true }),
  closeUserDrawer: () => set({ selectedUser: null, drawerOpen: false }),

  // Pending registrations (awaiting admin approval)
  pendingUsers: [],
  pendingUsersLoading: true,

  fetchPendingUsers: async () => {
    set({ pendingUsersLoading: true })
    try {
      const pendingUsers = await adminApi.getPendingRegistrations()
      set({ pendingUsers, pendingUsersLoading: false })
    } catch {
      set({ pendingUsersLoading: false })
    }
  },

  approvePendingUser: async (requestId) => {
    await adminApi.approvePendingUser(requestId)
    await Promise.all([get().fetchPendingUsers(), get().fetchUsers()])
  },

  rejectPendingUser: async (requestId) => {
    await adminApi.rejectPendingUser(requestId)
    await get().fetchPendingUsers()
  },

  // Resizable edit drawer
  drawerWidth: safeStorage.getNumber('admin-drawer-width', 420, { min: 320, max: typeof window !== 'undefined' ? Math.round(window.innerWidth * 0.6) : 420 }),
  setDrawerWidth: (width) => {
    safeStorage.setItem('admin-drawer-width', String(width))
    set({ drawerWidth: width })
  },

  // Fleet (live agent-runner snapshot, polled). Skeleton only on the first load;
  // background polls update in place without flashing the skeleton.
  fleet: null,
  fleetLoading: true,
  fleetRefreshing: false,
  fleetError: false,

  fetchFleet: async () => {
    set((s) => (s.fleet ? { fleetRefreshing: true } : { fleetLoading: true }))
    try {
      const fleet = await adminApi.getFleet()
      set({ fleet, fleetLoading: false, fleetRefreshing: false, fleetError: false })
    } catch {
      set({ fleetLoading: false, fleetRefreshing: false, fleetError: true })
    }
  },

  // Resource Quota (agent-runtime live usage vs allocated, polled). Same skeleton-on-
  // first-load + background-refresh shape as the fleet snapshot.
  resourceUsage: null,
  resourceUsageLoading: true,
  resourceUsageRefreshing: false,
  resourceUsageError: false,

  fetchResourceUsage: async () => {
    set((s) => (s.resourceUsage ? { resourceUsageRefreshing: true } : { resourceUsageLoading: true }))
    try {
      const resourceUsage = await adminApi.getResourceUsage()
      set({ resourceUsage, resourceUsageLoading: false, resourceUsageRefreshing: false, resourceUsageError: false })
    } catch {
      set({ resourceUsageLoading: false, resourceUsageRefreshing: false, resourceUsageError: true })
    }
  },

  // System Map (topology + live per-module health, own 5s poll). Latest snapshot
  // only — the byte-path flow is constant (not req/s-scaled), so there's no rate
  // buffer. Skeleton only on the first load; background polls update in place.
  systemHealth: null,
  systemHealthLoading: true,
  systemHealthRefreshing: false,
  systemHealthError: false,

  fetchSystemHealth: async () => {
    set((s) => (s.systemHealth ? { systemHealthRefreshing: true } : { systemHealthLoading: true }))
    try {
      const systemHealth = await adminApi.getSystemHealth()
      set({ systemHealth, systemHealthLoading: false, systemHealthRefreshing: false, systemHealthError: false })
    } catch {
      set({ systemHealthLoading: false, systemHealthRefreshing: false, systemHealthError: true })
    }
  },

  // Gateway traffic (agentgateway HTTP request counters, polled alongside the fleet).
  // The endpoint returns CUMULATIVE counters; we buffer per-destination cumulatives
  // { t, total, cp, ar } (server-clock) and derive the rolling-window count, current
  // req/s, and the sparkline for the selected scope in the view. The metric has no URL
  // path label, so the only path-ish dimension is the backend (cp=control-panel face,
  // ar=agent-runner pool). Time-capped to a little over the largest window.
  gateway: null,
  gatewayLoading: true,
  gatewayBuffer: [],  // [{ t, total, cp, ar }] oldest→newest, capped to GATEWAY_MAX_WINDOW_SEC
  // Selected trailing window (seconds) + destination scope for the count. Both persisted.
  gatewayWindowSec: safeStorage.getNumber('gateway-window-sec', 60, { min: 5, max: 3600 }),
  gatewayScope: safeStorage.getItem('gateway-scope') || 'all',  // 'all'|'control-panel'|'agent-runner'

  setGatewayWindowSec: (sec) => {
    safeStorage.setItem('gateway-window-sec', String(sec))
    set({ gatewayWindowSec: sec })
  },

  setGatewayScope: (scope) => {
    safeStorage.setItem('gateway-scope', scope)
    set({ gatewayScope: scope })
  },

  fetchGateway: async () => {
    try {
      const g = await adminApi.getGatewayMetrics()
      set((s) => {
        if (!g.available) return { gateway: g, gatewayLoading: false }
        const bb = g.by_backend || {}
        const sample = {
          t: g.scraped_at,
          total: g.total_requests,
          cp: bb['control-panel'] || 0,
          ar: bb['agent-runner'] || 0,
        }
        let buf = s.gatewayBuffer
        const last = buf.length ? buf[buf.length - 1] : null
        // Counter reset (gateway pod restart) => total dropped: start a fresh buffer.
        if (last && sample.total < last.total) buf = []
        // Append only when server time advanced (guards duplicate polls / clock skew).
        if (!last || sample.t > last.t) buf = [...buf, sample]
        // Time-cap the ring so the largest selectable window is always covered, no more.
        const cutoff = sample.t - (GATEWAY_MAX_WINDOW_SEC + 30)
        if (buf.length && buf[0].t < cutoff) buf = buf.filter((p) => p.t >= cutoff)
        return { gateway: g, gatewayLoading: false, gatewayBuffer: buf }
      })
    } catch {
      set({ gatewayLoading: false })
    }
  },

  // Audit log (cursor-paginated)
  auditEntries: [],
  auditTotal: null,
  auditLoading: true,
  auditCursorStack: [null],
  auditNextCursor: null,
  auditActionFilter: null,
  auditActorFilter: '',
  auditTargetFilter: '',
  auditSessionFilter: '',
  auditStartTime: null,
  auditEndTime: null,

  fetchAuditLog: async (append = false) => {
    const {
      auditActionFilter, auditActorFilter, auditTargetFilter, auditSessionFilter,
      auditStartTime, auditEndTime, auditEntries,
      auditCursorStack, auditNextCursor,
    } = get()
    set({ auditLoading: true })
    try {
      let cursor
      let nextStack = auditCursorStack
      if (append) {
        if (!auditNextCursor) {
          set({ auditLoading: false })
          return
        }
        nextStack = [...auditCursorStack, auditNextCursor]
        cursor = auditNextCursor
      } else {
        cursor = auditCursorStack.at(-1)
      }

      const params = { limit: 50 }
      if (cursor) params.before = cursor
      if (auditActionFilter && auditActionFilter !== '_actor' && auditActionFilter !== 'session') params.action = auditActionFilter
      if (auditActorFilter) params.actor = auditActorFilter
      if (auditTargetFilter) params.target = auditTargetFilter
      if (auditSessionFilter) params.session_id = auditSessionFilter
      if (auditStartTime) params.start = auditStartTime
      if (auditEndTime) params.end = auditEndTime

      const data = await adminApi.getAuditLog(params)
      set({
        auditEntries: append ? [...auditEntries, ...data.entries] : data.entries,
        auditTotal: data.total ?? null,
        auditNextCursor: data.next_cursor || null,
        auditCursorStack: nextStack,
        auditLoading: false,
      })
    } catch {
      set({ auditLoading: false })
    }
  },

  resetAuditCursors: () => set({
    auditCursorStack: [null], auditNextCursor: null, auditTotal: null, auditEntries: [],
  }),

  setAuditActionFilter: (filter) => set({
    auditActionFilter: filter, auditCursorStack: [null], auditNextCursor: null,
  }),
  setAuditActorFilter: (filter) => set({
    auditActorFilter: filter, auditCursorStack: [null], auditNextCursor: null,
  }),
  setAuditTargetFilter: (filter) => set({
    auditTargetFilter: filter, auditCursorStack: [null], auditNextCursor: null,
  }),
  setAuditSessionFilter: (filter) => set({
    auditSessionFilter: filter, auditCursorStack: [null], auditNextCursor: null,
  }),
  setAuditTimeRange: (start, end) => set({
    auditStartTime: start, auditEndTime: end,
    auditCursorStack: [null], auditNextCursor: null,
  }),

  // Audit chart data (separate from paginated entries)
  auditChartEntries: [],
  auditChartLoading: true,

  // Actor list for dropdown (fetched without actor filter)
  auditActors: [],

  fetchAuditActors: async () => {
    const { auditStartTime, auditEndTime } = get()
    try {
      const params = { limit: 200 }
      if (auditStartTime) params.start = auditStartTime
      if (auditEndTime) params.end = auditEndTime
      const data = await adminApi.getAuditLog(params)
      const counts = {}
      for (const e of data.entries) {
        if (e.actor) counts[e.actor] = (counts[e.actor] || 0) + 1
      }
      const actors = Object.entries(counts)
        .sort((a, b) => b[1] - a[1])
        .map(([actor, count]) => ({ actor, count }))
      set({ auditActors: actors })
    } catch {}
  },

  reset: () => set({
    users: [], usersLoading: true,
    selectedUser: null, drawerOpen: false,
    pendingUsers: [], pendingUsersLoading: true,
    fleet: null, fleetLoading: true, fleetRefreshing: false, fleetError: false,
    systemHealth: null, systemHealthLoading: true, systemHealthRefreshing: false, systemHealthError: false,
    gateway: null, gatewayLoading: true, gatewayBuffer: [],
    auditEntries: [], auditTotal: null, auditLoading: true,
    auditCursorStack: [null], auditNextCursor: null,
    auditActionFilter: null, auditActorFilter: '', auditTargetFilter: '', auditSessionFilter: '',
    auditStartTime: null, auditEndTime: null,
    auditChartEntries: [], auditChartLoading: true,
    auditActors: [],
  }),

  fetchAuditLogForCharts: async () => {
    const { auditActionFilter, auditActorFilter, auditTargetFilter, auditSessionFilter, auditStartTime, auditEndTime } = get()
    set({ auditChartLoading: true })
    try {
      const params = { limit: 200 }
      if (auditActionFilter && auditActionFilter !== '_actor' && auditActionFilter !== 'session') params.action = auditActionFilter
      if (auditActorFilter) params.actor = auditActorFilter
      if (auditTargetFilter) params.target = auditTargetFilter
      if (auditSessionFilter) params.session_id = auditSessionFilter
      if (auditStartTime) params.start = auditStartTime
      if (auditEndTime) params.end = auditEndTime
      const data = await adminApi.getAuditLog(params)
      set({ auditChartEntries: data.entries, auditChartLoading: false })
    } catch {
      set({ auditChartLoading: false })
    }
  },
}))

export default useAdminStore
