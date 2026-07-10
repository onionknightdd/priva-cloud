import { useState, useEffect, useMemo } from 'react'
import { Plus, Search, ChevronDown, FolderGit2, UsersRound } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import useSubagentsStore from '../../stores/subagentsStore'
import useUiStore from '@shared/stores/uiStore'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

// Agent list column for the content-only SubAgents view. `headerStart` is the
// back-button + section title supplied by SubAgentsPanel (mirrors MCPListSidebar).
// Agents are shown in collapsible PROJECT + USER groups — the same grouped design
// as the MCP panel (project {cwd}/.claude/agents ≈ workdir, user ~/.claude/agents).
export default function SubAgentsSidebar({ headerStart = null }) {
  const { t } = useTranslation()
  const list = useSubagentsStore((s) => s.list)
  const listLoading = useSubagentsStore((s) => s.listLoading)
  const selectedName = useSubagentsStore((s) => s.selectedName)
  const selectedScope = useSubagentsStore((s) => s.selectedScope)
  const selectedCwd = useSubagentsStore((s) => s.selectedCwd)
  const dirty = useSubagentsStore((s) => s.dirty)
  const selectAgent = useSubagentsStore((s) => s.selectAgent)
  const openScopePicker = useSubagentsStore((s) => s.openScopePicker)
  const loadList = useSubagentsStore((s) => s.loadList)
  const loadCatalog = useSubagentsStore((s) => s.loadCatalog)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)

  const [search, setSearch] = useState('')
  const [collapsedGroups, setCollapsedGroups] = useState({})

  useEffect(() => {
    loadList()
    loadCatalog()
  }, [loadList, loadCatalog])

  const guarded = (action) => {
    if (!dirty) {
      action()
      return
    }
    showConfirmDialog({
      title: t('subagents.unsavedTitle'),
      message: t('subagents.unsavedMessage'),
      confirmLabel: t('subagents.discardConfirm'),
      danger: true,
      onConfirm: action,
    })
  }

  const sections = useMemo(() => {
    const q = search.trim().toLowerCase()
    const matches = (a) =>
      !q || a.name.toLowerCase().includes(q) || (a.description || '').toLowerCase().includes(q)

    // Group project-scoped agents by workdir (blue folder), preserving backend order.
    const projectGroups = new Map()
    for (const a of list) {
      if (a.scope !== 'project' || !matches(a)) continue
      const cwd = a.cwd || ''
      if (!projectGroups.has(cwd)) projectGroups.set(cwd, [])
      projectGroups.get(cwd).push(a)
    }
    const userAgents = list.filter((a) => (a.scope || 'project') === 'user' && matches(a))

    return [
      ...[...projectGroups.entries()].map(([cwd, agents]) => ({
        key: `project:${cwd}`, scope: 'project', cwd: cwd || null,
        label: shortCwd(cwd), title: cwd, icon: FolderGit2, iconColor: 'var(--blue)', agents,
      })),
      ...(userAgents.length
        ? [{ key: 'user', scope: 'user', cwd: null, label: t('subagents.user'), title: '~/.claude/agents', icon: UsersRound, iconColor: 'var(--green)', agents: userAgents }]
        : []),
    ]
  }, [list, search, t])

  const hasAny = sections.length > 0

  return (
    <>
      {/* Header — back button + section title (left), new-agent (right) */}
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
          onClick={() => guarded(() => openScopePicker())}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)'; e.currentTarget.style.background = 'transparent' }}
          title={t('subagents.newScopeTitle')}
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
            placeholder={t('subagents.search')}
            value={search}
            onChange={(e) => setSearch(e.target.value)}
          />
        </div>
      </div>

      {/* Grouped list — collapsible PROJECT / USER sections */}
      <div className="flex-1 overflow-y-auto py-1" style={{ minHeight: 0 }}>
        {listLoading ? (
          <div className="flex flex-col gap-1 px-3">
            {[1, 2, 3].map((i) => (
              <div key={i} className="skeleton" style={{ height: 48, borderRadius: 2 }} />
            ))}
          </div>
        ) : !hasAny ? (
          <div className="px-3 py-4" style={{ color: 'var(--text-dim)', textAlign: 'center', fontSize: 13 }}>
            {list.length === 0 ? t('subagents.empty.list') : t('subagents.empty.search')}
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
                  <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--text-dim)' }}>{sec.agents.length}</span>
                </button>
                <AnimatedCollapse open={open}>
                  <div>
                    {sec.agents.map((agent) => {
                      const isActive =
                        selectedScope === sec.scope &&
                        selectedName === agent.name &&
                        (selectedCwd || null) === (sec.cwd || null)
                      return (
                        <button
                          key={`${sec.key}-${agent.name}`}
                          className="flex w-full items-start"
                          style={{
                            minHeight: 48,
                            background: isActive ? 'var(--bg-elevated)' : 'transparent',
                            border: 'none',
                            borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
                            cursor: 'pointer',
                            textAlign: 'left',
                            padding: '8px 12px',
                            transition: 'background 150ms ease',
                          }}
                          onClick={() => guarded(() => selectAgent(sec.scope, sec.cwd, agent.name))}
                          onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
                          onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                        >
                          <div className="flex flex-col gap-1 flex-1" style={{ minWidth: 0 }}>
                            <span className="font-semibold truncate" style={{ fontSize: 13, color: 'var(--text-primary)' }}>
                              {agent.name}
                            </span>
                            <span className="truncate" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
                              {agent.description || '—'}
                            </span>
                          </div>
                        </button>
                      )
                    })}
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
