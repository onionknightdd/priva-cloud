import { useState } from 'react'
import { Search, Plus, ChevronDown, FolderGit2, Globe } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import useMcpStore from '../../stores/mcpStore'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

// Server list column for the content-only MCP view. `headerStart` is the
// back-button + section title supplied by MCPPanel (mirrors SkillList). Servers
// are shown in collapsible PROJECT + GLOBAL groups — the same grouped design as
// the Skills panel (project `.mcp.json` ≈ workdir, global `settings.json` ≈ personal).
export default function MCPListSidebar({ headerStart = null }) {
  const { t } = useTranslation()

  const servers = useMcpStore((s) => s.servers)
  const serversLoading = useMcpStore((s) => s.serversLoading)
  const searchQuery = useMcpStore((s) => s.searchQuery)
  const setSearchQuery = useMcpStore((s) => s.setSearchQuery)
  const selectedServer = useMcpStore((s) => s.selectedServer)
  const selectServer = useMcpStore((s) => s.selectServer)
  const openScopePicker = useMcpStore((s) => s.openScopePicker)

  const [collapsedGroups, setCollapsedGroups] = useState({})

  const q = searchQuery.toLowerCase()
  const matchesSearch = (s) => s.name.toLowerCase().includes(q)

  // Grouped sections mirror the Skills panel: one collapsible group per project
  // workdir (blue folder, ≈ Skills workdir) + a GLOBAL group (green globe, ≈
  // Skills personal). Project groups preserve the backend's workdir order.
  const projectGroups = new Map()
  for (const s of servers) {
    if (s.level !== 'project' || !matchesSearch(s)) continue
    const cwd = s.cwd || ''
    if (!projectGroups.has(cwd)) projectGroups.set(cwd, [])
    projectGroups.get(cwd).push(s)
  }
  const globalServers = servers.filter((s) => s.level === 'global' && matchesSearch(s))

  const sections = [
    ...[...projectGroups.entries()].map(([cwd, srvs]) => ({
      key: `project:${cwd}`, level: 'project', cwd: cwd || null,
      label: shortCwd(cwd), title: cwd, icon: FolderGit2, iconColor: 'var(--blue)', servers: srvs,
    })),
    ...(globalServers.length
      ? [{ key: 'global', level: 'global', cwd: null, label: t('mcp.global'), title: '~/.claude/settings.json', icon: Globe, iconColor: 'var(--green)', servers: globalServers }]
      : []),
  ]

  const hasAny = sections.length > 0

  return (
    <>
      {/* Header — back button + section title (left), add-server (right) */}
      <div
        className="flex items-center justify-between flex-shrink-0 px-3"
        style={{ height: 40, borderBottom: '1px solid var(--border-subtle)' }}
      >
        {headerStart}
        <button
          className="flex items-center justify-center flex-shrink-0"
          style={{
            width: 28, height: 28, background: 'transparent', border: 'none',
            borderRadius: 4, cursor: 'pointer', color: 'var(--text-dim)',
            transition: 'color 150ms ease, background 150ms ease',
          }}
          onClick={openScopePicker}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)'; e.currentTarget.style.background = 'transparent' }}
          title={t('mcp.addServer')}
        >
          <Plus size={16} strokeWidth={1.5} />
        </button>
      </div>

      {/* Search */}
      <div className="px-3 py-2 flex-shrink-0">
        <div
          className="flex items-center gap-1 px-2 py-1"
          style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4 }}
        >
          <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
          <input
            className="flex-1"
            style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', minWidth: 0, fontSize: 13 }}
            placeholder={t('mcp.search')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Grouped list — collapsible PROJECT / GLOBAL sections */}
      <div className="flex-1 overflow-y-auto py-1" style={{ minHeight: 0 }}>
        {serversLoading ? (
          <div className="flex flex-col gap-1 px-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ height: 34, borderRadius: 2 }} />
            ))}
          </div>
        ) : !hasAny ? (
          <div className="px-3 py-4" style={{ color: 'var(--text-dim)', textAlign: 'center', fontSize: 13 }}>
            {t('mcp.noServers')}
          </div>
        ) : (
          sections.map((sec) => {
            const open = !collapsedGroups[sec.key]
            const Icon = sec.icon
            return (
              <div key={sec.key}>
                <button
                  className="flex items-center gap-1 w-full px-2 py-1"
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
                  title={sec.title}
                  onClick={() => setCollapsedGroups((m) => ({ ...m, [sec.key]: open }))}
                >
                  <AnimatedChevron open={open} style={{ color: 'var(--text-dim)' }}>
                    <ChevronDown size={12} strokeWidth={1.5} />
                  </AnimatedChevron>
                  <Icon size={13} strokeWidth={1.5} style={{ flexShrink: 0, color: sec.iconColor }} />
                  <span className="uppercase font-semibold truncate flex-1" style={{ color: 'var(--text-dim)', letterSpacing: '0.05em', fontSize: 11 }}>
                    {sec.label}
                  </span>
                  <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--text-dim)' }}>{sec.servers.length}</span>
                </button>
                <AnimatedCollapse open={open}>
                  <div>
                    {sec.servers.map((srv) => (
                      <ServerItem
                        key={`${sec.key}-${srv.name}`}
                        server={srv}
                        isActive={selectedServer?.level === sec.level && selectedServer?.name === srv.name && (selectedServer?.cwd || null) === (sec.cwd || null)}
                        onClick={() => selectServer(sec.level, srv.name, sec.cwd)}
                      />
                    ))}
                  </div>
                </AnimatedCollapse>
              </div>
            )
          })
        )}
      </div>
    </>
  )
}

function ServerItem({ server, isActive, onClick }) {
  return (
    <div
      className="flex items-center gap-2 px-3 py-2"
      style={{
        background: isActive ? 'var(--bg-elevated)' : 'transparent',
        borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
        cursor: 'pointer',
        transition: 'background 150ms ease',
      }}
      onClick={onClick}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      <span
        className="truncate flex-1"
        style={{ color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)', fontSize: 14 }}
      >
        {server.name}
      </span>
      <span
        className="uppercase flex-shrink-0 px-1"
        style={{
          fontSize: 10, fontWeight: 600, letterSpacing: '0.06em',
          color: server.type === 'http' ? 'var(--cyan)' : 'var(--purple)',
          border: `1px solid ${server.type === 'http' ? 'var(--cyan)' : 'var(--purple)'}`,
          borderRadius: 2, lineHeight: '16px',
        }}
      >
        {server.type}
      </span>
    </div>
  )
}
