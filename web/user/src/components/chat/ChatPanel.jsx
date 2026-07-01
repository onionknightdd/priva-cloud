import { Suspense, useEffect } from 'react'
import { FileDiff, FolderTree, PanelRight, SquareTerminal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import useSidebarStore from '../../stores/sidebarStore'
import useSandboxStore from '../../stores/sandboxStore'
import useUiStore from '@shared/stores/uiStore'
import CopyButton from '@shared/components/shared/CopyButton'
import MessageListBoundary from './MessageListBoundary'
import ChatInput from './ChatInput'
import UsageStatsOverview from './UsageStatsOverview'
import QuickActionChips from './QuickActionChips'
import CheckpointToggle from './CheckpointToggle'
import RewindBanner from './RewindBanner'
import { isSplitPane } from '../../utils/splitMode'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'

const MessageList = lazyWithChunkReload(() => import('./MessageList'))

function CanvasShortcut({ icon: Icon, title, hidden, onClick }) {
  if (hidden) return null
  return (
    <button
      type="button"
      title={title}
      onClick={onClick}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        width: 26,
        height: 26,
        border: 'none',
        background: 'transparent',
        color: 'var(--text-dim)',
        cursor: 'pointer',
        padding: 0,
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
      <Icon size={16} strokeWidth={1.5} />
    </button>
  )
}

export default function ChatPanel() {
  const { t } = useTranslation()
  const embeddedPane = isSplitPane()
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
  const activeSidebarSession = sidebarSessions.find((s) => s.sessionId === sessionId || s.id === sessionId)
  const sessionTitle = activeSidebarSession?.name || (sessionId ? sessionId : '')
  const isEmpty = messages.length === 0
  // First-page bootstrap: wake the sandbox and learn the workspace via the
  // agent-runner's /api/health (drives the waking/ready toasts in client.js).
  useEffect(() => { fetchHealth() }, [fetchHealth])
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

  // The chat header is a permanent fixture — rendered in both the empty/welcome
  // state and the active conversation. The session name is simply empty when no
  // session is active.
  const headerBar = (
    <div
      className="flex items-center justify-between px-4 flex-shrink-0"
      style={{
        height: 40,
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
        {!embeddedPane && terminalFeatureEnabled && (
          <button
            type="button"
            onClick={toggleTerminal}
            title={terminalActiveCount > 0
              ? t('terminal.openWithCount', { count: terminalActiveCount })
              : t('terminal.open')}
            style={{
              position: 'relative',
              display: 'inline-flex',
              alignItems: 'center',
              justifyContent: 'center',
              width: 26,
              height: 26,
              border: 'none',
              background: 'transparent',
              color: terminalOpen || terminalActiveCount > 0 ? 'var(--red)' : 'var(--text-dim)',
              cursor: 'pointer',
              padding: 0,
              transition: 'color 150ms ease, background 150ms ease',
            }}
            onMouseEnter={(e) => {
              e.currentTarget.style.color = 'var(--red)'
              e.currentTarget.style.background = 'var(--bg-elevated)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.color = terminalOpen || terminalActiveCount > 0 ? 'var(--red)' : 'var(--text-dim)'
              e.currentTarget.style.background = 'transparent'
            }}
          >
            <SquareTerminal size={16} strokeWidth={1.5} />
            {terminalActiveCount > 0 && (
              <span
                style={{
                  position: 'absolute',
                  top: -2,
                  right: -2,
                  minWidth: 14,
                  height: 14,
                  padding: '0 3px',
                  borderRadius: 4,
                  background: 'var(--red)',
                  color: 'var(--text-inverse)',
                  fontSize: 9,
                  fontWeight: 700,
                  lineHeight: '14px',
                  textAlign: 'center',
                  boxSizing: 'border-box',
                  pointerEvents: 'none',
                }}
              >
                {terminalActiveCount}
              </span>
            )}
          </button>
        )}
        <CheckpointToggle />
        <CanvasShortcut
          icon={PanelRight}
          title={t('canvas.tasks')}
          hidden={isCanvasTabVisible('tasks')}
          onClick={() => activateCanvasTab('tasks')}
        />
        <CanvasShortcut
          icon={FolderTree}
          title={t('canvas.fileBrowser')}
          hidden={isCanvasTabVisible('file-browser')}
          onClick={() => activateCanvasTab('file-browser')}
        />
        <CanvasShortcut
          icon={FileDiff}
          title={t('canvas.changeReview')}
          hidden={isCanvasTabVisible('changes')}
          onClick={() => activateCanvasTab('changes')}
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
