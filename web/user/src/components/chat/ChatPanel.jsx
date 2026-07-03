import { Suspense, useEffect, useRef, useState } from 'react'
import { FileDiff, FolderTree, MoreVertical, PanelRight, SquareTerminal, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import useSidebarStore from '../../stores/sidebarStore'
import useSandboxStore from '../../stores/sandboxStore'
import useTaskStore from '../../stores/taskStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useUiStore from '@shared/stores/uiStore'
import useSplitStore from '../../stores/splitStore'
import CopyButton from '@shared/components/shared/CopyButton'
import MessageListBoundary from './MessageListBoundary'
import ChatInput from './ChatInput'
import UsageStatsOverview from './UsageStatsOverview'
import QuickActionChips from './QuickActionChips'
import CheckpointToggle from './CheckpointToggle'
import RewindBanner from './RewindBanner'
import { getSplitParams, isSplitPane } from '../../utils/splitMode'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'

const MessageList = lazyWithChunkReload(() => import('./MessageList'))
const SESSION_HEADER_HEIGHT = 27

function CanvasShortcut({ icon: Icon, title, hidden, indicator, onClick }) {
  if (hidden) return null
  const hasIndicator = !!indicator
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      className="inline-flex items-center justify-center"
      style={{
        minWidth: 26,
        width: hasIndicator ? 'auto' : 26,
        height: 26,
        border: 'none',
        background: 'transparent',
        color: 'var(--text-dim)',
        cursor: 'pointer',
        padding: hasIndicator ? '0 5px' : 0,
        gap: hasIndicator ? 4 : 0,
        transition: 'color 150ms ease, background 150ms ease',
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.color = 'var(--text-secondary)'
        event.currentTarget.style.background = 'var(--bg-elevated)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.color = 'var(--text-dim)'
        event.currentTarget.style.background = 'transparent'
      }}
    >
      {hasIndicator && (
        <span
          className="inline-flex items-center"
          style={{
            maxWidth: 68,
            minWidth: 0,
            overflow: 'hidden',
            textOverflow: 'ellipsis',
            whiteSpace: 'nowrap',
            color: 'var(--blue)',
            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
            fontSize: 10,
            fontWeight: 600,
            lineHeight: '14px',
            letterSpacing: '0.06em',
          }}
        >
          {indicator.label}
          {indicator.count ? ` ${indicator.count}` : ''}
        </span>
      )}
      <Icon size={16} strokeWidth={1.5} />
    </button>
  )
}

export default function ChatPanel() {
  const { t } = useTranslation()
  const [headerMenuOpen, setHeaderMenuOpen] = useState(false)
  const headerMenuRef = useRef(null)
  const embeddedPane = isSplitPane()
  const { paneId } = getSplitParams()
  const sessionId = useChatStore((s) => s.sessionId)
  const messages = useChatStore((s) => s.messages)
  const sidebarSessions = useSidebarStore((s) => s.sessions)
  const agentWorkspace = useSandboxStore((s) => s.workspace)
  const fetchHealth = useSandboxStore((s) => s.fetchHealth)
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const canvasMinimized = useUiStore((s) => s.canvasMinimized)
  const activeCanvasTab = useUiStore((s) => s.activeCanvasTab)
  const showCanvas = useUiStore((s) => s.showCanvas)
  const setCanvasMinimized = useUiStore((s) => s.setCanvasMinimized)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)
  // Terminal toggle — relocated from the (removed) NavBar into the session header.
  const terminalOpen = useUiStore((s) => s.terminalOpen)
  const toggleTerminal = useUiStore((s) => s.toggleTerminal)
  const terminalFeatureEnabled = useUiStore((s) => s.terminalFeatureEnabled)
  const terminalSessionActive = useUiStore((s) => s.terminalSessionActive)
  const terminalActiveCount = useUiStore((s) => s.terminalActiveCount) || (terminalSessionActive ? 1 : 0)
  const tasks = useTaskStore((s) => s.tasks)
  const todos = useTaskStore((s) => s.todos)
  const fileBrowserCount = useFileBrowserStore((s) => s.tabs.length)
  const changeOpsCount = useFileOpsStore((s) => s.fileOps.filter((op) => op.type === 'write' || op.type === 'edit').length)
  const splitPaneCount = useSplitStore((s) => s.panes.length)
  const closePane = useSplitStore((s) => s.closePane)
  const activeSidebarSession = sidebarSessions.find((s) => s.sessionId === sessionId || s.id === sessionId)
  const sessionTitle = activeSidebarSession?.name || (sessionId ? sessionId : '')
  const isEmpty = messages.length === 0
  // First-page bootstrap: wake the sandbox and learn the workspace via the
  // agent-runner's /api/health (drives the waking/ready toasts in client.js).
  useEffect(() => { fetchHealth() }, [fetchHealth])
  useEffect(() => {
    if (!headerMenuOpen) return undefined
    const handlePointerDown = (event) => {
      if (!headerMenuRef.current?.contains(event.target)) setHeaderMenuOpen(false)
    }
    const handleKeyDown = (event) => {
      if (event.key === 'Escape') setHeaderMenuOpen(false)
    }
    document.addEventListener('pointerdown', handlePointerDown)
    document.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('pointerdown', handlePointerDown)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [headerMenuOpen])
  // cwd comes entirely from the agent-runner: the active session's cwd, else the
  // /api/health workspace. Empty until one resolves — CwdIndicator then shows '~'.
  const activeCwd = activeSidebarSession?.cwd || agentWorkspace || ''
  const activateCanvasTab = (tab) => {
    setActiveCanvasTab(tab)
    setCanvasMinimized(false)
    showCanvas()
  }
  const isCanvasTabVisible = (tab) => {
    if (!canvasVisible || canvasMinimized) return false
    if (tab === 'changes') return activeCanvasTab === 'changes' || activeCanvasTab === 'files'
    return activeCanvasTab === tab
  }
  const taskTotal = Object.keys(tasks || {}).length
  const todoTotal = todos ? todos.length : 0
  const todoCompleted = todos ? todos.filter((todo) => todo.status === 'completed').length : 0
  const canvasMenuIndicator = canvasVisible && canvasMinimized
    ? activeCanvasTab === 'plan'
      ? { label: t('canvas.rail.plan') }
      : activeCanvasTab === 'file-browser'
        ? { label: t('canvas.rail.files'), count: fileBrowserCount || null }
        : activeCanvasTab === 'changes' || activeCanvasTab === 'files'
          ? { label: t('canvas.rail.changes'), count: changeOpsCount || null }
          : activeCanvasTab === 'browser'
            ? { label: t('canvas.rail.browser') }
            : { label: t('canvas.rail.tasks'), count: todoTotal ? `${todoCompleted}/${todoTotal}` : taskTotal || null }
    : null
  const showSplitClose = embeddedPane || splitPaneCount > 1
  const closeSplitPane = () => {
    if (embeddedPane) {
      window.parent?.postMessage({ type: 'priva:split-pane-close', paneId }, window.location.origin)
      return
    }
    const activePaneId = useSplitStore.getState().activePaneId
    if (activePaneId && useSplitStore.getState().panes.length > 1) {
      closePane(activePaneId)
    }
  }
  const headerMenuItems = [
    {
      id: 'tasks',
      label: t('canvas.tasks'),
      icon: PanelRight,
      active: isCanvasTabVisible('tasks'),
      onClick: () => activateCanvasTab('tasks'),
    },
    {
      id: 'file-browser',
      label: t('canvas.fileBrowser'),
      icon: FolderTree,
      active: isCanvasTabVisible('file-browser'),
      onClick: () => activateCanvasTab('file-browser'),
    },
    {
      id: 'changes',
      label: t('canvas.changeReview'),
      icon: FileDiff,
      active: isCanvasTabVisible('changes'),
      onClick: () => activateCanvasTab('changes'),
    },
    ...(!embeddedPane && terminalFeatureEnabled ? [{
      id: 'terminal',
      label: terminalActiveCount > 0
        ? t('terminal.openWithCount', { count: terminalActiveCount })
        : t('terminal.open'),
      icon: SquareTerminal,
      active: terminalOpen || terminalActiveCount > 0,
      danger: terminalOpen || terminalActiveCount > 0,
      onClick: toggleTerminal,
    }] : []),
  ]
  const runHeaderMenuItem = (item) => {
    item.onClick()
    setHeaderMenuOpen(false)
  }

  // The chat header is a permanent fixture — rendered in both the empty/welcome
  // state and the active conversation. The session name is simply empty when no
  // session is active.
  const headerBar = (
    <div
      className="flex items-center justify-between px-4 flex-shrink-0"
      style={{
        height: SESSION_HEADER_HEIGHT,
        borderBottom: '1px solid var(--border-subtle)',
        background: 'var(--bg-surface)',
      }}
    >
      <div className="flex items-center gap-1 min-w-0" style={{ marginRight: 12 }}>
        <span
          className="truncate"
          style={{ color: 'var(--text-secondary)', fontSize: 13, minWidth: 0 }}
          title={sessionTitle}
        >
          {sessionTitle}
        </span>
        {sessionId && (
          <span className="flex-shrink-0" title={t('sidebar.copySessionId')}>
            <CopyButton content={sessionId} inline />
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        <CheckpointToggle />
        <div ref={headerMenuRef} className="relative">
          <CanvasShortcut
            icon={MoreVertical}
            title={canvasMenuIndicator
              ? `${t('common.more', { defaultValue: '更多' })} · ${canvasMenuIndicator.label}${canvasMenuIndicator.count ? ` ${canvasMenuIndicator.count}` : ''}`
              : t('common.more', { defaultValue: '更多' })}
            hidden={false}
            indicator={canvasMenuIndicator}
            onClick={() => setHeaderMenuOpen((open) => !open)}
          />
          {headerMenuOpen && (
            <div
              className="absolute"
              style={{
                top: '100%',
                right: 0,
                zIndex: 80,
                width: 196,
                marginTop: 4,
                background: 'var(--bg-surface)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                padding: 4,
              }}
            >
              {headerMenuItems.map((item) => {
                const Icon = item.icon
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => runHeaderMenuItem(item)}
                    className="flex items-center gap-2 w-full"
                    style={{
                      minWidth: 0,
                      height: 30,
                      padding: '0 8px',
                      background: item.active ? 'var(--bg-elevated)' : 'transparent',
                      border: 'none',
                      borderLeft: `2px solid ${item.active ? (item.danger ? 'var(--red)' : 'var(--blue)') : 'transparent'}`,
                      borderRadius: 2,
                      color: item.danger ? 'var(--red)' : item.active ? 'var(--text-primary)' : 'var(--text-secondary)',
                      cursor: 'pointer',
                      fontSize: 12,
                      textAlign: 'left',
                      transition: 'background 150ms ease, color 150ms ease',
                    }}
                    onMouseEnter={(event) => {
                      event.currentTarget.style.background = 'var(--bg-elevated)'
                      event.currentTarget.style.color = item.danger ? 'var(--red)' : 'var(--text-primary)'
                    }}
                    onMouseLeave={(event) => {
                      event.currentTarget.style.background = item.active ? 'var(--bg-elevated)' : 'transparent'
                      event.currentTarget.style.color = item.danger ? 'var(--red)' : item.active ? 'var(--text-primary)' : 'var(--text-secondary)'
                    }}
                  >
                    <Icon size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                    <span className="truncate" style={{ minWidth: 0 }}>{item.label}</span>
                  </button>
                )
              })}
            </div>
          )}
        </div>
        <CanvasShortcut
          icon={X}
          title={t('split.closePane', { defaultValue: '关闭窗格' })}
          hidden={!showSplitClose}
          onClick={closeSplitPane}
        />
      </div>
    </div>
  )

  if (isEmpty) {
    const TRACK_STYLE = { width: '70%', maxWidth: 1000, margin: '0 auto' }

    return (
      <div
        className="flex flex-col flex-1 min-w-0"
        style={{ background: 'var(--bg-base)' }}
      >
        {headerBar}
        {/* Top: scrollable overview (card at half-track width) + chips */}
        <div
          className="flex-1 overflow-y-auto"
          style={{ background: 'var(--bg-base)' }}
        >
          <div className="flex min-h-full flex-col">
            <div style={{ ...TRACK_STYLE, paddingTop: 24 }}>
              <div style={{ width: '50%', minWidth: 320 }}>
                <UsageStatsOverview />
              </div>
            </div>
            <div
              className="flex flex-1 items-center justify-center"
              style={{ padding: '16px 0 24px' }}
            >
              <QuickActionChips />
            </div>
          </div>
        </div>

        {/* Bottom: pinned input at 70% track, same left edge as card */}
        <div
          className="flex-shrink-0 chat-empty-input"
          style={{ ...TRACK_STYLE, paddingBottom: 12 }}
        >
          <ChatInput cwd={activeCwd} cwdPlacement="below" />
        </div>
      </div>
    )
  }

  return (
    <div
      className="flex flex-col flex-1 min-w-0"
      style={{ background: 'var(--bg-base)', height: '100%', minHeight: 0 }}
    >
      {headerBar}
      <RewindBanner />
      <MessageListBoundary resetKey={sessionId ? `${sessionId}:${messages.length}` : `draft:${messages.length}`}>
        <Suspense fallback={<div className="flex-1" style={{ background: 'var(--bg-base)' }} />}>
          <MessageList />
        </Suspense>
      </MessageListBoundary>
      <ChatInput cwd={activeCwd} cwdPlacement="below" />
    </div>
  )
}
