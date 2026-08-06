import { useEffect, useRef, useState, useMemo } from 'react'
import { createPortal, flushSync } from 'react-dom'
import {
  Trash2, ChevronDown, ChevronRight, FolderBookmark, MoreHorizontal,
  RefreshCw, Settings, Search, X, Pencil, Flag, GitBranch, Pin, Archive, SlidersVertical, SquarePen,
  Bot, PanelLeftClose, Plus, CalendarClock, PackageSearch, ChartColumnBig,
  Maximize2, Minimize2, FolderOpenDot, FolderGit2, LogOut,
  BarChart3, TrendingUp, ScrollText, FileText, FolderOpen,
  Cable, Webhook, BrainCircuit, NotebookPen, SquareSlash,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useSidebarStore from '../../stores/sidebarStore'
import useUiStore from '@shared/stores/uiStore'
import useSplitStore from '../../stores/splitStore'
import useAuthStore from '@shared/stores/authStore'
import useUserDataStore from '../../stores/userDataStore'
import useSessionStatusStore from '../../stores/sessionStatusStore'
import {
  deleteSession as apiDeleteSession,
  renameSession as apiRenameSession,
  tagSession as apiTagSession,
} from '../../api/sessions'
import { openSession, newDraftSession } from '../../session/openSession'
import { stopSessionStream } from '../../hooks/useSSE'
import { getActiveKey, removeRuntime, resolveKey } from '../../stores/runtime/registry'
import SidebarResizer from './SidebarResizer'
import SettingsPopover from '../settings/SettingsPopover'
import NavItem from '@shared/components/shared/NavItem'
import PanelHeader from '@shared/components/shared/PanelHeader'
import Chip from '@shared/components/shared/Chip'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import DirectoryPicker from '../shared/DirectoryPicker'
import TagFilterChip from '../shared/TagFilterChip'
import safeStorage from '@shared/utils/safeStorage'

// Compact cwd label for a group header: the last path segment (full path in title).
function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

function ProjectGroupIcon({ expanded }) {
  return (
    <span className="project-group-icon" aria-hidden="true">
      <FolderBookmark className="project-group-folder-icon" size={19} strokeWidth={1.5} />
      <span
        className="project-group-chevron-icon"
        style={{ '--project-group-chevron-rotation': `${expanded ? 0 : -90}deg` }}
      >
        <ChevronDown size={19} strokeWidth={1.5} />
      </span>
    </span>
  )
}

// Dropdown item style shared by the workdir (sliders) menu.
const WD_MENU_ITEM_STYLE = {
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  fontSize: 13,
  paddingTop: 6,
  paddingBottom: 6,
  transition: 'background 150ms ease',
}

// Data & Usage sub-sections (icons mirror the former UserDataPanel).
const DATA_SECTIONS = [
  { id: 'usage', icon: BarChart3, labelKey: 'userData.usage' },
  { id: 'analytics', icon: TrendingUp, labelKey: 'userData.analytics' },
  { id: 'audit', icon: ScrollText, labelKey: 'userData.auditLog' },
  { id: 'files', icon: FileText, labelKey: 'userData.uploadedFiles' },
  { id: 'fileexplorer', icon: FolderOpen, labelKey: 'userData.fileExplorer' },
]

// Plugins/Customize sub-sections. Skills is live (Phase 2); the rest render a
// "coming soon" placeholder until each is redesigned one by one.
const PLUGINS_SECTIONS = [
  { id: 'skills', icon: ScrollText, labelKey: 'tabs.skills' },
  { id: 'mcp', icon: Cable, labelKey: 'tabs.mcp' },
  { id: 'hooks', icon: Webhook, labelKey: 'tabs.hooks' },
  { id: 'subagents', icon: BrainCircuit, labelKey: 'tabs.subagents' },
  { id: 'commands', icon: SquareSlash, labelKey: 'tabs.commands' },
  { id: 'memory', icon: NotebookPen, labelKey: 'tabs.memory' },
]

// Session titles align with the project name column. Scheduled sessions keep
// their deeper nesting while their CalendarClock icon remains visible.
const PROJECT_SESSION_INDENT = 53
const SCHEDULED_SESSION_INDENT = 59

function SessionItem({
  session, isActive, openMenuId, menuRef, onSelect, onMenuToggle,
  onDelete, onRenameStart, onTagStart, onPinToggle, onArchive, renameEditingId,
  onRenameCommit, onRenameCancel, onDragStartSession, onDragEndSession, t, indent = 0,
}) {
  // Resolve rotated session ids (resume mints a new id per turn) so the title
  // follows the live runtime even while the row still holds a former id.
  const sessionStatus = useSessionStatusStore((s) => s.statuses[resolveKey(session.sessionId || session.id)])
  const titleStatusClass = sessionStatus === 'running'
    ? ' is-running'
    : sessionStatus === 'attention'
      ? ' is-attention'
      : sessionStatus === 'unseen'
        ? ' is-unseen'
        : ''
  const [renameValue, setRenameValue] = useState(session.name || '')
  useEffect(() => {
    if (renameEditingId === session.id) setRenameValue(session.name || '')
  }, [renameEditingId, session.id, session.name])
  const editing = renameEditingId === session.id
  const isProject = session.sessionSource === 'project'
  const menuItemStyle = {
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    fontSize: 13,
    paddingTop: 6,
    paddingBottom: 6,
    transition: 'background 150ms ease',
  }

  return (
    <div
      className="sidebar-session-item flex flex-col gap-1 px-3 group"
      draggable={!editing}
      style={{
        position: 'relative',
        paddingTop: 4,
        paddingBottom: 4,
        // Keep the hover box on the same 16px-to-16px gutter as the main menu;
        // the text indent remains independent so project/session names align.
        marginLeft: 16,
        marginRight: 16,
        paddingLeft: Math.max(0, (indent || 12) - 16),
        paddingRight: 8,
        background: isActive ? 'var(--bg-elevated)' : 'transparent',
        borderRadius: 8,
        color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: editing ? 'default' : 'pointer',
        fontSize: 14,
        transition: 'background 150ms ease',
      }}
      onDragStart={(e) => {
        if (editing) {
          e.preventDefault()
          return
        }
        e.dataTransfer.effectAllowed = 'copy'
        e.dataTransfer.setData('application/priva-session', JSON.stringify({
          sessionId: session.sessionId || session.id,
          name: session.name,
          cwd: session.cwd,
        }))
        e.dataTransfer.setData('text/plain', JSON.stringify({
          sessionId: session.sessionId || session.id,
          name: session.name,
          cwd: session.cwd,
        }))
        const dragSession = {
          sessionId: session.sessionId || session.id,
          name: session.name,
          cwd: session.cwd,
        }
        flushSync(() => {
          onDragStartSession?.(dragSession)
        })
      }}
      onDragEnd={() => {
        onDragEndSession?.()
      }}
      onClick={() => { if (!editing) onSelect(session) }}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      <div className="flex items-center gap-2 min-w-0">
        {editing ? (
          <input
            type="text"
            autoFocus
            value={renameValue}
            onChange={(e) => setRenameValue(e.target.value)}
            onClick={(e) => e.stopPropagation()}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                onRenameCommit(session, renameValue)
              } else if (e.key === 'Escape') {
                e.preventDefault()
                onRenameCancel()
              }
            }}
            onBlur={() => onRenameCommit(session, renameValue)}
            style={{
              flex: 1,
              minWidth: 0,
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 2,
              color: 'var(--text-primary)',
              outline: 'none',
              fontSize: 14,
              padding: '2px 4px',
            }}
          />
        ) : (
          <>
            {session.origin === 'scheduler' && (
              <CalendarClock
                size={13}
                strokeWidth={1.5}
                style={{ color: 'var(--sidebar-icon-color)', flexShrink: 0 }}
                title={session.schedulerJobName ? `${t('sidebar.scheduled', { defaultValue: 'scheduled' })} · ${session.schedulerJobName}` : t('sidebar.scheduled', { defaultValue: 'scheduled' })}
              />
            )}
            <span
              className={`sidebar-session-title flex-1 truncate${titleStatusClass}`}
              style={{ minWidth: 0, fontSize: 13, lineHeight: 1.2 }}
            >
              {session.name}
            </span>
          </>
        )}
        {session.forkCount > 0 && !editing && (
          <span
            className="inline-flex items-center gap-1"
            style={{
              color: 'var(--cyan)',
              fontSize: 12,
              fontWeight: 600,
              flexShrink: 0,
            }}
            title={`${session.forkCount} fork${session.forkCount === 1 ? '' : 's'}`}
          >
            <GitBranch size={12} strokeWidth={1.5} />
            {session.forkCount}
          </span>
        )}
        {session.pinned && !editing && (
          <button
            type="button"
            title={t('sidebar.unpin')}
            style={{
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--cyan)',
              padding: 2,
              display: 'flex',
              alignItems: 'center',
              flexShrink: 0,
              transition: 'color 150ms ease',
            }}
            onClick={(e) => { e.stopPropagation(); onPinToggle(session) }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--cyan)' }}
          >
            <Pin size={12} strokeWidth={1.5} />
          </button>
        )}
        {isProject && !editing && (
          <div className="relative" ref={openMenuId === session.id ? menuRef : undefined}>
            <button
              style={{
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-dim)',
                padding: 2,
                // Always visible (per spec) — brightens on hover.
                opacity: 1,
                transition: 'color 150ms ease',
              }}
              onClick={(e) => {
                e.stopPropagation()
                onMenuToggle(openMenuId === session.id ? null : session.id)
              }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => {
                if (openMenuId !== session.id) {
                  e.currentTarget.style.color = 'var(--text-dim)'
                }
              }}
            >
              <MoreHorizontal size={12} strokeWidth={1.5} />
            </button>
            {openMenuId === session.id && (
              <div
                className="absolute"
                style={{
                  top: '100%',
                  right: 0,
                  marginTop: 4,
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border)',
                  borderRadius: 4,
                  zIndex: 50,
                  minWidth: 124,
                  overflow: 'hidden',
                }}
              >
                <button
                  className="flex items-center gap-2 px-3 w-full"
                  style={{ ...menuItemStyle, color: 'var(--text-primary)' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onMenuToggle(null)
                    onPinToggle(session)
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Pin size={13} strokeWidth={1.5} />
                  {session.pinned ? t('sidebar.unpin') : t('sidebar.pin')}
                </button>
                <button
                  className="flex items-center gap-2 px-3 w-full"
                  style={{ ...menuItemStyle, color: 'var(--text-primary)' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onMenuToggle(null)
                    onRenameStart(session)
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Pencil size={13} strokeWidth={1.5} />
                  {t('sidebar.rename')}
                </button>
                <button
                  className="flex items-center gap-2 px-3 w-full"
                  style={{ ...menuItemStyle, color: 'var(--text-primary)' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onMenuToggle(null)
                    onTagStart(session, e.currentTarget.getBoundingClientRect())
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Flag size={13} strokeWidth={1.5} />
                  {session.tag ? t('sidebar.changeTag') : t('sidebar.setTag')}
                </button>
                <div style={{ height: 1, background: 'var(--border-subtle)' }} />
                <button
                  className="flex items-center gap-2 px-3 w-full"
                  style={{ ...menuItemStyle, color: 'var(--text-primary)' }}
                  onClick={(e) => {
                    e.stopPropagation()
                    onMenuToggle(null)
                    onArchive(session)
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Archive size={13} strokeWidth={1.5} />
                  {t('sidebar.archive')}
                </button>
                <button
                  className="flex items-center gap-2 px-3 w-full"
                  style={{ ...menuItemStyle, color: 'var(--red)' }}
                  onClick={(e) => {
                    onMenuToggle(null)
                    onDelete(e, session)
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <Trash2 size={13} strokeWidth={1.5} />
                  {t('sidebar.delete')}
                </button>
              </div>
            )}
          </div>
        )}
      </div>
      {session.tag && !editing && (
        <div className="flex items-center gap-1" style={{ paddingLeft: 19 }}>
          <span
            className="inline-flex items-center gap-1 px-2 uppercase"
            style={{
              background: 'transparent',
              border: '1px solid var(--border-subtle)',
              borderLeft: '1px solid var(--orange)',
              borderRadius: 2,
              color: 'var(--text-dim)',
              fontSize: 11,
              letterSpacing: '0.06em',
              fontWeight: 600,
              padding: '1px 6px',
              maxWidth: '100%',
            }}
            title={session.tag}
          >
            <Flag size={11} strokeWidth={1.5} style={{ color: 'var(--sidebar-icon-color)', flexShrink: 0 }} />
            <span className="truncate">{session.tag}</span>
          </span>
        </div>
      )}
    </div>
  )
}

function TagPopover({ session, onClose, recentTags, onSaved }) {
  const { t } = useTranslation()
  const [value, setValue] = useState(session.tag || '')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)

  const commit = async (nextTag) => {
    if (saving) return
    setSaving(true)
    setError(null)
    try {
      await apiTagSession(session.sessionId || session.id, nextTag || null)
      onSaved(session, nextTag || null)
      onClose()
    } catch (e) {
      setError(String(e?.message || e))
      setSaving(false)
    }
  }

  return (
    <div
      onClick={(e) => e.stopPropagation()}
      style={{
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        zIndex: 60,
        minWidth: 240,
        maxWidth: 'calc(100vw - 24px)',
        padding: 10,
      }}
    >
      <input
        autoFocus
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        placeholder={t('sidebar.tagPlaceholder')}
        onKeyDown={(e) => {
          if (e.key === 'Enter') { e.preventDefault(); commit(value.trim()) }
          else if (e.key === 'Escape') { e.preventDefault(); onClose() }
        }}
        style={{
          width: '100%',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 2,
          color: 'var(--text-primary)',
          padding: '4px 6px',
          fontSize: 13,
          outline: 'none',
          marginBottom: 6,
        }}
      />
      {recentTags.length > 0 && (
        <div className="flex flex-wrap gap-1" style={{ marginBottom: 6 }}>
          {recentTags.slice(0, 6).map((tag) => (
            <button
              key={tag}
              type="button"
              onClick={() => setValue(tag)}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 2,
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: 12,
                padding: '1px 6px',
              }}
            >
              {tag}
            </button>
          ))}
        </div>
      )}
      {error && (
        <div style={{ color: 'var(--red)', fontSize: 12, marginBottom: 6 }}>{error}</div>
      )}
      <div className="flex justify-end gap-1">
        <button
          type="button"
          onClick={() => commit('')}
          disabled={saving}
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 2,
            color: 'var(--text-secondary)',
            cursor: saving ? 'default' : 'pointer',
            fontSize: 12,
            padding: '2px 8px',
          }}
        >
          {t('sidebar.tagClear')}
        </button>
        <button
          type="button"
          onClick={() => commit(value.trim())}
          disabled={saving}
          style={{
            background: 'var(--blue)',
            border: 'none',
            borderRadius: 2,
            color: 'var(--text-inverse)',
            cursor: saving ? 'default' : 'pointer',
            fontSize: 12,
            fontWeight: 600,
            padding: '2px 10px',
          }}
        >
          {t('sidebar.tagSave')}
        </button>
      </div>
    </div>
  )
}

export default function Sidebar() {
  const { t } = useTranslation()
  const width = useSidebarStore((s) => s.width)
  const collapsed = useSidebarStore((s) => s.collapsed)
  const sessions = useSidebarStore((s) => s.sessions)
  const groups = useSidebarStore((s) => s.groups)
  const activeCwd = useSidebarStore((s) => s.activeCwd)
  const expandedCwds = useSidebarStore((s) => s.expandedCwds)
  const expandedScheduledCwds = useSidebarStore((s) => s.expandedScheduledCwds)
  const toggleGroup = useSidebarStore((s) => s.toggleGroup)
  const toggleScheduledGroup = useSidebarStore((s) => s.toggleScheduledGroup)
  const setAllGroupsExpanded = useSidebarStore((s) => s.setAllGroupsExpanded)
  const fetchMoreInGroup = useSidebarStore((s) => s.fetchMoreInGroup)
  const groupLoadingCwd = useSidebarStore((s) => s.groupLoadingCwd)
  const activeSessionId = useSidebarStore((s) => s.activeSessionId)
  const setActiveSessionId = useSidebarStore((s) => s.setActiveSessionId)
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed)
  const setCollapsed = useSidebarStore((s) => s.setCollapsed)
  const fetchSessions = useSidebarStore((s) => s.fetchSessions)
  const sessionsLoading = useSidebarStore((s) => s.sessionsLoading)
  const togglePinSession = useSidebarStore((s) => s.togglePinSession)
  const archiveSessionLocal = useSidebarStore((s) => s.archiveSessionLocal)
  const togglePinWorkdir = useSidebarStore((s) => s.togglePinWorkdir)
  const archiveWorkdirLocal = useSidebarStore((s) => s.archiveWorkdirLocal)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const toggleSettingsPopover = useUiStore((s) => s.toggleSettingsPopover)
  const activeNavTab = useUiStore((s) => s.activeNavTab)
  const setActiveNavTab = useUiStore((s) => s.setActiveNavTab)
  const splitPanes = useSplitStore((s) => s.panes)
  const openSessionInActivePane = useSplitStore((s) => s.openSessionInActivePane)
  const resetSplit = useSplitStore((s) => s.reset)
  const beginSessionDrag = useSplitStore((s) => s.beginSessionDrag)
  const endSessionDrag = useSplitStore((s) => s.endSessionDrag)
  const authUser = useAuthStore((s) => s.user)
  const logout = useAuthStore((s) => s.logout)
  const activeSection = useUserDataStore((s) => s.activeSection)
  const setActiveSection = useUserDataStore((s) => s.setActiveSection)
  const activePluginSection = useUiStore((s) => s.activePluginSection)
  const setActivePluginSection = useUiStore((s) => s.setActivePluginSection)
  const listRef = useRef(null)
  const [openMenuId, setOpenMenuId] = useState(null)
  const [openWorkdirMenu, setOpenWorkdirMenu] = useState(null) // cwd whose workdir menu is open
  const [searchQuery, setSearchQuery] = useState('')
  const [searchOpen, setSearchOpen] = useState(false)
  const [dataMenuOpen, setDataMenuOpen] = useState(activeNavTab === 'userdata')
  const [pluginsMenuOpen, setPluginsMenuOpen] = useState(activeNavTab === 'plugins')
  const [projectOpen, setProjectOpen] = useState(true)
  const [cwdPickerOpen, setCwdPickerOpen] = useState(false)
  const menuRef = useRef(null)
  const workdirMenuRef = useRef(null)
  const searchInputRef = useRef(null)
  const [renameEditingId, setRenameEditingId] = useState(null)
  const [tagPopoverSession, setTagPopoverSession] = useState(null)
  const [tagPopoverTop, setTagPopoverTop] = useState(120)
  const tagPopoverRef = useRef(null)

  const activeTag = useSidebarStore((s) => s.activeTag)
  const setActiveTag = useSidebarStore((s) => s.setActiveTag)
  const availableTags = useMemo(() => {
    const seen = new Set()
    const out = []
    for (const s of sessions) {
      if (s.tag && !seen.has(s.tag)) {
        seen.add(s.tag)
        out.push(s.tag)
      }
    }
    return out
  }, [sessions])

  // Group sessions by cwd (order from the store: active cwd pinned first), then
  // apply the tag + search filters WITHIN each group. Empty groups drop out.
  const renderedGroups = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const match = (s) => {
      if (activeTag && s.tag !== activeTag) return false
      if (!q) return true
      const sid = (s.sessionId || s.id || '').toLowerCase()
      const name = (s.name || '').toLowerCase()
      return sid.includes(q) || name.includes(q)
    }
    // Within a cwd: most-recent first, then float pinned sessions to the top
    // (two stable passes — mirrors the backend ordering).
    const sortInGroup = (list) => {
      list.sort((a, b) => (b.createdAt || 0) - (a.createdAt || 0))
      list.sort((a, b) => (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1))
    }
    const byCwd = new Map()
    for (const s of sessions) {
      if (s.archived) continue
      if (!match(s)) continue
      const arr = byCwd.get(s.cwd) || []
      arr.push(s)
      byCwd.set(s.cwd, arr)
    }
    const groupMeta = new Map(groups.map((g) => [g.cwd, g]))
    const ordered = []
    for (const g of groups) {
      const list = byCwd.get(g.cwd)
      if (!list || list.length === 0) continue
      sortInGroup(list)
      ordered.push({ cwd: g.cwd, total: g.total, sessions: list, pinned: !!g.pinned, lastActivity: list[0].createdAt || 0 })
      byCwd.delete(g.cwd)
    }
    // Sessions whose cwd isn't in the group list yet (e.g. a freshly created one).
    for (const [cwd, list] of byCwd) {
      sortInGroup(list)
      ordered.push({ cwd, total: list.length, sessions: list, pinned: !!groupMeta.get(cwd)?.pinned, lastActivity: list[0].createdAt || 0 })
    }
    // Group order (stable passes, last wins): activity desc → pinned ahead →
    // active workspace absolute first.
    ordered.sort((a, b) => (b.lastActivity || 0) - (a.lastActivity || 0))
    ordered.sort((a, b) => (a.pinned ? 0 : 1) - (b.pinned ? 0 : 1))
    ordered.sort((a, b) => ((a.cwd === activeCwd) ? 0 : 1) - ((b.cwd === activeCwd) ? 0 : 1))
    return ordered
  }, [sessions, groups, searchQuery, activeTag, activeCwd])

  const filtersActive = !!searchQuery.trim() || !!activeTag
  // Keep the currently selected session reachable when its project tree is
  // collapsed. The row is rendered from the unfiltered store list so folding
  // PROJECT does not make the active conversation disappear.
  const activeSession = useMemo(() => (
    sessions.find((session) => session.id === activeSessionId && !session.archived) || null
  ), [sessions, activeSessionId])
  // The PROJECT section can be collapsed independently of each cwd group. Keep
  // the selected session's parent project row with it so the hierarchy remains
  // legible while the rest of the project list is hidden.
  const activeProject = useMemo(() => {
    if (!activeSession) return null
    const group = groups.find((item) => item.cwd === activeSession.cwd)
    if (group) return group
    return {
      cwd: activeSession.cwd,
      total: sessions.filter((session) => !session.archived && session.cwd === activeSession.cwd).length,
      pinned: false,
    }
  }, [activeSession, groups, sessions])
  const allGroupsExpanded = renderedGroups.length > 0 && renderedGroups.every((g) => (
    !!expandedCwds[g.cwd]
    && (!g.sessions.some((s) => s.origin === 'scheduler') || !!expandedScheduledCwds[g.cwd])
  ))
  // A nav menu being expanded (Data & Usage or Plugins/Customize) drops the PROJECT
  // block to the sidebar bottom and collapses it; collapsing the menu restores it.
  const menuExpanded = dataMenuOpen || pluginsMenuOpen
  const projectAtBottom = menuExpanded
  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

  // Focus the search input as soon as it morphs open.
  useEffect(() => {
    if (searchOpen) searchInputRef.current?.focus()
  }, [searchOpen])

  // Keep the Data & Usage / Plugins submenu open whenever its view is active.
  useEffect(() => {
    if (activeNavTab === 'userdata') {
      setDataMenuOpen(true)
      setPluginsMenuOpen(false)
    }
    if (activeNavTab === 'plugins') {
      setPluginsMenuOpen(true)
      setDataMenuOpen(false)
    }
  }, [activeNavTab])

  // Expanding a nav menu collapses the PROJECT list (it drops to the bottom);
  // collapsing the menu restores PROJECT to its normal place and re-expands it.
  useEffect(() => {
    setProjectOpen(!menuExpanded)
  }, [menuExpanded])

  // Close menu on outside click
  useEffect(() => {
    if (!openMenuId) return
    const handler = (e) => {
      if (menuRef.current && !menuRef.current.contains(e.target)) {
        setOpenMenuId(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [openMenuId])

  // Close workdir menu on outside click
  useEffect(() => {
    if (!openWorkdirMenu) return
    const handler = (e) => {
      if (workdirMenuRef.current && !workdirMenuRef.current.contains(e.target)) {
        setOpenWorkdirMenu(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [openWorkdirMenu])

  // Close tag popover on outside click
  useEffect(() => {
    if (!tagPopoverSession) return
    const handler = (e) => {
      if (tagPopoverRef.current && !tagPopoverRef.current.contains(e.target)) {
        setTagPopoverSession(null)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [tagPopoverSession])

  const handleRenameStart = (session) => setRenameEditingId(session.id)
  const handleRenameCancel = () => setRenameEditingId(null)
  const handleRenameCommit = async (session, nextTitle) => {
    const trimmed = (nextTitle || '').trim()
    setRenameEditingId(null)
    if (!trimmed || trimmed === session.name) return
    try {
      await apiRenameSession(session.sessionId || session.id, trimmed)
      useSidebarStore.setState((s) => ({
        sessions: s.sessions.map((row) =>
          row.id === session.id ? { ...row, name: trimmed, customTitle: trimmed } : row
        ),
      }))
    } catch (err) {
      showConfirmDialog({
        title: t('sidebar.renameFailed'),
        message: String(err?.message || err),
        confirmLabel: t('confirm.ok'),
      })
    }
  }
  const handleTagStart = (session, anchorRect) => {
    if (anchorRect) {
      // Anchor below the trigger row; keep the popup body on-screen.
      setTagPopoverTop(Math.max(60, Math.min(window.innerHeight - 240, anchorRect.bottom + 4)))
    }
    setTagPopoverSession(session)
  }
  const handleTagSaved = (session, nextTag) => {
    useSidebarStore.setState((s) => ({
      sessions: s.sessions.map((row) =>
        row.id === session.id ? { ...row, tag: nextTag } : row
      ),
    }))
    // If the currently-active tag filter matches a tag that no longer exists, reset.
    if (activeTag && nextTag !== activeTag) {
      const stillExists = useSidebarStore.getState().sessions.some((s) => s.tag === activeTag)
      if (!stillExists) setActiveTag(null)
    }
  }

  const effectiveWidth = collapsed ? 48 : width

  // Fresh conversation in a NEW draft runtime — a running session keeps
  // streaming in the background (its dot stays purple).
  const handleNewChat = () => {
    newDraftSession()
  }

  // "New Session" — switch to the chat view and start fresh.
  const handleNewSession = () => {
    resetSplit()
    setActiveNavTab('priva')
    handleNewChat()
  }

  // Start a fresh chat pre-seeded to a workdir's cwd (group square-pen + PROJECT
  // new-workdir picker). Always returns to the chat view.
  const handleNewChatHere = (cwd) => {
    setOpenWorkdirMenu(null)
    resetSplit()
    setActiveNavTab('priva')
    newDraftSession({ cwd })
  }

  // Open a Data & Usage section in the content area.
  const openDataSection = (id) => {
    setActiveNavTab('userdata')
    setActiveSection(id)
    setDataMenuOpen(true)
    setPluginsMenuOpen(false)
  }

  // Open a Plugins/Customize section in the content area.
  const openPluginSection = (id) => {
    setActiveNavTab('plugins')
    setActivePluginSection(id)
    setPluginsMenuOpen(true)
    setDataMenuOpen(false)
  }

  const togglePluginsMenu = () => {
    const nextOpen = !pluginsMenuOpen
    setPluginsMenuOpen(nextOpen)
    if (nextOpen) setDataMenuOpen(false)
  }

  const toggleDataMenu = () => {
    const nextOpen = !dataMenuOpen
    setDataMenuOpen(nextOpen)
    if (nextOpen) setPluginsMenuOpen(false)
  }

  const handlePinSession = (session) => togglePinSession(session.id)
  const handleArchiveSession = (session) => archiveSessionLocal(session.id)

  const handlePinWorkdir = (cwd) => {
    setOpenWorkdirMenu(null)
    togglePinWorkdir(cwd)
  }

  const handleArchiveWorkdir = (cwd, count) => {
    setOpenWorkdirMenu(null)
    showConfirmDialog({
      title: t('sidebar.archiveWorkdir'),
      message: t('sidebar.archiveWorkdirConfirm', { count }),
      confirmLabel: t('sidebar.archive'),
      onConfirm: () => archiveWorkdirLocal(cwd),
    })
  }

  const handleSelectSession = async (session) => {
    // Selecting a session always shows the chat view.
    setActiveNavTab('priva')
    if (splitPanes.length > 0) {
      const activePane = useSplitStore.getState().getActivePane()
      openSessionInActivePane(session.sessionId || session.id)
      if (!activePane?.local) {
        setActiveSessionId(session.id)
        return
      }
    }
    // Swap to the session's runtime (live streams keep running in the
    // background); cold sessions hydrate before the swap — see openSession.
    await openSession(session)
  }

  const handleDeleteSession = (e, session) => {
    e.stopPropagation()
    showConfirmDialog({
      title: t('sidebar.deleteTitle'),
      message: t('sidebar.deleteMessage', { name: session.name }),
      confirmLabel: t('sidebar.deleteConfirm'),
      danger: true,
      onConfirm: async () => {
        const sid = session.sessionId || session.id
        try {
          await apiDeleteSession(sid)
        } catch (err) {
          console.error('Failed to delete session:', err)
        }
        safeStorage.removeItem(`priva-rewind:${sid}`)
        useSidebarStore.setState((s) => ({
          sessions: s.sessions.filter((row) => row.id !== session.id),
          groups: s.groups.map((g) => (g.cwd === session.cwd ? { ...g, total: Math.max(0, g.total - 1) } : g)),
        }))
        // Deleting a running session aborts its stream; deleting the active
        // one swaps to a fresh draft before its runtime is dropped. Resolve
        // rotated ids so a resumed session's live runtime is the one removed.
        const canonical = resolveKey(sid)
        stopSessionStream(canonical, { broadcast: true })
        if (getActiveKey() === canonical || activeSessionId === session.id) {
          newDraftSession()
        }
        removeRuntime(canonical)
        useSessionStatusStore.getState().clear(canonical)
        useSessionStatusStore.getState().clear(sid)
      },
    })
  }

  const renderSessionItem = (session, indent) => (
    <SessionItem
      key={session.id}
      session={session}
      isActive={session.id === activeSessionId}
      openMenuId={openMenuId}
      menuRef={menuRef}
      onSelect={handleSelectSession}
      onMenuToggle={setOpenMenuId}
      onDelete={handleDeleteSession}
      onRenameStart={handleRenameStart}
      onTagStart={handleTagStart}
      onPinToggle={handlePinSession}
      onArchive={handleArchiveSession}
      renameEditingId={renameEditingId}
      onRenameCommit={handleRenameCommit}
      onRenameCancel={handleRenameCancel}
      onDragStartSession={beginSessionDrag}
      onDragEndSession={endSessionDrag}
      t={t}
      indent={indent}
    />
  )

  // Small icon-button style shared by the brand collapse + bottom controls.
  const iconBtn = {
    width: 28,
    height: 28,
    background: 'transparent',
    border: 'none',
    cursor: 'pointer',
    color: 'var(--text-dim)',
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    borderRadius: 8,
    flexShrink: 0,
    transition: 'color 150ms ease, background 150ms ease',
  }
  const iconBtnIn = (e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }
  const iconBtnOut = (e) => { e.currentTarget.style.color = 'var(--text-dim)'; e.currentTarget.style.background = 'transparent' }

  return (
    <aside
      className="sidebar-menu fixed flex flex-col overflow-hidden"
      style={{
        width: effectiveWidth,
        top: 'var(--navbar-height)',
        left: 0,
        bottom: 0,
        background: 'var(--bg-surface)',
        borderRight: '1px solid var(--border)',
        transition: 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      {/* Brand + collapse toggle */}
      <div
        className="flex items-center flex-shrink-0"
        style={{
          height: 48,
          // Match the primary menu's 53px text baseline (16px outer gutter
          // + 10px row inset + 19px icon + 8px icon gap).
          padding: collapsed ? 0 : '0 8px 0 24px',
          justifyContent: collapsed ? 'center' : 'space-between',
          borderBottom: '1px solid var(--border-subtle)',
        }}
      >
        {collapsed ? (
          <button style={iconBtn} onClick={toggleCollapsed} title={t('sidebar.expand')} onMouseEnter={iconBtnIn} onMouseLeave={iconBtnOut}>
            <Bot size={21} strokeWidth={1.5} style={{ color: 'var(--sidebar-icon-color)' }} />
          </button>
        ) : (
          <>
            <div className="flex items-center gap-2 min-w-0">
              <Bot size={21} strokeWidth={1.5} style={{ color: 'var(--sidebar-icon-color)', flexShrink: 0 }} />
              <span className="font-bold truncate" style={{ color: 'var(--text-primary)', fontSize: 17, letterSpacing: '-0.01em', minWidth: 0 }}>
                {t('brand.title')}
              </span>
            </div>
            <button style={iconBtn} onClick={toggleCollapsed} title={t('sidebar.collapse')} onMouseEnter={iconBtnIn} onMouseLeave={iconBtnOut}>
              <PanelLeftClose size={17} strokeWidth={1.5} />
            </button>
          </>
        )}
      </div>

      {collapsed ? (
        /* Collapsed icon rail */
        <div className="flex flex-col items-center flex-1 overflow-hidden" style={{ padding: '8px 0', gap: 2 }}>
          <NavItem collapsed icon={Plus} label={t('sidebar.newSession')} onClick={handleNewSession} />
          <NavItem collapsed icon={CalendarClock} label={t('sidebar.scheduler')} active={activeNavTab === 'scheduler'} onClick={() => setActiveNavTab('scheduler')} />
          <NavItem collapsed icon={PackageSearch} label={t('sidebar.plugins')} title={t('sidebar.plugins')} active={activeNavTab === 'plugins'} onClick={() => openPluginSection(activePluginSection || 'skills')} />
          <NavItem collapsed icon={ChartColumnBig} label={t('sidebar.dataUsage')} active={activeNavTab === 'userdata'} onClick={() => openDataSection(activeSection || 'usage')} />
          <div style={{ height: 1, width: 24, background: 'var(--border-subtle)', margin: '4px 0' }} />
          <NavItem collapsed icon={Search} label={t('sidebar.search')} onClick={() => { setCollapsed(false); setSearchOpen(true) }} />
          <NavItem collapsed icon={FolderGit2} label={t('sidebar.project')} onClick={() => setCollapsed(false)} />
          <div className="flex-1" />
          <div className="relative flex flex-col items-center gap-1">
            <SettingsPopover />
            <NavItem collapsed icon={Settings} label={t('sidebar.settings')} onClick={toggleSettingsPopover} />
          </div>
        </div>
      ) : (
        <>
          {/* Primary navigation — full-width rows aligned to the shared sidebar gutter. */}
          <div style={{ padding: '6px 16px 4px', flexShrink: 0, display: 'flex', flexDirection: 'column', gap: 4 }}>
            <NavItem scale="lg" icon={Plus} label={t('sidebar.newSession')} onClick={handleNewSession} />
            <NavItem scale="lg" icon={CalendarClock} label={t('sidebar.scheduler')} active={activeNavTab === 'scheduler'} onClick={() => setActiveNavTab('scheduler')} />
            {/* Keep each trigger and its animated submenu inside one stable
                flex item. Otherwise removing the zero-height collapse shell
                also removes one parent gap on the terminal frame. */}
            <div className="min-w-0">
              <NavItem
                scale="lg"
                icon={PackageSearch}
                label={t('sidebar.plugins')}
                active={activeNavTab === 'plugins'}
                expandable
                expanded={pluginsMenuOpen}
                onClick={togglePluginsMenu}
              />
              <AnimatedCollapse open={pluginsMenuOpen}>
                <div style={{ paddingTop: 4 }}>
                  {PLUGINS_SECTIONS.map((sec) => (
                    <NavItem
                      scale="md"
                      key={sec.id}
                      icon={sec.icon}
                      label={t(sec.labelKey)}
                      indent={16}
                      active={activeNavTab === 'plugins' && activePluginSection === sec.id}
                      onClick={() => openPluginSection(sec.id)}
                    />
                  ))}
                </div>
              </AnimatedCollapse>
            </div>
            <div className="min-w-0">
              <NavItem
                scale="lg"
                icon={ChartColumnBig}
                label={t('sidebar.dataUsage')}
                active={activeNavTab === 'userdata'}
                expandable
                expanded={dataMenuOpen}
                onClick={toggleDataMenu}
              />
              <AnimatedCollapse open={dataMenuOpen}>
                <div style={{ paddingTop: 4 }}>
                  {DATA_SECTIONS.map((sec) => (
                    <NavItem
                      scale="md"
                      key={sec.id}
                      icon={sec.icon}
                      label={t(sec.labelKey)}
                      indent={16}
                      active={activeNavTab === 'userdata' && activeSection === sec.id}
                      onClick={() => openDataSection(sec.id)}
                    />
                  ))}
                </div>
              </AnimatedCollapse>
            </div>
          </div>

          {/* When a nav menu is expanded, push Search + PROJECT to the sidebar bottom */}
          {projectAtBottom && <div style={{ flex: '1 1 0', minHeight: 0 }} />}

          {/* Divider — separates the nav menu from Search + PROJECT */}
          <div style={{ height: 1, background: 'var(--border-subtle)', margin: '4px 12px', flexShrink: 0 }} />

          {/* Search — the nav row and the input box cross-animate (height) for a smooth morph.
              Both stay mounted so AnimatedCollapse can run enter/exit transitions. */}
          <div style={{ flexShrink: 0, padding: '0 16px' }}>
            <AnimatedCollapse open={!searchOpen}>
              <NavItem icon={Search} label={t('sidebar.search')} onClick={() => setSearchOpen(true)} />
            </AnimatedCollapse>
            <AnimatedCollapse open={searchOpen}>
              <div
                className="flex items-center gap-2"
                style={{
                  background: 'var(--bg-elevated)',
                  border: '1px solid var(--border)',
                  borderRadius: 8,
                  padding: '4px 8px',
                  minWidth: 0,
                  width: '100%',
                  height: 32,
                  margin: 0,
                }}
              >
                <Search size={13} strokeWidth={1.5} style={{ color: 'var(--sidebar-icon-color)', flexShrink: 0 }} />
                <input
                  ref={searchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onBlur={() => { if (!searchQuery.trim()) setSearchOpen(false) }}
                  placeholder={t('sidebar.searchPlaceholder')}
                  style={{
                    flex: 1,
                    background: 'transparent',
                    border: 'none',
                    outline: 'none',
                    color: 'var(--text-primary)',
                    fontSize: 13,
                    fontFamily: 'var(--font-ui)',
                    minWidth: 0,
                  }}
                />
                {searchQuery && (
                  <button
                    style={{
                      background: 'none',
                      border: 'none',
                      cursor: 'pointer',
                      color: 'var(--text-dim)',
                      padding: 0,
                      display: 'flex',
                      transition: 'color 150ms ease',
                    }}
                    onClick={() => { setSearchQuery(''); searchInputRef.current?.focus() }}
                    onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                  >
                    <X size={13} strokeWidth={1.5} />
                  </button>
                )}
              </div>
            </AnimatedCollapse>
          </div>

          {/* PROJECT header — collapse-all + refresh + expand/collapse-all + new-workdir */}
          <div style={{ flexShrink: 0 }}>
          <PanelHeader
            label={t('sidebar.project')}
            labelClassName="sidebar-menu-project-label"
            open={projectOpen}
            title={projectOpen ? t('sidebar.collapse') : t('sidebar.expand')}
            onClick={() => setProjectOpen((v) => !v)}
            actions={[
              {
                key: 'refresh',
                icon: RefreshCw,
                title: t('sidebar.refresh'),
                onClick: fetchSessions,
                spinning: sessionsLoading,
                disabled: sessionsLoading,
              },
              {
                key: 'toggleAll',
                icon: allGroupsExpanded ? Minimize2 : Maximize2,
                title: allGroupsExpanded ? t('sidebar.collapseAll') : t('sidebar.expandAll'),
                onClick: () => setAllGroupsExpanded(!allGroupsExpanded),
              },
              {
                key: 'newWorkdir',
                icon: FolderOpenDot,
                title: t('sidebar.newWorkdir'),
                onClick: () => setCwdPickerOpen(true),
              },
            ]}
          />
          </div>

          {projectOpen && (
            <>
          {/* Tag filter bar (only when at least one tag exists) — indented to nest under PROJECT */}
          {availableTags.length > 0 && (
            <div
              className="flex flex-wrap gap-1 px-3"
              style={{ borderBottom: '1px solid var(--border-subtle)', flexShrink: 0, paddingLeft: 28, paddingTop: 2, paddingBottom: 4 }}
            >
              <TagFilterChip
                active={activeTag === null}
                label={t('sidebar.all')}
                showIcon={false}
                onClick={() => setActiveTag(null)}
              />
              {availableTags.map((tag) => (
                <TagFilterChip
                  key={tag}
                  active={activeTag === tag}
                  label={tag}
                  onClick={() => setActiveTag(tag)}
                />
              ))}
            </div>
          )}

          {/* Tag popover host — fixed-position, anchored to the trigger row */}
          {tagPopoverSession && (
            <div
              className="fixed"
              ref={tagPopoverRef}
              style={{ top: tagPopoverTop, left: 12, zIndex: 80 }}
            >
              <TagPopover
                session={tagPopoverSession}
                recentTags={availableTags}
                onClose={() => setTagPopoverSession(null)}
                onSaved={handleTagSaved}
              />
            </div>
          )}

          {/* Session List — grouped by cwd (accordion). Fills remaining height in chat
              mode; sits compact at the bottom (scrolls) when a nav menu is expanded. */}
          <div
            className="overflow-y-auto py-1"
            ref={listRef}
            style={{ flex: projectAtBottom ? '0 1 auto' : '1 1 auto', minHeight: 0 }}
          >
            <div style={{ position: 'relative', minHeight: '100%' }}>
              {sessions.length === 0 && !sessionsLoading && (
                <div className="px-3 py-4" style={{ color: 'var(--text-dim)', fontSize: 14 }}>
                  {t('sidebar.noSessions')}
                </div>
              )}

              {filtersActive && sessions.length > 0 && renderedGroups.length === 0 && (
                <div className="px-3 py-4" style={{ color: 'var(--text-dim)', fontSize: 14 }}>
                  {t('sidebar.noResults')}
                </div>
              )}

              {sessions.length === 0 && sessionsLoading && (
                <div className="px-3 py-2">
                  {[1, 2, 3].map((i) => (
                    <div key={i} className="flex items-center gap-2 px-0 py-2">
                      <div className="skeleton" style={{ width: 14, height: 14, flexShrink: 0 }} />
                      <div className="skeleton" style={{ height: 14, flex: 1 }} />
                    </div>
                  ))}
                </div>
              )}

              {renderedGroups.map((group) => {
                const isActiveGroup = group.cwd === activeCwd
                // Toggle state fully controls expansion (active cwd defaults open
                // via the store). While filtering, force every matching group open.
                const isExpanded = filtersActive || !!expandedCwds[group.cwd]
                const loadedCount = group.sessions.length
                const showMore = !filtersActive && loadedCount < group.total
                const regularSessions = group.sessions.filter((session) => session.origin !== 'scheduler')
                const scheduledSessions = group.sessions.filter((session) => session.origin === 'scheduler')
                const activeSessionInCollapsedGroup = !isExpanded
                  ? group.sessions.find((session) => session.id === activeSessionId)
                  : null
                // Search/tag filtering must reveal matching scheduled sessions;
                // otherwise this nested group intentionally starts collapsed.
                const scheduledExpanded = filtersActive || !!expandedScheduledCwds[group.cwd]
                return (
                  <div key={group.cwd} style={{ marginBottom: 2 }}>
                    {/* Group header — toggle + (hover) workdir menu & new-chat */}
                    <div
                      className="project-group-row flex items-center gap-2 py-1 min-w-0 group"
                      style={{
                        borderLeft: 'none',
                        marginLeft: 16,
                        marginRight: 16,
                        paddingLeft: 10,
                        paddingRight: 8,
                        color: isActiveGroup ? 'var(--text-secondary)' : 'var(--text-dim)',
                      }}
                      title={group.cwd}
                    >
                      <button
                        type="button"
                        onClick={() => toggleGroup(group.cwd)}
                        className="flex items-center gap-2 flex-1 min-w-0"
                        style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'inherit', minWidth: 0, transition: 'color 150ms ease' }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)' }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = 'inherit' }}
                      >
                        <ProjectGroupIcon expanded={isExpanded} />
                        <span className="flex-1 truncate" style={{ fontSize: 15, minWidth: 0, textAlign: 'left' }}>
                          {shortCwd(group.cwd)}
                        </span>
                      </button>
                      {group.pinned && (
                      <Pin size={11} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--sidebar-icon-color)' }} />
                      )}
                      <span style={{ fontSize: 12, fontWeight: 600, flexShrink: 0 }}>{group.total}</span>
                      {/* Workdir menu (sliders) */}
                      <div className="relative" ref={openWorkdirMenu === group.cwd ? workdirMenuRef : undefined} style={{ flexShrink: 0 }}>
                        <button
                          type="button"
                          className="group-hover-visible"
                          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2, display: 'flex', alignItems: 'center', opacity: openWorkdirMenu === group.cwd ? 1 : 0, transition: 'color 150ms ease, opacity 150ms ease' }}
                          title={t('sidebar.workdirOptions')}
                          onClick={(e) => { e.stopPropagation(); setOpenWorkdirMenu(openWorkdirMenu === group.cwd ? null : group.cwd) }}
                          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                        >
                          <SlidersVertical size={13} strokeWidth={1.5} />
                        </button>
                        {openWorkdirMenu === group.cwd && (
                          <div className="absolute" style={{ top: '100%', right: 0, marginTop: 4, background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, zIndex: 50, minWidth: 150, overflow: 'hidden' }}>
                            <button
                              className="flex items-center gap-2 px-3 w-full"
                              style={{ ...WD_MENU_ITEM_STYLE, color: 'var(--text-primary)' }}
                              onClick={(e) => { e.stopPropagation(); handlePinWorkdir(group.cwd) }}
                              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                            >
                              <Pin size={13} strokeWidth={1.5} />
                              {group.pinned ? t('sidebar.unpinWorkdir') : t('sidebar.pinWorkdir')}
                            </button>
                            <button
                              className="flex items-center gap-2 px-3 w-full"
                              style={{ ...WD_MENU_ITEM_STYLE, color: 'var(--text-primary)' }}
                              onClick={(e) => { e.stopPropagation(); handleArchiveWorkdir(group.cwd, group.total) }}
                              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                              onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                            >
                              <Archive size={13} strokeWidth={1.5} />
                              {t('sidebar.archiveWorkdir')}
                            </button>
                          </div>
                        )}
                      </div>
                      {/* New chat in this workdir (square-pen) */}
                      <button
                        type="button"
                        className="group-hover-visible"
                        style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2, display: 'flex', alignItems: 'center', flexShrink: 0, opacity: 0, transition: 'color 150ms ease, opacity 150ms ease' }}
                        title={t('sidebar.newChatHere')}
                        onClick={(e) => { e.stopPropagation(); handleNewChatHere(group.cwd) }}
                        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                      >
                        <SquarePen size={13} strokeWidth={1.5} />
                      </button>
                    </div>

                    {/* A collapsed project keeps its selected session in view. */}
                    {activeSessionInCollapsedGroup && renderSessionItem(
                      activeSessionInCollapsedGroup,
                      activeSessionInCollapsedGroup.origin === 'scheduler' ? SCHEDULED_SESSION_INDENT : PROJECT_SESSION_INDENT
                    )}

                    {/* Group sessions */}
                    {isExpanded && (
                      <>
                        {/* Regular sessions remain directly visible — only scheduler
                            sessions gain their own collapsible subgroup. */}
                        {regularSessions.map((session) => renderSessionItem(session, PROJECT_SESSION_INDENT))}
                        {scheduledSessions.length > 0 && (
                          <div style={{ marginTop: regularSessions.length > 0 ? 2 : 0 }}>
                            <button
                              type="button"
                              className="flex items-center gap-1 w-full py-1 min-w-0"
                              style={{
                                background: 'transparent',
                                border: 'none',
                                color: 'var(--text-dim)',
                                cursor: 'pointer',
                                paddingLeft: 28,
                                paddingRight: 12,
                                transition: 'color 150ms ease',
                              }}
                              onClick={() => toggleScheduledGroup(group.cwd)}
                              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                            >
                              {scheduledExpanded
                                ? <ChevronDown size={13} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                                : <ChevronRight size={13} strokeWidth={1.5} style={{ flexShrink: 0 }} />}
                              <CalendarClock size={13} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                              <span
                                className="flex-1 truncate uppercase"
                                style={{ fontSize: 12, fontWeight: 600, letterSpacing: '0.06em', minWidth: 0, textAlign: 'left' }}
                              >
                                {t('sidebar.scheduledSessions', { defaultValue: 'Scheduled sessions' })}
                              </span>
                            </button>
                            {scheduledExpanded && scheduledSessions.map((session) => renderSessionItem(session, SCHEDULED_SESSION_INDENT))}
                          </div>
                        )}
                        {showMore && (
                          <button
                            type="button"
                            className="flex items-center gap-1 w-full py-1.5"
                            style={{
                              background: 'transparent',
                              border: 'none',
                              color: 'var(--text-dim)',
                              cursor: groupLoadingCwd === group.cwd ? 'default' : 'pointer',
                              fontSize: 13,
                              // 36px + chevron (13px) + gap (4px) = 53px,
                              // matching the regular session title column.
                              paddingLeft: 36,
                              opacity: groupLoadingCwd === group.cwd ? 0.45 : 0.65,
                              transition: 'color 150ms ease, opacity 150ms ease',
                            }}
                            onClick={() => fetchMoreInGroup(group.cwd)}
                            disabled={groupLoadingCwd === group.cwd}
                            onMouseEnter={(e) => {
                              if (groupLoadingCwd !== group.cwd) {
                                e.currentTarget.style.color = 'var(--text-secondary)'
                                e.currentTarget.style.opacity = '0.85'
                              }
                            }}
                            onMouseLeave={(e) => {
                              e.currentTarget.style.color = 'var(--text-dim)'
                              e.currentTarget.style.opacity = groupLoadingCwd === group.cwd ? '0.45' : '0.65'
                            }}
                          >
                            {groupLoadingCwd === group.cwd ? (
                              t('sidebar.loading')
                            ) : (
                              <>
                                <ChevronDown size={13} strokeWidth={1.5} />
                                <span className="truncate" style={{ minWidth: 0 }}>
                                  {t('sidebar.moreInDir')}
                                </span>
                              </>
                            )}
                          </button>
                        )}
                      </>
                    )}
                  </div>
                )
              })}
            </div>
          </div>
            </>
          )}

          {/* Keep the selected conversation visible while the whole PROJECT
              section is collapsed (including when a sidebar menu pushes it
              down). */}
          {!projectOpen && activeSession && activeProject && (
            <div className="py-1" style={{ flexShrink: 0 }}>
              <div
                className="project-group-row flex items-center gap-2 py-1 min-w-0"
                style={{
                  marginLeft: 16,
                  marginRight: 16,
                  paddingLeft: 10,
                  paddingRight: 8,
                  color: 'var(--text-secondary)',
                }}
                title={activeProject.cwd}
              >
                <button
                  type="button"
                  className="flex items-center gap-2 flex-1 min-w-0"
                  title={t('sidebar.expand')}
                  onClick={() => setProjectOpen(true)}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'inherit',
                    minWidth: 0,
                    transition: 'color 150ms ease',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'inherit' }}
                >
                  <ProjectGroupIcon expanded={false} />
                  <span className="flex-1 truncate" style={{ fontSize: 15, minWidth: 0, textAlign: 'left' }}>
                    {shortCwd(activeProject.cwd)}
                  </span>
                </button>
                {activeProject.pinned && (
                  <Pin size={11} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--sidebar-icon-color)' }} />
                )}
                <span style={{ fontSize: 12, fontWeight: 600, flexShrink: 0 }}>{activeProject.total}</span>
              </div>
              {renderSessionItem(
                activeSession,
                activeSession.origin === 'scheduler' ? SCHEDULED_SESSION_INDENT : PROJECT_SESSION_INDENT
              )}
            </div>
          )}

          {/* Chat mode with PROJECT manually collapsed: filler keeps the footer pinned to the bottom */}
          {!projectAtBottom && !projectOpen && <div style={{ flex: '1 1 0', minHeight: 0 }} />}
        </>
      )}

      {/* Bottom: Settings + user + logout (expanded only) — always pinned to the bottom */}
      {!collapsed && (
        <div
          className="p-2 flex items-center"
          style={{ borderTop: '1px solid var(--border-subtle)', justifyContent: 'space-between', gap: 8 }}
        >
          <div className="relative flex-shrink-0">
            <SettingsPopover />
            <button
              style={iconBtn}
              onClick={toggleSettingsPopover}
              title={t('sidebar.settings')}
              onMouseEnter={iconBtnIn}
              onMouseLeave={iconBtnOut}
            >
              <Settings size={17} strokeWidth={1.5} />
            </button>
          </div>
          <div className="flex items-center gap-2 min-w-0">
            {authUser && (
              <span className="truncate" style={{ color: 'var(--text-secondary)', fontSize: 13, minWidth: 0 }}>
                {authUser.username}
              </span>
            )}
            <button
              style={iconBtn}
              onClick={logout}
              title={t('sidebar.signOut')}
              onMouseEnter={iconBtnIn}
              onMouseLeave={iconBtnOut}
            >
              <LogOut size={15} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      )}

      {!collapsed && <SidebarResizer />}

      {/* DirectoryPicker for "open session in new workdir" — portaled to body so its
          fixed full-screen overlay escapes the sidebar's (position:fixed) stacking context. */}
      {createPortal(
        <DirectoryPicker
          open={cwdPickerOpen}
          multiple={false}
          allowCreate
          title={t('picker.cwdTitle')}
          initialPath={activeCwd || '/'}
          onConfirm={(path) => { setCwdPickerOpen(false); handleNewChatHere(path) }}
          onCancel={() => setCwdPickerOpen(false)}
        />,
        document.body,
      )}

      {/* Group hover CSS for delete button + spinner keyframe */}
      <style>{`
        .group:hover .group-hover-visible { opacity: 1 !important; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </aside>
  )
}
