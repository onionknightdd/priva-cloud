import { useEffect, useRef, useState, useMemo } from 'react'
import { PlusCircle, MessageSquare, PanelLeftClose, PanelLeft, Trash2, ChevronDown, ChevronRight, FolderBookmark, MoreHorizontal, RefreshCw, Settings, Search, X, Pencil, Flag, GitBranch, Pin, Archive, SlidersVertical, SquarePen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useSidebarStore from '../../stores/sidebarStore'
import useChatStore from '../../stores/chatStore'
import useTaskStore from '../../stores/taskStore'
import useUiStore from '@shared/stores/uiStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useToastStore from '@shared/stores/toastStore'
import {
  fetchSessionMessages,
  deleteSession as apiDeleteSession,
  renameSession as apiRenameSession,
  tagSession as apiTagSession,
} from '../../api/sessions'
import { UnauthorizedError } from '@shared/api/client'
import { hasCanvasInspectorItems, transformSessionMessages } from '../../utils/sessionTransform'
import { stopActiveStream } from '../../hooks/useSSE'
import SidebarResizer from './SidebarResizer'
import SettingsPopover from '../settings/SettingsPopover'
import CopyButton from '@shared/components/shared/CopyButton'
import TagFilterChip from '../shared/TagFilterChip'
import safeStorage from '@shared/utils/safeStorage'

// Compact cwd label for a group header: the last path segment (full path in title).
function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

// Dropdown item style shared by the workdir (sliders) menu.
const WD_MENU_ITEM_STYLE = {
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  fontSize: 12,
  paddingTop: 6,
  paddingBottom: 6,
  transition: 'background 150ms ease',
}

function SessionItem({
  session, isActive, openMenuId, menuRef, onSelect, onMenuToggle,
  onDelete, onRenameStart, onTagStart, onPinToggle, onArchive, renameEditingId,
  onRenameCommit, onRenameCancel, t, indent = 0,
}) {
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
    fontSize: 12,
    paddingTop: 6,
    paddingBottom: 6,
    transition: 'background 150ms ease',
  }

  return (
    <div
      className="flex flex-col gap-1 px-3 group"
      style={{
        position: 'relative',
        paddingTop: 4,
        paddingBottom: 4,
        // Shift the box right so it starts at the active indicator bar (its left
        // edge) and runs to the sidebar's right edge; icon stays put (margin + pad).
        marginLeft: (indent || 12) - 8,
        paddingLeft: 8,
        background: isActive ? 'var(--bg-elevated)' : 'transparent',
        color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
        cursor: editing ? 'default' : 'pointer',
        fontSize: 13,
        transition: 'background 150ms ease',
      }}
      onClick={() => { if (!editing) onSelect(session) }}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      {isActive && (
        <span
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: 2,
            background: 'var(--blue)',
            borderRadius: 1,
          }}
        />
      )}
      <div className="flex items-center gap-2 min-w-0">
        <MessageSquare size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
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
              fontSize: 13,
              padding: '2px 4px',
            }}
          />
        ) : (
          <span className="flex-1 truncate" style={{ minWidth: 0, fontSize: 12, lineHeight: 1.2 }}>{session.name}</span>
        )}
        {session.forkCount > 0 && !editing && (
          <span
            className="inline-flex items-center gap-1"
            style={{
              color: 'var(--cyan)',
              fontSize: 11,
              fontWeight: 600,
              flexShrink: 0,
            }}
            title={`${session.forkCount} fork${session.forkCount === 1 ? '' : 's'}`}
          >
            <GitBranch size={11} strokeWidth={1.5} />
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
            <Pin size={11} strokeWidth={1.5} />
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
              <MoreHorizontal size={11} strokeWidth={1.5} />
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
                  <Pin size={12} strokeWidth={1.5} />
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
                  <Pencil size={12} strokeWidth={1.5} />
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
                  <Flag size={12} strokeWidth={1.5} />
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
                  <Archive size={12} strokeWidth={1.5} />
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
                  <Trash2 size={12} strokeWidth={1.5} />
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
              borderLeft: '2px solid var(--orange)',
              borderRadius: 2,
              color: 'var(--text-dim)',
              fontSize: 10,
              letterSpacing: '0.06em',
              fontWeight: 600,
              padding: '1px 6px',
              maxWidth: '100%',
            }}
            title={session.tag}
          >
            <Flag size={10} strokeWidth={1.5} style={{ color: 'var(--orange)', flexShrink: 0 }} />
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
          fontSize: 12,
          outline: 'none',
          marginBottom: 6,
        }}
      />
      {recentTags.length > 0 && (
        <div className="flex flex-wrap gap-1" style={{ marginBottom: 6 }}>
          {recentTags.slice(0, 6).map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setValue(t)}
              style={{
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderRadius: 2,
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: 11,
                padding: '1px 6px',
              }}
            >
              {t}
            </button>
          ))}
        </div>
      )}
      {error && (
        <div style={{ color: 'var(--red)', fontSize: 11, marginBottom: 6 }}>{error}</div>
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
            fontSize: 11,
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
            fontSize: 11,
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
  const toggleGroup = useSidebarStore((s) => s.toggleGroup)
  const fetchMoreInGroup = useSidebarStore((s) => s.fetchMoreInGroup)
  const groupLoadingCwd = useSidebarStore((s) => s.groupLoadingCwd)
  const activeSessionId = useSidebarStore((s) => s.activeSessionId)
  const setActiveSessionId = useSidebarStore((s) => s.setActiveSessionId)
  const toggleCollapsed = useSidebarStore((s) => s.toggleCollapsed)
  const fetchSessions = useSidebarStore((s) => s.fetchSessions)
  const sessionsLoading = useSidebarStore((s) => s.sessionsLoading)
  const togglePinSession = useSidebarStore((s) => s.togglePinSession)
  const archiveSessionLocal = useSidebarStore((s) => s.archiveSessionLocal)
  const togglePinWorkdir = useSidebarStore((s) => s.togglePinWorkdir)
  const archiveWorkdirLocal = useSidebarStore((s) => s.archiveWorkdirLocal)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const setCwdDraft = useChatStore((s) => s.setCwdDraft)
  const loadSession = useChatStore((s) => s.loadSession)
  const currentSessionId = useChatStore((s) => s.sessionId)
  const clearTasks = useTaskStore((s) => s.clearTasks)
  const clearFileOps = useFileOpsStore((s) => s.clearFileOps)
  const clearFileBrowser = useFileBrowserStore((s) => s.clear)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const toggleSettingsPopover = useUiStore((s) => s.toggleSettingsPopover)
  const clearPlanContent = useUiStore((s) => s.clearPlanContent)
  const hideCanvas = useUiStore((s) => s.hideCanvas)
  const listRef = useRef(null)
  const [openMenuId, setOpenMenuId] = useState(null)
  const [openWorkdirMenu, setOpenWorkdirMenu] = useState(null) // cwd whose workdir menu is open
  const [searchQuery, setSearchQuery] = useState('')
  const menuRef = useRef(null)
  const workdirMenuRef = useRef(null)
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

  useEffect(() => {
    fetchSessions()
  }, [fetchSessions])

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

  const handleNewChat = () => {
    stopActiveStream()
    clearMessages()
    clearTasks()
    clearFileOps()
    clearFileBrowser()
    clearPlanContent()
    hideCanvas()
  }

  // Start a fresh chat pre-seeded to a workdir's cwd (the square-pen control).
  const handleNewChatHere = (cwd) => {
    setOpenWorkdirMenu(null)
    handleNewChat()
    setCwdDraft(cwd)
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
    // Kill any in-flight stream first so its late events can't bleed into
    // the session we're about to load.
    stopActiveStream()
    setActiveSessionId(session.id)
    clearTasks()
    useFileOpsStore.getState().clearFileOps()
    useFileBrowserStore.getState().clear()
    clearPlanContent()
    try {
      const data = await fetchSessionMessages(session.sessionId || session.id)
      const { messages, fileOps, fileBrowserTabs, tasks, subagentContent } = transformSessionMessages(data.messages || [])
      loadSession(session.sessionId || session.id, messages, null, subagentContent, data.add_dirs || [])

      // Populate file ops store
      const fileOpsStore = useFileOpsStore.getState()
      for (const op of fileOps) {
        fileOpsStore.addFileOp(op)
      }
      useFileBrowserStore.getState().setTabs(fileBrowserTabs)

      // Populate task store
      const taskStore = useTaskStore.getState()
      for (const task of tasks) {
        taskStore.addTask(task)
      }

      // Show Canvas only when this session has artifacts that a Canvas tab
      // can actually render. Plain Skill/Bash sessions should not inherit
      // the previous session's visible Canvas state.
      const hasInspectorItems = hasCanvasInspectorItems(messages)
      const canvasTab = fileBrowserTabs.length > 0
        ? 'file-browser'
        : fileOps.length > 0
          ? 'changes'
          : hasInspectorItems
            ? 'tasks'
            : null
      if (canvasTab) {
        const ui = useUiStore.getState()
        ui.showCanvas()
        ui.setActiveCanvasTab(canvasTab)
      } else {
        useUiStore.getState().hideCanvas()
      }
    } catch (err) {
      if (err instanceof UnauthorizedError) return
      console.error('Failed to load session messages:', err)
      // Keep the previous view instead of loading an empty session — an empty
      // chat looks like data loss. Offer a retry via toast.
      useToastStore.getState().pushToast({
        level: 'error',
        title: t('sidebar.loadFailedTitle'),
        body: String(err?.message || err),
        action: {
          label: t('sidebar.loadFailedRetry'),
          onClick: () => handleSelectSession(session),
        },
      })
    }
  }

  const handleDeleteSession = (e, session) => {
    e.stopPropagation()
    showConfirmDialog({
      title: t('sidebar.deleteTitle'),
      message: t('sidebar.deleteMessage', { name: session.name }),
      confirmLabel: t('sidebar.deleteConfirm'),
      danger: true,
      onConfirm: async () => {
        try {
          await apiDeleteSession(session.sessionId || session.id)
        } catch (err) {
          console.error('Failed to delete session:', err)
        }
        safeStorage.removeItem(`priva-rewind:${session.sessionId || session.id}`)
        useSidebarStore.setState((s) => ({
          sessions: s.sessions.filter((row) => row.id !== session.id),
          groups: s.groups.map((g) => (g.cwd === session.cwd ? { ...g, total: Math.max(0, g.total - 1) } : g)),
        }))
        if (activeSessionId === session.id) {
          clearMessages()
          clearTasks()
          useFileOpsStore.getState().clearFileOps()
          useFileBrowserStore.getState().clear()
        }
      },
    })
  }

  return (
    <aside
      className="fixed flex flex-col overflow-hidden"
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
      {collapsed ? (
        /* Collapsed: icon-only new chat + bottom settings/collapse */
        <div className="flex flex-col items-center p-2 flex-1">
          <button
            style={{
              width: 32,
              height: 32,
              background: 'transparent',
              border: 'none',
              borderRadius: '4px',
              cursor: 'pointer',
              color: 'var(--text-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'color 150ms ease',
            }}
            onClick={handleNewChat}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
            title={t('chat.newChat')}
          >
            <PlusCircle size={16} strokeWidth={1.5} />
          </button>
          <div className="flex-1" />
          {/* Settings icon */}
          <div className="relative flex flex-col items-center gap-1">
            <SettingsPopover />
            <button
              style={{
                width: 32,
                height: 32,
                background: 'transparent',
                border: 'none',
                borderRadius: '4px',
                cursor: 'pointer',
                color: 'var(--text-dim)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'color 150ms ease',
              }}
              onClick={toggleSettingsPopover}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              title={t('sidebar.settings')}
            >
              <Settings size={16} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      ) : (
        <>
          {/* New Chat Button */}
          <div className="px-3 py-3">
            <button
              className="flex items-center gap-2 px-3 py-2 w-full"
              style={{
                background: 'transparent',
                border: '1px solid var(--border)',
                borderRadius: '4px',
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                fontSize: 13,
                transition: 'border-color 150ms ease, color 150ms ease',
              }}
              onClick={handleNewChat}
              onMouseEnter={(e) => {
                e.currentTarget.style.borderColor = 'var(--border-strong)'
                e.currentTarget.style.color = 'var(--text-primary)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.borderColor = 'var(--border)'
                e.currentTarget.style.color = 'var(--text-secondary)'
              }}
            >
              <PlusCircle size={13} strokeWidth={1.5} />
              {t('sidebar.newChat')}
            </button>
          </div>

          {/* Current Session Indicator */}
          {currentSessionId && (
            <>
              <div className="px-3 py-2">
                <div
                  className="uppercase"
                  style={{
                    color: 'var(--text-dim)',
                    fontSize: 11,
                    fontWeight: 600,
                    letterSpacing: '0.06em',
                    marginBottom: 4,
                  }}
                >
                  {t('chat.session')}
                </div>
                <div className="flex items-center gap-2 min-w-0">
                  <span
                    className="flex-1 truncate"
                    style={{
                      color: 'var(--text-secondary)',
                      fontSize: 12,
                      fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                      minWidth: 0,
                    }}
                    title={currentSessionId}
                  >
                    {currentSessionId}
                  </span>
                  <CopyButton content={currentSessionId} inline />
                </div>
              </div>
              <div style={{ height: 1, background: 'var(--border-subtle)', margin: '0 12px' }} />
            </>
          )}

          {/* Search + Refresh */}
          <div className="flex items-center gap-2 px-3 py-1">
            <div
              className="flex items-center gap-2 flex-1"
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                padding: '4px 8px',
                minWidth: 0,
              }}
            >
              <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
              <input
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('sidebar.searchPlaceholder')}
                style={{
                  flex: 1,
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: 'var(--text-primary)',
                  fontSize: 12,
                  fontFamily: "'Noto Sans', sans-serif",
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
                  onClick={() => setSearchQuery('')}
                  onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                >
                  <X size={12} strokeWidth={1.5} />
                </button>
              )}
            </div>
            <button
              style={{
                background: 'transparent',
                border: 'none',
                cursor: sessionsLoading ? 'default' : 'pointer',
                color: 'var(--text-dim)',
                padding: 4,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexShrink: 0,
                transition: 'color 150ms ease',
              }}
              onClick={fetchSessions}
              disabled={sessionsLoading}
              onMouseEnter={(e) => { if (!sessionsLoading) e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
            >
              <RefreshCw
                size={13}
                strokeWidth={1.5}
                style={{
                  animation: sessionsLoading ? 'spin 1s linear infinite' : 'none',
                }}
              />
            </button>
          </div>

          {/* Tag filter bar (only when at least one tag exists) */}
          {availableTags.length > 0 && (
            <div
              className="flex flex-wrap gap-1 px-3 py-2"
              style={{ borderBottom: '1px solid var(--border-subtle)' }}
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

          {/* Session List — grouped by cwd (accordion) */}
          <div className="flex-1 overflow-y-auto py-1" ref={listRef}>
            {sessions.length === 0 && !sessionsLoading && (
              <div className="px-3 py-4" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
                {t('sidebar.noSessions')}
              </div>
            )}

            {filtersActive && sessions.length > 0 && renderedGroups.length === 0 && (
              <div className="px-3 py-4" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
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
              return (
                <div key={group.cwd} style={{ marginBottom: 2 }}>
                  {/* Group header — toggle + (hover) workdir menu & new-chat */}
                  <div
                    className="flex items-center gap-1 w-full px-3 py-1 min-w-0 group"
                    style={{
                      borderLeft: isActiveGroup ? '2px solid var(--blue)' : '2px solid transparent',
                      color: isActiveGroup ? 'var(--text-secondary)' : 'var(--text-dim)',
                    }}
                    title={group.cwd}
                  >
                    <button
                      type="button"
                      onClick={() => toggleGroup(group.cwd)}
                      className="flex items-center gap-1 flex-1 min-w-0"
                      style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'inherit', minWidth: 0, transition: 'color 150ms ease' }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)' }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = 'inherit' }}
                    >
                      {isExpanded
                        ? <ChevronDown size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                        : <ChevronRight size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />}
                      <FolderBookmark size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                      <span className="flex-1 truncate" style={{ fontSize: 12, minWidth: 0, textAlign: 'left' }}>
                        {shortCwd(group.cwd)}
                      </span>
                    </button>
                    {group.pinned && (
                      <Pin size={10} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--cyan)' }} />
                    )}
                    <span style={{ fontSize: 11, fontWeight: 600, flexShrink: 0 }}>{group.total}</span>
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
                        <SlidersVertical size={12} strokeWidth={1.5} />
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
                            <Pin size={12} strokeWidth={1.5} />
                            {group.pinned ? t('sidebar.unpinWorkdir') : t('sidebar.pinWorkdir')}
                          </button>
                          <button
                            className="flex items-center gap-2 px-3 w-full"
                            style={{ ...WD_MENU_ITEM_STYLE, color: 'var(--text-primary)' }}
                            onClick={(e) => { e.stopPropagation(); handleArchiveWorkdir(group.cwd, group.total) }}
                            onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                            onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                          >
                            <Archive size={12} strokeWidth={1.5} />
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
                      <SquarePen size={12} strokeWidth={1.5} />
                    </button>
                  </div>

                  {/* Group sessions */}
                  {isExpanded && (
                    <>
                      {group.sessions.map((session) => (
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
                          t={t}
                          indent={28}
                        />
                      ))}
                      {showMore && (
                        <button
                          type="button"
                          className="flex items-center gap-1 w-full py-1.5"
                          style={{
                            background: 'transparent',
                            border: 'none',
                            color: 'var(--text-dim)',
                            cursor: groupLoadingCwd === group.cwd ? 'default' : 'pointer',
                            fontSize: 12,
                            paddingLeft: 28,
                            transition: 'color 150ms ease',
                          }}
                          onClick={() => fetchMoreInGroup(group.cwd)}
                          disabled={groupLoadingCwd === group.cwd}
                          onMouseEnter={(e) => { if (groupLoadingCwd !== group.cwd) e.currentTarget.style.color = 'var(--text-secondary)' }}
                          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                        >
                          {groupLoadingCwd === group.cwd ? (
                            t('sidebar.loading')
                          ) : (
                            <>
                              <ChevronDown size={12} strokeWidth={1.5} />
                              {t('sidebar.moreInDir', { count: group.total - loadedCount })}
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
        </>
      )}

      {/* Bottom: Settings + Toggle */}
      <div
        className="p-2 flex items-center"
        style={{
          borderTop: '1px solid var(--border-subtle)',
          justifyContent: collapsed ? 'center' : 'space-between',
        }}
      >
        {collapsed ? (
          /* Collapse toggle only — settings icon is in collapsed top section */
          <button
            style={{
              width: 28,
              height: 28,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-dim)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              borderRadius: '4px',
              transition: 'color 150ms ease, background 150ms ease',
            }}
            onClick={toggleCollapsed}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--text-secondary)'
              e.currentTarget.style.background = 'var(--bg-elevated)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = 'var(--text-dim)'
              e.currentTarget.style.background = 'transparent'
            }}
            title={t('sidebar.expand')}
          >
            <PanelLeft size={16} strokeWidth={1.5} />
          </button>
        ) : (
          <>
            {/* Settings button with popover */}
            <div className="relative">
              <SettingsPopover />
              <button
                className="flex items-center gap-2"
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  color: 'var(--text-dim)',
                  padding: '4px 6px',
                  borderRadius: '4px',
                  fontSize: 13,
                  transition: 'color 150ms ease, background 150ms ease',
                }}
                onClick={toggleSettingsPopover}
                onMouseEnter={(e) => {
                  e.currentTarget.style.color = 'var(--text-secondary)'
                  e.currentTarget.style.background = 'var(--bg-elevated)'
                }}
                onMouseLeave={(e) => {
                  e.currentTarget.style.color = 'var(--text-dim)'
                  e.currentTarget.style.background = 'transparent'
                }}
                title={t('sidebar.settings')}
              >
                <Settings size={14} strokeWidth={1.5} />
                <span>{t('sidebar.settings')}</span>
              </button>
            </div>
            {/* Collapse toggle */}
            <button
              style={{
                width: 28,
                height: 28,
                background: 'transparent',
                border: 'none',
                cursor: 'pointer',
                color: 'var(--text-dim)',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                borderRadius: '4px',
                transition: 'color 150ms ease, background 150ms ease',
              }}
              onClick={toggleCollapsed}
              onMouseEnter={(e) => {
                e.currentTarget.style.color = 'var(--text-secondary)'
                e.currentTarget.style.background = 'var(--bg-elevated)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = 'var(--text-dim)'
                e.currentTarget.style.background = 'transparent'
              }}
              title={t('sidebar.collapse')}
            >
              <PanelLeftClose size={16} strokeWidth={1.5} />
            </button>
          </>
        )}
      </div>

      {!collapsed && <SidebarResizer />}

      {/* Group hover CSS for delete button */}
      <style>{`
        .group:hover .group-hover-visible { opacity: 1 !important; }
        @keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
      `}</style>
    </aside>
  )
}
