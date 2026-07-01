import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import useTaskStore from '../../stores/taskStore'
import useChatStore from '../../stores/chatStore'
import useSidebarStore from '../../stores/sidebarStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import { useResizable } from '@shared/hooks/useResizable'
import ErrorBoundary from '../shared/ErrorBoundary'
import CanvasHeader from '../canvas/CanvasHeader'
import SubagentInspector from '../canvas/SubagentInspector'
import WorkflowTree from '../canvas/WorkflowTree'
import FileOpsPanel from '../canvas/FileOpsPanel'
import FileBrowserPanel from '../canvas/FileBrowserPanel'
import PlanReviewPanel from '../canvas/PlanReviewPanel'
import BrowserDebugPanel from '../canvas/BrowserDebugPanel'
import { isSplitPane } from '../../utils/splitMode'

const CANVAS_MIN_WIDTH = 280
const EMBEDDED_CANVAS_MIN_WIDTH = 160
const MIN_CHAT_WIDTH = 360
const EMBEDDED_MIN_CHAT_WIDTH = 220

// Combined budget: sidebar + canvas may never squeeze the chat column below
// MIN_CHAT_WIDTH. Also keeps the historical 60vw ceiling.
function getCanvasMax(sidebarWidth, collapsed, embedded = false, minWidth = CANVAS_MIN_WIDTH) {
  if (typeof window === 'undefined') return minWidth
  if (embedded) {
    const paneWidth = window.innerWidth
    const reservedChat = Math.min(MIN_CHAT_WIDTH, Math.max(EMBEDDED_MIN_CHAT_WIDTH, paneWidth * 0.5))
    return Math.max(
      minWidth,
      Math.min(paneWidth * 0.45, paneWidth - reservedChat),
    )
  }
  const sidebar = collapsed ? 48 : sidebarWidth
  return Math.max(
    minWidth,
    Math.min(window.innerWidth * 0.6, window.innerWidth - sidebar - MIN_CHAT_WIDTH),
  )
}

export default function CanvasPanel() {
  const { t } = useTranslation()
  const embeddedPane = isSplitPane()
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const canvasWidth = useUiStore((s) => s.canvasWidth)
  const canvasMinimized = useUiStore((s) => s.canvasMinimized)
  const setCanvasWidth = useUiStore((s) => s.setCanvasWidth)
  const setCanvasMinimized = useUiStore((s) => s.setCanvasMinimized)
  const activeCanvasTab = useUiStore((s) => s.activeCanvasTab)
  const todos = useTaskStore((s) => s.todos)
  const subagentContent = useChatStore((s) => s.subagentContent)
  const sidebarWidth = useSidebarStore((s) => s.width)
  const sidebarCollapsed = useSidebarStore((s) => s.collapsed)
  const changeOpsCount = useFileOpsStore((s) => s.fileOps.filter((op) => op.type === 'write' || op.type === 'edit').length)
  const fileBrowserCount = useFileBrowserStore((s) => s.tabs.length)

  const hasRunning = Object.values(subagentContent || {}).some((blocks) =>
    blocks.some((b) => b.type === 'tool_use' && (b.status === 'running' || !b.status))
  )
  const todoTotal = todos ? todos.length : 0
  const todoCompleted = todos ? todos.filter((t) => t.status === 'completed').length : 0

  const canvasMin = embeddedPane ? EMBEDDED_CANVAS_MIN_WIDTH : CANVAS_MIN_WIDTH
  const [canvasMax, setCanvasMax] = useState(() => getCanvasMax(sidebarWidth, sidebarCollapsed, embeddedPane, canvasMin))

  // Re-budget on window resize and on sidebar width/collapse changes.
  useEffect(() => {
    const update = () => {
      const sb = useSidebarStore.getState()
      setCanvasMax(getCanvasMax(sb.width, sb.collapsed, embeddedPane, canvasMin))
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [canvasMin, embeddedPane, sidebarWidth, sidebarCollapsed])

  // Clamp DOWNWARD only — never grow the canvas automatically, which would
  // feed back into another resize.
  useEffect(() => {
    if (canvasWidth > canvasMax) setCanvasWidth(canvasMax)
  }, [canvasMax, canvasWidth, setCanvasWidth])

  const effectiveCanvasWidth = Math.min(canvasWidth, canvasMax)

  const { dragging, onMouseDown } = useResizable({
    initial: effectiveCanvasWidth,
    min: canvasMin,
    max: canvasMax,
    direction: 'left',
    onResize: setCanvasWidth,
  })

  if (!canvasVisible) return null

  // Minimized rail
  if (canvasMinimized) {
    const rail = activeCanvasTab === 'plan'
      ? { label: t('canvas.rail.plan'), count: null }
      : activeCanvasTab === 'file-browser'
        ? { label: t('canvas.rail.files'), count: fileBrowserCount || null }
        : activeCanvasTab === 'changes' || activeCanvasTab === 'files'
          ? { label: t('canvas.rail.changes'), count: changeOpsCount || null }
          : activeCanvasTab === 'browser'
            ? { label: t('canvas.rail.browser'), count: null }
            : { label: t('canvas.rail.tasks'), count: todoTotal ? `${todoCompleted}/${todoTotal}` : null }
    return (
      <div
        className="flex flex-col items-center justify-center flex-shrink-0"
        role="button"
        tabIndex={0}
        aria-label={t('canvas.expand')}
        style={{
          width: 40,
          height: '100%',
          minHeight: 0,
          background: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border)',
          cursor: 'pointer',
          animation: hasRunning ? 'pulse-border 1.5s ease infinite' : 'none',
          borderLeftColor: hasRunning ? 'var(--purple)' : undefined,
        }}
        onClick={() => setCanvasMinimized(false)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            setCanvasMinimized(false)
          }
        }}
        title={`${t('canvas.expand')} · ${rail.label}`}
      >
        <span
          className="text-xs font-semibold"
          style={{
            color: 'var(--text-secondary)',
            writingMode: 'vertical-rl',
            textOrientation: 'mixed',
            letterSpacing: 0,
          }}
        >
          {rail.label}
        </span>
        {rail.count && (
          <span
            style={{
              marginTop: 6,
              minWidth: 16,
              height: 16,
              padding: '0 3px',
              border: '1px solid var(--border)',
              borderRadius: 4,
              color: 'var(--text-secondary)',
              fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
              fontSize: 10,
              fontWeight: 600,
              lineHeight: '14px',
              textAlign: 'center',
              boxSizing: 'border-box',
            }}
          >
            {rail.count}
          </span>
        )}
      </div>
    )
  }

  return (
    <div
      className="flex flex-col flex-shrink-0 relative"
      style={{
        width: effectiveCanvasWidth,
        height: '100%',
        minHeight: 0,
        background: 'var(--bg-surface)',
        borderLeft: '1px solid var(--border)',
        transition: dragging ? 'none' : 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
        overflow: 'hidden',
      }}
    >
      <div
        onMouseDown={onMouseDown}
        style={{
          position: 'absolute',
          left: 0,
          top: 0,
          bottom: 0,
          width: 4,
          cursor: 'col-resize',
          background: dragging ? 'var(--blue)' : 'transparent',
          transition: 'background 100ms ease',
          zIndex: 10,
        }}
        onMouseEnter={(e) => { if (!dragging) e.currentTarget.style.background = 'var(--blue)' }}
        onMouseLeave={(e) => { if (!dragging) e.currentTarget.style.background = 'transparent' }}
      />

      <CanvasHeader />

      <ErrorBoundary resetKey={activeCanvasTab} compact>
        {activeCanvasTab === 'file-browser' ? (
          <FileBrowserPanel />
        ) : activeCanvasTab === 'changes' || activeCanvasTab === 'files' ? (
          <FileOpsPanel />
        ) : activeCanvasTab === 'plan' ? (
          <PlanReviewPanel />
        ) : activeCanvasTab === 'browser' ? (
          <BrowserDebugPanel />
        ) : (
          // Default inspector view: live workflows + execution artifacts grouped
          // by conversation turn. Both render null when empty (zero layout cost).
          <div className="flex-1 overflow-y-auto overflow-x-hidden">
            <WorkflowTree />
            <SubagentInspector />
          </div>
        )}
      </ErrorBoundary>
    </div>
  )
}
