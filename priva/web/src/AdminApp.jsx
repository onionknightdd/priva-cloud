import { useEffect, useState } from 'react'
import { LogOut, Users, ScrollText } from 'lucide-react'
import useAuthStore from './stores/authStore'
import useAdminStore from './stores/adminStore'
import LoginPage from './components/auth/LoginPage'
import UserManagement from './components/admin/UserManagement'
import AuditLog from './components/admin/AuditLog'
import UserEditDrawer from './components/admin/UserEditDrawer'
import safeStorage from './utils/safeStorage'

// Promoted admin SPA (Phase 2 §D2). Reuses the existing, design-spec-conformant
// admin components in a minimal shell; calls only /api/admin/* + /api/auth/login.
// Non-admins are redirected to the user app at /.

const NAV = [
  { id: 'users', label: 'Users', icon: Users },
  { id: 'audit', label: 'Audit', icon: ScrollText },
]

export default function AdminApp() {
  const loading = useAuthStore((s) => s.loading)
  const user = useAuthStore((s) => s.user)
  const initialize = useAuthStore((s) => s.initialize)
  const logout = useAuthStore((s) => s.logout)
  const drawerOpen = useAdminStore((s) => s.drawerOpen)
  const [section, setSection] = useState('users')

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
    <div className="flex flex-col" style={{ height: '100vh', background: 'var(--bg-base)', color: 'var(--text-primary)' }}>
      {/* Topbar */}
      <header
        className="flex items-center justify-between px-4 flex-shrink-0"
        style={{ height: 48, background: 'var(--bg-surface)', borderBottom: '1px solid var(--border)' }}
      >
        <div className="flex items-center gap-2">
          <span className="font-bold uppercase" style={{ fontSize: 13, letterSpacing: '0.06em' }}>Priva</span>
          <span style={{ color: 'var(--text-dim)' }}>·</span>
          <span className="font-semibold uppercase" style={{ fontSize: 12, letterSpacing: '0.06em', color: 'var(--blue)' }}>Admin</span>
        </div>
        <div className="flex items-center gap-3">
          <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>{user.username}</span>
          <button
            onClick={logout}
            title="Sign out"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <LogOut size={14} strokeWidth={1.5} />
          </button>
        </div>
      </header>

      <div className="flex flex-1" style={{ minHeight: 0 }}>
        {/* Left nav */}
        <nav
          className="flex flex-col flex-shrink-0 py-2"
          style={{ width: 200, background: 'var(--bg-surface)', borderRight: '1px solid var(--border)' }}
        >
          {NAV.map(({ id, label, icon: Icon }) => {
            const active = section === id
            return (
              <button
                key={id}
                onClick={() => setSection(id)}
                className="flex items-center gap-2 px-3 py-2 text-left"
                style={{
                  background: active ? 'var(--bg-elevated)' : 'transparent',
                  borderLeft: `2px solid ${active ? 'var(--blue)' : 'transparent'}`,
                  color: active ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontSize: 13,
                  fontWeight: active ? 600 : 400,
                  cursor: 'pointer',
                  transition: 'color 150ms ease, background 150ms ease',
                }}
              >
                <Icon size={16} strokeWidth={1.5} />
                <span>{label}</span>
              </button>
            )
          })}
        </nav>

        {/* Content */}
        <main className="flex-1 flex flex-col" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
          {section === 'users' && <UserManagement />}
          {section === 'audit' && <AuditLog />}
        </main>
      </div>

      {drawerOpen && <UserEditDrawer />}
    </div>
  )
}
