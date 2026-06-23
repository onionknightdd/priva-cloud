import { useEffect, useState, useCallback, useRef } from 'react'
import { LogOut, Users, ScrollText, LayoutDashboard, Settings, PanelLeftClose, PanelLeftOpen, Server, Activity } from 'lucide-react'
import useAuthStore from '@shared/stores/authStore'
import useAdminStore from './stores/adminStore'
import { useResizable } from '@shared/hooks/useResizable'
import Tabs from '@shared/components/shared/Tabs'
import LoginPage from '@shared/components/auth/LoginPage'
import UserManagement from './components/admin/UserManagement'
import AuditLog from './components/admin/AuditLog'
import FleetView from './components/admin/FleetView'
import UserEditDrawer from './components/admin/UserEditDrawer'
import AgentRunnerSandbox from './components/admin/AgentRunnerSandbox'
import safeStorage from '@shared/utils/safeStorage'

// Promoted admin SPA (Phase 2 §D2). Reuses the existing, design-spec-conformant
// admin components in a minimal shell; calls only /api/admin/* + /api/auth/login.
// Non-admins are redirected to the user app at /.

// Top-level tabs (segmented switch under the sidebar title) → each owns a nav group.
const TABS = [
  { id: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { id: 'config', label: 'Configurations', icon: Settings },
]

const NAV = {
  dashboard: [
    { id: 'fleet', label: 'Fleet', icon: Activity },
    { id: 'users', label: 'Users', icon: Users },
    { id: 'audit', label: 'Audit', icon: ScrollText },
  ],
  config: [
    { id: 'sandbox', label: 'Agent Runner Sandbox', icon: Server },
  ],
}

const SIDEBAR_MIN = 180
const SIDEBAR_MAX = 480
const SIDEBAR_DEFAULT = 240
const SIDEBAR_COLLAPSED = 48

// Empty-state for the Configurations tab until real settings pages are wired.
function ConfigPlaceholder() {
  return (
    <div className="flex flex-1 items-center justify-center" style={{ color: 'var(--text-dim)' }}>
      <div className="flex flex-col items-center gap-2">
        <Settings size={24} strokeWidth={1.5} />
        <span className="text-sm">No settings yet</span>
      </div>
    </div>
  )
}

export default function AdminApp() {
  const loading = useAuthStore((s) => s.loading)
  const user = useAuthStore((s) => s.user)
  const initialize = useAuthStore((s) => s.initialize)
  const logout = useAuthStore((s) => s.logout)
  const drawerOpen = useAdminStore((s) => s.drawerOpen)
  const [activeTab, setActiveTab] = useState(() => (safeStorage.getItem('admin-tab') === 'config' ? 'config' : 'dashboard'))
  const [section, setSection] = useState(() => {
    const t = safeStorage.getItem('admin-tab') === 'config' ? 'config' : 'dashboard'
    return NAV[t][0]?.id ?? null
  })

  const selectTab = useCallback((id) => {
    setActiveTab(id)
    safeStorage.setItem('admin-tab', id)
    const items = NAV[id] || []
    setSection((s) => (items.some((n) => n.id === s) ? s : (items[0]?.id ?? null)))
  }, [])

  // Resizable + collapsible sidebar (design spec: 180–480px, collapse to 48px, persisted).
  const [sidebarWidth, setSidebarWidth] = useState(() => {
    const saved = parseInt(safeStorage.getItem('sidebar-width'), 10)
    return Number.isFinite(saved) ? Math.min(Math.max(saved, SIDEBAR_MIN), SIDEBAR_MAX) : SIDEBAR_DEFAULT
  })
  const [collapsed, setCollapsed] = useState(() => safeStorage.getItem('sidebar-collapsed') === '1')
  const [hoverHandle, setHoverHandle] = useState(false)
  const lastDownRef = useRef(0)

  const onSidebarResize = useCallback((w) => {
    setSidebarWidth(w)
    safeStorage.setItem('sidebar-width', String(Math.round(w)))
  }, [])

  const { dragging, onMouseDown } = useResizable({
    initial: sidebarWidth,
    min: SIDEBAR_MIN,
    max: SIDEBAR_MAX,
    direction: 'right',
    onResize: onSidebarResize,
  })

  const toggleCollapsed = useCallback(() => {
    setCollapsed((c) => {
      const next = !c
      safeStorage.setItem('sidebar-collapsed', next ? '1' : '0')
      return next
    })
  }, [])

  // Drag to resize; a quick second press on the handle toggles collapse. The resize
  // hook's full-screen drag overlay swallows the native dblclick, so detect it here.
  const handleDown = useCallback((e) => {
    const now = e.timeStamp
    if (now - lastDownRef.current < 350) {
      lastDownRef.current = 0
      toggleCollapsed()
      return
    }
    lastDownRef.current = now
    if (!collapsed) onMouseDown(e)
  }, [collapsed, onMouseDown, toggleCollapsed])

  useEffect(() => {
    document.documentElement.dataset.theme = safeStorage.getItem('theme') || 'light'
  }, [])
  useEffect(() => { initialize() }, [initialize])

  if (loading) {
    return <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }} />
  }
  if (!user) {
    return <LoginPage />
  }
  if (user.role !== 'admin') {
    // Non-admins have no business in the admin SPA — send them to the user app.
    window.location.href = '/'
    return null
  }

  return (
    <div className="flex" style={{ height: '100vh', background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      {/* Left sidebar — brand header, nav, user/logout footer */}
      <nav
        className="flex flex-col flex-shrink-0"
        style={{
          width: collapsed ? SIDEBAR_COLLAPSED : sidebarWidth,
          background: 'var(--bg-surface)',
          borderRight: '1px solid var(--border)',
          overflow: 'hidden',
          transition: dragging ? 'none' : 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* Brand header + collapse toggle */}
        <div
          className="flex items-center flex-shrink-0"
          style={{
            height: 48,
            gap: 8,
            padding: collapsed ? 0 : '0 8px 0 12px',
            justifyContent: collapsed ? 'center' : 'space-between',
            borderBottom: '1px solid var(--border)',
            whiteSpace: 'nowrap',
          }}
        >
          {collapsed ? (
            <button
              onClick={toggleCollapsed}
              title="Expand sidebar"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
            >
              <PanelLeftOpen size={16} strokeWidth={1.5} />
            </button>
          ) : (
            <>
              <div className="flex items-center gap-2" style={{ overflow: 'hidden' }}>
                <span className="font-bold uppercase" style={{ fontSize: 16, letterSpacing: '0.06em' }}>Priva</span>
                <span style={{ color: 'var(--text-dim)', fontSize: 16 }}>·</span>
                <span className="font-semibold uppercase" style={{ fontSize: 14, letterSpacing: '0.06em', color: 'var(--blue)' }}>Admin</span>
              </div>
              <button
                onClick={toggleCollapsed}
                title="Collapse sidebar"
                className="flex-shrink-0"
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              >
                <PanelLeftClose size={16} strokeWidth={1.5} />
              </button>
            </>
          )}
        </div>

        {/* Tab switch — sliding segmented control (hidden when collapsed). Same
            animated indicator as the user app's Tabs (variant="frame"). */}
        {!collapsed && (
          <div className="px-2 py-2 flex-shrink-0">
            <Tabs
              tabs={TABS}
              variant="frame"
              layoutId="admin-tab-indicator"
              activeKey={activeTab}
              onChange={(index, tab) => selectTab(tab.id)}
              className="flex"
              style={{ background: 'var(--bg-base)', border: '1px solid var(--border)', borderRadius: 4, padding: 2, gap: 2 }}
              buttonClassName="flex-1"
              buttonStyle={{ minWidth: 0, height: 30, padding: '0 8px', borderRadius: 3, fontSize: 12, display: 'flex', alignItems: 'center', justifyContent: 'center' }}
              getButtonStyle={({ active }) => ({ fontWeight: active ? 600 : 400 })}
              indicatorStyle={{ borderRadius: 3 }}
              renderLabel={(tab) => {
                const Icon = tab.icon
                return (
                  <span className="flex items-center justify-center gap-1" style={{ minWidth: 0 }}>
                    <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
                    <span className="truncate">{tab.label}</span>
                  </span>
                )
              }}
            />
          </div>
        )}

        {/* Nav items */}
        <div className="flex flex-col flex-1 py-2" style={{ minHeight: 0, overflowY: 'auto' }}>
          {NAV[activeTab].map(({ id, label, icon: Icon }) => {
            const active = section === id
            return (
              <button
                key={id}
                onClick={() => setSection(id)}
                title={collapsed ? label : undefined}
                className="flex items-center gap-2 text-left"
                style={{
                  padding: collapsed ? '8px 0' : '8px 12px',
                  justifyContent: collapsed ? 'center' : 'flex-start',
                  background: active ? 'var(--bg-elevated)' : 'transparent',
                  borderLeft: `2px solid ${active ? 'var(--blue)' : 'transparent'}`,
                  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  cursor: 'pointer',
                  whiteSpace: 'nowrap',
                  overflow: 'hidden',
                  transition: 'color 150ms ease, background 150ms ease',
                }}
              >
                <Icon size={16} strokeWidth={1.5} className="flex-shrink-0" />
                {!collapsed && <span>{label}</span>}
              </button>
            )
          })}
        </div>

        {/* User + logout footer */}
        <div
          className="flex items-center flex-shrink-0"
          style={{
            gap: 8,
            padding: collapsed ? '8px 0' : '8px 12px',
            justifyContent: collapsed ? 'center' : 'space-between',
            borderTop: '1px solid var(--border)',
            whiteSpace: 'nowrap',
          }}
        >
          {!collapsed && (
            <span className="truncate" style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{user.username}</span>
          )}
          <button
            onClick={logout}
            title={collapsed ? `Sign out (${user.username})` : 'Sign out'}
            className="flex-shrink-0"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <LogOut size={14} strokeWidth={1.5} />
          </button>
        </div>
      </nav>

      {/* Sidebar resize handle — drag to resize, double-click to collapse */}
      <div
        onMouseDown={handleDown}
        onMouseEnter={() => setHoverHandle(true)}
        onMouseLeave={() => setHoverHandle(false)}
        className="flex-shrink-0"
        style={{
          width: 4,
          marginLeft: -2,
          marginRight: -2,
          zIndex: 20,
          cursor: collapsed ? 'pointer' : 'col-resize',
          background: (hoverHandle || dragging) ? 'var(--blue)' : 'transparent',
          transition: 'background 150ms ease',
        }}
        title={collapsed ? 'Double-click to expand' : 'Drag to resize · double-click to collapse'}
      />

      {/* Content */}
      <main className="flex-1 flex flex-col" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
        {activeTab === 'dashboard' && section === 'fleet' && <FleetView />}
        {activeTab === 'dashboard' && section === 'users' && <UserManagement />}
        {activeTab === 'dashboard' && section === 'audit' && <AuditLog />}
        {activeTab === 'config' && section === 'sandbox' && <AgentRunnerSandbox />}
        {activeTab === 'config' && section !== 'sandbox' && <ConfigPlaceholder />}
      </main>

      {drawerOpen && <UserEditDrawer />}
    </div>
  )
}
