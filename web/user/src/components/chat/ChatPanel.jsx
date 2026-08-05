import { Suspense, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { FileDiff, FolderTree, PanelRight, SquareTerminal, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import useSidebarStore from '../../stores/sidebarStore'
import useSandboxStore from '../../stores/sandboxStore'
import useTaskStore from '../../stores/taskStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useWorkflowStore from '../../stores/workflowStore'
import useUiStore from '@shared/stores/uiStore'
import useSplitStore from '../../stores/splitStore'
import { renameSession as apiRenameSession } from '../../api/sessions'
import CopyButton from '@shared/components/shared/CopyButton'
import MessageListBoundary from './MessageListBoundary'
import ChatInput from './ChatInput'
import { UsageStatsOverviewTitle } from './UsageStatsOverview'
import RecentActivities from './RecentActivities'
import QuickActionChips from './QuickActionChips'
import RewindBanner from './RewindBanner'
import SessionRecap from './SessionRecap'
import TaskProgressCapsule from './TaskProgressCapsule'
import { getSplitParams, isSplitPane } from '../../utils/splitMode'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'

const MessageList = lazyWithChunkReload(() => import('./MessageList'))
const SESSION_HEADER_HEIGHT = 30
const TRACKED_TASK_TOOL_NAMES = new Set([
  'TaskOutput',
  'TaskStop',
  'delegate_to_openclaw',
  'mcp__priva_openclaw__delegate_to_openclaw',
])

function toMotionRect(rect) {
  return {
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  }
}

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

function HeaderBadgeShortcut({
  icon: Icon,
  count,
  title,
  accent = 'var(--blue)',
  dataAttrs,
  hiddenDuringMotion = false,
  onClick,
}) {
  const displayCount = count > 99 ? '99+' : String(count)
  const showBadge = count > 0

  return (
    <button
      type="button"
      aria-label={title}
      title={title}
      {...(dataAttrs || {})}
      onClick={onClick}
      className="inline-flex items-center justify-center flex-shrink-0"
      style={{
        position: 'relative',
        width: 24,
        height: 20,
        padding: 0,
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border-subtle)',
        borderLeft: `2px solid ${accent}`,
        borderRadius: 2,
        color: accent,
        cursor: 'pointer',
        opacity: hiddenDuringMotion ? 0 : 1,
        pointerEvents: hiddenDuringMotion ? 'none' : 'auto',
        transition: 'opacity 150ms ease, background 150ms ease, border-color 150ms ease, color 150ms ease',
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.background = 'var(--bg-elevated)'
        event.currentTarget.style.borderColor = 'var(--blue)'
        event.currentTarget.style.borderLeftColor = accent
        event.currentTarget.style.color = 'var(--text-primary)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.background = 'var(--bg-elevated)'
        event.currentTarget.style.borderColor = 'var(--border-subtle)'
        event.currentTarget.style.borderLeftColor = accent
        event.currentTarget.style.color = accent
      }}
    >
      <Icon size={14} strokeWidth={1.5} />
      {showBadge && (
        <span
          className="font-semibold"
          style={{
            position: 'absolute',
            top: -4,
            right: -5,
            minWidth: 14,
            height: 12,
            padding: '0 2px',
            boxSizing: 'border-box',
            borderRadius: 4,
            border: '1px solid var(--bg-surface)',
            background: accent,
            color: 'var(--text-inverse)',
            fontSize: 8,
            lineHeight: '10px',
            textAlign: 'center',
          }}
        >
          {displayCount}
        </span>
      )}
    </button>
  )
}

function TerminalCheckpointShortcut({ count, title, hiddenDuringMotion, onClick }) {
  if (count <= 0) return null
  return (
    <HeaderBadgeShortcut
      icon={SquareTerminal}
      count={count}
      title={title}
      accent="var(--purple)"
      dataAttrs={{ 'data-terminal-minimize-anchor': 'true' }}
      hiddenDuringMotion={hiddenDuringMotion}
      onClick={onClick}
    />
  )
}

function isSubagentTool(block) {
  return block?.type === 'tool_use' && (block.name === 'Agent' || block.name === 'Task')
}

function isTodoWriteTool(block) {
  return block?.type === 'tool_use' && block.name === 'TodoWrite'
}

function isWorkflowTool(block) {
  return block?.type === 'tool_use' && block.name === 'Workflow'
}

function isIndependentTaskTool(block) {
  return block?.type === 'tool_use'
    && !isSubagentTool(block)
    && !isTodoWriteTool(block)
    && !isWorkflowTool(block)
    && (block.input?.run_in_background === true || TRACKED_TASK_TOOL_NAMES.has(block.name))
}

function extractTodoItems(block) {
  const inputItems = block?.input?.todos
  if (Array.isArray(inputItems)) return inputItems
  const result = block?.result
  const toolUseResult = result?.tool_use_result || result?.toolUseResult
  const resultItems = toolUseResult?.newTodos || toolUseResult?.todos || toolUseResult?.new_todos
  if (Array.isArray(resultItems)) return resultItems
  if (typeof result?.content !== 'string' || !result.content.trim()) return []
  try {
    const parsed = JSON.parse(result.content)
    if (Array.isArray(parsed)) return parsed
    const parsedItems = parsed?.newTodos || parsed?.todos || parsed?.new_todos
    return Array.isArray(parsedItems) ? parsedItems : []
  } catch {
    return []
  }
}

function mergeTrackingCounts(base, next) {
  return {
    subagents: base.subagents + next.subagents,
    tasks: base.tasks + next.tasks,
    todos: base.todos + next.todos,
    workflows: base.workflows + next.workflows,
  }
}

function getTrackingTotal(counts) {
  return counts.subagents + counts.tasks + counts.todos + counts.workflows
}

function collectTrackingCounts(blocks, subagentContent, seen = new Set()) {
  let counts = { subagents: 0, tasks: 0, todos: 0, workflows: 0 }
  if (!Array.isArray(blocks)) return counts

  for (let index = 0; index < blocks.length; index += 1) {
    const block = blocks[index]
    if (!block?.id && !block?.name) continue
    const key = `${block.name || block.type}:${block.id || index}`
    if (seen.has(key)) continue
    seen.add(key)

    if (isSubagentTool(block)) {
      counts.subagents += 1
      counts = mergeTrackingCounts(counts, collectTrackingCounts(subagentContent[block.id] || [], subagentContent, seen))
    } else if (isIndependentTaskTool(block)) {
      counts.tasks += 1
    } else if (isTodoWriteTool(block)) {
      counts.todos += Math.max(1, extractTodoItems(block).length)
    } else if (isWorkflowTool(block)) {
      counts.workflows += 1
    }
  }

  return counts
}

function getCurrentRoundTrackingCounts(messages, subagentContent, fallback) {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const message = messages[index]
    if (message.role !== 'assistant') continue
    const counts = collectTrackingCounts(message.content, subagentContent)
    if (getTrackingTotal(counts) > 0) return counts
  }
  return fallback
}

function formatTrackingTitle(label, counts) {
  const total = getTrackingTotal(counts)
  const parts = []
  if (counts.subagents > 0) parts.push(`SubAgent ${counts.subagents}`)
  if (counts.tasks > 0) parts.push(`Task ${counts.tasks}`)
  if (counts.todos > 0) parts.push(`Todo ${counts.todos}`)
  if (counts.workflows > 0) parts.push(`Workflow ${counts.workflows}`)
  return parts.length ? `${label} · ${total} (${parts.join(' · ')})` : label
}

export default function ChatPanel() {
  const { t } = useTranslation()
  const embeddedPane = isSplitPane()
  const { paneId } = getSplitParams()
  const sessionId = useChatStore((s) => s.sessionId)
  const messages = useChatStore((s) => s.messages)
  const subagentContent = useChatStore((s) => s.subagentContent)
  const sidebarSessions = useSidebarStore((s) => s.sessions)
  const agentWorkspace = useSandboxStore((s) => s.workspace)
  const fetchHealth = useSandboxStore((s) => s.fetchHealth)
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const canvasMinimized = useUiStore((s) => s.canvasMinimized)
  const activeCanvasTab = useUiStore((s) => s.activeCanvasTab)
  const showCanvas = useUiStore((s) => s.showCanvas)
  const hideCanvas = useUiStore((s) => s.hideCanvas)
  const showCanvasMenu = useUiStore((s) => s.showCanvasMenu)
  const setCanvasMinimized = useUiStore((s) => s.setCanvasMinimized)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)
  // Terminal toggle — relocated from the (removed) NavBar into the session header.
  const toggleTerminal = useUiStore((s) => s.toggleTerminal)
  const terminalFeatureEnabled = useUiStore((s) => s.terminalFeatureEnabled)
  const terminalMinimized = useUiStore((s) => s.terminalMinimized)
  const terminalSessionActive = useUiStore((s) => s.terminalSessionActive)
  const terminalActiveCount = useUiStore((s) => s.terminalActiveCount) || (terminalSessionActive ? 1 : 0)
  const setTerminalMotionAnchorRect = useUiStore((s) => s.setTerminalMotionAnchorRect)
  const terminalMotionActive = useUiStore((s) => s.terminalMotionActive)
  const tasks = useTaskStore((s) => s.tasks)
  const taskOrder = useTaskStore((s) => s.taskOrder)
  const todos = useTaskStore((s) => s.todos)
  const todoWriteInfo = useTaskStore((s) => s.todoWriteInfo)
  const workflows = useWorkflowStore((s) => s.workflows)
  const workflowOrder = useWorkflowStore((s) => s.workflowOrder)
  const fileBrowserCount = useFileBrowserStore((s) => s.tabs.length)
  const changeOpsCount = useFileOpsStore((s) => s.fileOps.filter((op) => op.type === 'write' || op.type === 'edit').length)
  const splitPaneCount = useSplitStore((s) => s.panes.length)
  const closePane = useSplitStore((s) => s.closePane)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const activeSidebarSession = sidebarSessions.find((s) => s.sessionId === sessionId || s.id === sessionId)
  const sessionTitle = activeSidebarSession?.name || (sessionId ? sessionId : '')
  const [renamingSessionTitle, setRenamingSessionTitle] = useState(false)
  const [sessionTitleDraft, setSessionTitleDraft] = useState('')
  const sessionTitleInputMeasureRef = useRef(null)
  const [sessionTitleInputWidth, setSessionTitleInputWidth] = useState(0)
  const sessionTitleHandledRef = useRef(false)
  const isEmpty = messages.length === 0
  // First-page bootstrap: wake the sandbox and learn the workspace via the
  // agent-runner's /api/health (drives the waking/ready toasts in client.js).
  useEffect(() => { fetchHealth() }, [fetchHealth])
  useEffect(() => {
    setRenamingSessionTitle(false)
    setSessionTitleDraft('')
    setSessionTitleInputWidth(0)
    sessionTitleHandledRef.current = false
  }, [sessionId])
  useLayoutEffect(() => {
    if (!renamingSessionTitle || !sessionTitleInputMeasureRef.current) return
    setSessionTitleInputWidth(Math.ceil(sessionTitleInputMeasureRef.current.getBoundingClientRect().width))
  }, [renamingSessionTitle, sessionTitleDraft])
  // cwd comes entirely from the agent-runner: the active session's cwd, else the
  // /api/health workspace. Empty until one resolves — CwdIndicator then shows '~'.
  const activeCwd = activeSidebarSession?.cwd || agentWorkspace || ''
  const activateCanvasTab = (tab) => {
    setActiveCanvasTab(tab)
    setCanvasMinimized(false)
    showCanvas()
  }
  const toggleCanvasMenu = () => {
    if (canvasVisible && !canvasMinimized) {
      hideCanvas()
      return
    }
    showCanvasMenu()
  }
  const isCanvasTabVisible = (tab) => {
    if (!canvasVisible || canvasMinimized) return false
    if (tab === 'changes') return activeCanvasTab === 'changes' || activeCanvasTab === 'files'
    return activeCanvasTab === tab
  }
  const todoTotal = todos ? todos.length : 0
  const fallbackTrackingCounts = {
    subagents: 0,
    tasks: taskOrder.filter((id) => tasks[id] && tasks[id].task_type !== 'local_workflow').length,
    todos: todoTotal || (todoWriteInfo ? 1 : 0),
    workflows: workflowOrder.filter((id) => workflows[id]).length,
  }
  const currentTrackingCounts = getCurrentRoundTrackingCounts(messages, subagentContent, fallbackTrackingCounts)
  const taskTrackingTotal = getTrackingTotal(currentTrackingCounts)
  const showCanvasShortcuts = canvasVisible && canvasMinimized
  const showTasksShortcut = showCanvasShortcuts
  const showFilesShortcut = showCanvasShortcuts
  const showChangesShortcut = showCanvasShortcuts
  const tasksShortcutTitle = formatTrackingTitle(t('canvas.tasks'), currentTrackingCounts)
  const filesShortcutTitle = fileBrowserCount > 0
    ? `${t('canvas.fileBrowser')} · ${fileBrowserCount}`
    : t('canvas.fileBrowser')
  const changesShortcutTitle = changeOpsCount > 0
    ? `${t('canvas.changeReview')} · ${changeOpsCount}`
    : t('canvas.changeReview')
  const showTerminalShortcut = !embeddedPane && terminalFeatureEnabled && terminalMinimized && terminalActiveCount > 0
  const terminalShortcutTitle = terminalActiveCount > 0
    ? t('terminal.openWithCount', { count: terminalActiveCount })
    : t('terminal.open')
  const restoreTerminalFromShortcut = (event) => {
    setTerminalMotionAnchorRect(toMotionRect(event.currentTarget.getBoundingClientRect()))
    toggleTerminal()
  }
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
  const startSessionTitleRename = () => {
    if (!sessionId) return
    sessionTitleHandledRef.current = false
    setSessionTitleDraft(sessionTitle)
    setSessionTitleInputWidth(0)
    setRenamingSessionTitle(true)
  }
  const cancelSessionTitleRename = () => {
    sessionTitleHandledRef.current = true
    setRenamingSessionTitle(false)
    setSessionTitleDraft('')
  }
  const commitSessionTitleRename = async () => {
    if (!sessionId || sessionTitleHandledRef.current) return
    sessionTitleHandledRef.current = true
    const trimmed = sessionTitleDraft.trim()
    setRenamingSessionTitle(false)
    setSessionTitleDraft('')
    if (!trimmed || trimmed === sessionTitle) return
    try {
      await apiRenameSession(sessionId, trimmed)
      useSidebarStore.setState((state) => ({
        sessions: state.sessions.map((row) => (
          row.id === sessionId || row.sessionId === sessionId
            ? { ...row, name: trimmed, customTitle: trimmed }
            : row
        )),
      }))
    } catch (err) {
      showConfirmDialog({
        title: t('sidebar.renameFailed'),
        message: String(err?.message || err),
        confirmLabel: t('confirm.ok'),
      })
    }
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
      <div className="flex flex-1 items-center gap-1 min-w-0" style={{ marginRight: 12 }}>
        {renamingSessionTitle ? (
          <>
            <span
              ref={sessionTitleInputMeasureRef}
              aria-hidden="true"
              style={{
                position: 'fixed',
                left: -9999,
                top: -9999,
                visibility: 'hidden',
                whiteSpace: 'pre',
                pointerEvents: 'none',
                boxSizing: 'border-box',
                border: '1px solid var(--border)',
                padding: '2px 4px',
                fontFamily: 'inherit',
                fontSize: 13,
                fontWeight: 400,
              }}
            >
              {sessionTitleDraft || ' '}
            </span>
            <input
              type="text"
              autoFocus
              aria-label={t('sidebar.rename')}
              value={sessionTitleDraft}
              onChange={(event) => setSessionTitleDraft(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') {
                  event.preventDefault()
                  commitSessionTitleRename()
                } else if (event.key === 'Escape') {
                  event.preventDefault()
                  cancelSessionTitleRename()
                }
              }}
              onBlur={commitSessionTitleRename}
              size={Math.max(sessionTitleDraft.length, 1)}
              className="min-w-0"
              style={{
                background: 'var(--bg-elevated)',
                border: '1px solid var(--border)',
                borderRadius: 2,
                color: 'var(--text-primary)',
                outline: 'none',
                fontFamily: 'inherit',
                fontSize: 13,
                fontWeight: 400,
                padding: '2px 4px',
                width: sessionTitleInputWidth ? `${sessionTitleInputWidth}px` : undefined,
                maxWidth: '100%',
                flex: '0 1 auto',
                boxSizing: 'border-box',
              }}
            />
            {sessionId && (
              <span
                className="flex-shrink-0"
                title={t('sidebar.copySessionId')}
                onMouseDown={(event) => event.preventDefault()}
              >
                <CopyButton content={sessionId} inline />
              </span>
            )}
          </>
        ) : (
          <button
            type="button"
            disabled={!sessionId}
            className="min-w-0 truncate"
            style={{
              background: 'transparent',
              border: 'none',
              color: 'var(--text-secondary)',
              cursor: sessionId ? 'pointer' : 'default',
              flex: '0 1 auto',
              fontSize: 13,
              minWidth: 0,
              width: 'fit-content',
              maxWidth: '100%',
              padding: 0,
              textAlign: 'left',
              transition: 'color 150ms ease',
            }}
            title={sessionId ? `${sessionTitle}\n${t('sidebar.rename')}` : sessionTitle}
            onClick={startSessionTitleRename}
            onMouseEnter={(event) => {
              if (sessionId) event.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            {sessionTitle}
          </button>
        )}
        {!renamingSessionTitle && sessionId && (
          <span className="flex-shrink-0" title={t('sidebar.copySessionId')}>
            <CopyButton content={sessionId} inline />
          </span>
        )}
      </div>
      <div className="flex items-center gap-1 flex-shrink-0">
        {showTasksShortcut && (
          <HeaderBadgeShortcut
            icon={PanelRight}
            count={taskTrackingTotal}
            title={tasksShortcutTitle}
            onClick={() => activateCanvasTab('tasks')}
          />
        )}
        {showFilesShortcut && (
          <HeaderBadgeShortcut
            icon={FolderTree}
            count={fileBrowserCount}
            title={filesShortcutTitle}
            onClick={() => activateCanvasTab('file-browser')}
          />
        )}
        {showChangesShortcut && (
          <HeaderBadgeShortcut
            icon={FileDiff}
            count={changeOpsCount}
            title={changesShortcutTitle}
            onClick={() => activateCanvasTab('changes')}
          />
        )}
        {showTerminalShortcut && (
          <TerminalCheckpointShortcut
            count={terminalActiveCount}
            title={terminalShortcutTitle}
            hiddenDuringMotion={terminalMotionActive}
            onClick={restoreTerminalFromShortcut}
          />
        )}
        <CanvasShortcut
          icon={PanelRight}
          title={canvasVisible && !canvasMinimized ? t('canvas.close') : t('canvas.expand')}
          hidden={canvasVisible && !canvasMinimized}
          onClick={toggleCanvasMenu}
        />
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
        {/* Top: scrollable recent activity + chips */}
        <div
          className="flex-1 overflow-y-auto"
          style={{ background: 'var(--bg-base)' }}
        >
          <div className="flex min-h-full flex-col">
            <div style={{ ...TRACK_STYLE, paddingTop: 24 }}>
              <UsageStatsOverviewTitle />
              <div style={{ marginTop: 32 }}>
                <RecentActivities />
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
      <div className="flex-shrink-0" style={{ background: 'var(--bg-base)' }}>
        <div style={{ maxWidth: 900, width: '80%', margin: '0 auto' }}>
          <TaskProgressCapsule />
        </div>
      </div>
      <SessionRecap />
      <ChatInput cwd={activeCwd} cwdPlacement="below" />
    </div>
  )
}
