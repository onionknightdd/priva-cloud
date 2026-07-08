import { useEffect, useState } from 'react'
import useUiStore from '@shared/stores/uiStore'
import useSidebarStore from '../../stores/sidebarStore'
import { useResizable } from '@shared/hooks/useResizable'
import useCollapseWidth from '@shared/motion/useCollapseWidth'
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
const CANVAS_MAX_PANE_RATIO = 2 / 3

function getFallbackPaneWidth(sidebarWidth, collapsed, embedded = false) {
  if (typeof window === 'undefined') return 0
  if (embedded) return window.innerWidth
  const sidebar = collapsed ? 48 : sidebarWidth
  return Math.max(0, window.innerWidth - sidebar)
}

function getCanvasMax(paneWidth) {
  return Math.max(1, Math.floor((paneWidth || 0) * CANVAS_MAX_PANE_RATIO))
}

export default function CanvasPanel() {
  const embeddedPane = isSplitPane()
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const canvasWidth = useUiStore((s) => s.canvasWidth)
  const canvasMinimized = useUiStore((s) => s.canvasMinimized)
  const setCanvasWidth = useUiStore((s) => s.setCanvasWidth)
  const activeCanvasTab = useUiStore((s) => s.activeCanvasTab)
  const sidebarWidth = useSidebarStore((s) => s.width)
  const sidebarCollapsed = useSidebarStore((s) => s.collapsed)

  const canvasBaseMin = embeddedPane ? EMBEDDED_CANVAS_MIN_WIDTH : CANVAS_MIN_WIDTH
  const [canvasMax, setCanvasMax] = useState(() => (
    getCanvasMax(getFallbackPaneWidth(sidebarWidth, sidebarCollapsed, embeddedPane))
  ))
  const canvasMin = Math.min(canvasBaseMin, canvasMax)

  // Re-budget against the real pane width; in split mode this is the iframe
  // viewport, and in single-pane mode it is the non-sidebar content area.
  useEffect(() => {
    const update = () => {
      const parentWidth = rootRef.current?.parentElement?.getBoundingClientRect().width
      const sb = useSidebarStore.getState()
      const paneWidth = parentWidth || getFallbackPaneWidth(sb.width, sb.collapsed, embeddedPane)
      setCanvasMax(getCanvasMax(paneWidth))
    }
    update()
    const parent = rootRef.current?.parentElement
    const observer = parent && typeof ResizeObserver !== 'undefined' ? new ResizeObserver(update) : null
    if (parent && observer) observer.observe(parent)
    window.addEventListener('resize', update)
    return () => {
      observer?.disconnect()
      window.removeEventListener('resize', update)
    }
  }, [canvasMinimized, canvasVisible, embeddedPane, sidebarWidth, sidebarCollapsed])

  useEffect(() => {
    if (canvasWidth > canvasMax) setCanvasWidth(canvasMax)
    if (canvasWidth < canvasMin) setCanvasWidth(canvasMin)
  }, [canvasMax, canvasMin, canvasWidth, setCanvasWidth])

  const effectiveCanvasWidth = Math.max(canvasMin, Math.min(canvasWidth, canvasMax))

  const { dragging, onMouseDown } = useResizable({
    initial: effectiveCanvasWidth,
    min: canvasMin,
    max: canvasMax,
    direction: 'left',
    onResize: setCanvasWidth,
  })

  // Show/hide: width animates 0 ↔ effective width (layout push both ways),
  // replacing the old hard mount/unmount + CSS width transition (which would
  // double-smooth the animator's per-frame writes).
  const open = canvasVisible && !canvasMinimized
  const { mounted, rootRef } = useCollapseWidth({ open, width: effectiveCanvasWidth })

  if (!mounted) return null

  return (
    <div
      ref={rootRef}
      className="flex-shrink-0 relative"
      style={{
        width: effectiveCanvasWidth,
        maxWidth: `${CANVAS_MAX_PANE_RATIO * 100}%`,
        height: '100%',
        minHeight: 0,
        background: 'var(--bg-surface)',
        borderLeft: '1px solid var(--border)',
        overflow: 'hidden',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      {/* Fixed-width inner shell so content never reflows while the pane
          width animates — it is revealed, not squished. */}
      <div className="flex flex-col" style={{ width: effectiveCanvasWidth, height: '100%', minHeight: 0 }}>
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
    </div>
  )
}
