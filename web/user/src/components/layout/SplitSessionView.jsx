import { useEffect, useMemo, useRef, useState } from 'react'
import { Columns2, Rows2, Grid2X2, PanelLeft, PanelRight } from 'lucide-react'
import useSplitStore from '../../stores/splitStore'
import useSidebarStore from '../../stores/sidebarStore'
import useChatStore from '../../stores/chatStore'
import { applySessionSnapshot, sessionSnapshot, subscribeSessionSnapshot } from '../../utils/sessionSnapshot'
import { stopActiveStream } from '../../hooks/useSSE'

function parseDraggedSession(event) {
  const raw = event.dataTransfer.getData('application/priva-session') || event.dataTransfer.getData('text/plain')
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (parsed?.sessionId) return parsed
  } catch {
    return null
  }
  return null
}

function hasSessionDrag(event) {
  return Array.from(event.dataTransfer?.types || []).includes('application/priva-session')
}

function paneSrc(pane) {
  if (typeof window === 'undefined') return ''
  const params = new URLSearchParams()
  params.set('splitPane', '1')
  params.set('sessionId', pane.sessionId)
  params.set('paneId', pane.id)
  const theme = document.documentElement.dataset.theme
  if (theme) params.set('theme', theme)
  return `${window.location.origin}${window.location.pathname}?${params.toString()}`
}

const MIN_SPLIT_RATIO = 20
const MAX_SPLIT_RATIO = 80
const SESSION_HEADER_HEIGHT = 27
const DEFAULT_DROP_PREVIEW = { placement: 'right', choice: 'two-columns' }

function clampSplitRatio(value) {
  return Math.max(MIN_SPLIT_RATIO, Math.min(MAX_SPLIT_RATIO, value))
}

function getGridStyle(layout, ratios) {
  const columnTemplate = `minmax(0, ${ratios.column}fr) minmax(0, ${100 - ratios.column}fr)`
  const rowTemplate = `minmax(0, ${ratios.row}fr) minmax(0, ${100 - ratios.row}fr)`
  const base = {
    display: 'grid',
    width: '100%',
    height: '100%',
    minWidth: 0,
    minHeight: 0,
    gap: 1,
    background: 'var(--border)',
  }
  if (layout === 'two-rows') {
    return { ...base, gridTemplateColumns: 'minmax(0, 1fr)', gridTemplateRows: rowTemplate }
  }
  if (layout === 'three-left' || layout === 'three-right' || layout === 'four') {
    return { ...base, gridTemplateColumns: columnTemplate, gridTemplateRows: rowTemplate }
  }
  if (layout === 'two-columns') {
    return { ...base, gridTemplateColumns: columnTemplate, gridTemplateRows: 'minmax(0, 1fr)' }
  }
  return { ...base, gridTemplateColumns: 'minmax(0, 1fr)', gridTemplateRows: 'minmax(0, 1fr)' }
}

function getPaneStyle(layout, index) {
  if (layout === 'three-left') {
    if (index === 0) return { gridColumn: '1', gridRow: '1 / 3' }
    if (index === 1) return { gridColumn: '2', gridRow: '1' }
    return { gridColumn: '2', gridRow: '2' }
  }
  if (layout === 'three-right') {
    if (index === 0) return { gridColumn: '1', gridRow: '1' }
    if (index === 1) return { gridColumn: '1', gridRow: '2' }
    return { gridColumn: '2', gridRow: '1 / 3' }
  }
  if (layout === 'four') {
    return {
      gridColumn: index % 2 === 0 ? '1' : '2',
      gridRow: index < 2 ? '1' : '2',
    }
  }
  return {}
}

function LayoutGlyph({ type, size = 14 }) {
  if (type === 'two-columns') return <Columns2 size={size} strokeWidth={1.5} />
  if (type === 'two-rows') return <Rows2 size={size} strokeWidth={1.5} />
  if (type === 'three-left') return <PanelLeft size={size} strokeWidth={1.5} />
  if (type === 'three-right') return <PanelRight size={size} strokeWidth={1.5} />
  return <Grid2X2 size={size} strokeWidth={1.5} />
}

function getPaneSizes(layout, ratios, rootSize, count) {
  const width = rootSize.width || 0
  const height = rootSize.height || 0
  if (!width || !height || count <= 0) return [{ width, height }]
  const leftWidth = width * (ratios.column / 100)
  const rightWidth = width - leftWidth
  const topHeight = height * (ratios.row / 100)
  const bottomHeight = height - topHeight

  if (layout === 'two-columns') return [{ width: leftWidth, height }, { width: rightWidth, height }]
  if (layout === 'two-rows') return [{ width, height: topHeight }, { width, height: bottomHeight }]
  if (layout === 'three-left') {
    return [
      { width: leftWidth, height },
      { width: rightWidth, height: topHeight },
      { width: rightWidth, height: bottomHeight },
    ]
  }
  if (layout === 'three-right') {
    return [
      { width: leftWidth, height: topHeight },
      { width: leftWidth, height: bottomHeight },
      { width: rightWidth, height },
    ]
  }
  if (layout === 'four') {
    return [
      { width: leftWidth, height: topHeight },
      { width: rightWidth, height: topHeight },
      { width: leftWidth, height: bottomHeight },
      { width: rightWidth, height: bottomHeight },
    ]
  }
  return [{ width, height }]
}

function getControlMetrics(layout, ratios, rootSize, count) {
  const paneSizes = getPaneSizes(layout, ratios, rootSize, count)
  const minWidth = Math.min(...paneSizes.map((pane) => pane.width || 0))
  const minHeight = Math.min(...paneSizes.map((pane) => pane.height || 0))
  const compact = minWidth < 360 || minHeight < 260
  const tiny = minWidth < 260 || minHeight < 190
  const buttonSize = tiny ? 20 : compact ? 22 : 24
  const panelPadding = tiny ? 1 : 2
  const gap = tiny ? 0 : 1
  const top = SESSION_HEADER_HEIGHT + (tiny ? 3 : 6)
  return {
    buttonSize,
    iconSize: tiny ? 12 : 14,
    panelPadding,
    gap,
    top,
    left: tiny ? 4 : 6,
    compact,
    tiny,
  }
}

function layoutChoices(count) {
  if (count <= 0) return [{ id: 'single', title: '打开', label: '打开' }]
  if (count <= 1) {
    return [
      { id: 'two-columns', title: '左右双屏', label: '左右' },
      { id: 'two-rows', title: '上下双屏', label: '上下' },
    ]
  }
  if (count === 2) {
    return [
      { id: 'three-left', title: '左1右上下', label: '左1' },
      { id: 'three-right', title: '左上下右1', label: '右1' },
    ]
  }
  if (count === 3) return [{ id: 'four', title: '四屏', label: '四屏' }]
  return [{ id: 'replace-active', title: '替换当前窗格', label: '替换' }]
}

function layoutControlChoices(count) {
  if (count === 2) {
    return [
      { id: 'two-columns', title: '左右双屏' },
      { id: 'two-rows', title: '上下双屏' },
    ]
  }
  if (count === 3) {
    return [
      { id: 'three-left', title: '左1右上下' },
      { id: 'three-right', title: '左上下右1' },
    ]
  }
  if (count >= 4) return [{ id: 'four', title: '四屏' }]
  return []
}

function getDropPreviewFromPoint(event, rect, paneCount) {
  if (!rect?.width || !rect?.height) return DEFAULT_DROP_PREVIEW

  const x = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
  const y = Math.max(0, Math.min(1, (event.clientY - rect.top) / rect.height))
  const horizontalPull = Math.abs(x - 0.5)
  const verticalPull = Math.abs(y - 0.5)

  if (paneCount >= 4) return { placement: 'center', choice: 'replace-active' }

  if (paneCount <= 1) {
    if (verticalPull > horizontalPull) {
      return y < 0.5
        ? { placement: 'top', choice: 'two-rows-top' }
        : { placement: 'bottom', choice: 'two-rows' }
    }
    return x < 0.5
      ? { placement: 'left', choice: 'two-columns-left' }
      : { placement: 'right', choice: 'two-columns' }
  }

  if (paneCount === 2) {
    return x >= 0.5
      ? { placement: 'right', choice: 'three-right' }
      : { placement: 'bottom', choice: 'three-left' }
  }

  return { placement: 'bottom-right', choice: 'four' }
}

function getDropTargetStyle(placement) {
  const base = {
    position: 'absolute',
    boxSizing: 'border-box',
  }

  if (placement === 'left') {
    return { ...base, left: 0, top: 0, width: '50%', height: '100%' }
  }
  if (placement === 'right') {
    return { ...base, right: 0, top: 0, width: '50%', height: '100%' }
  }
  if (placement === 'top') {
    return { ...base, left: 0, top: 0, width: '100%', height: '50%' }
  }
  if (placement === 'bottom') {
    return { ...base, left: 0, bottom: 0, width: '100%', height: '50%' }
  }
  if (placement === 'bottom-right') {
    return { ...base, right: 0, bottom: 0, width: '50%', height: '50%' }
  }
  return { ...base, inset: 0 }
}

function SplitDropOverlay({ paneCount, preview = DEFAULT_DROP_PREVIEW, onChoose, onPreview }) {
  const choices = layoutChoices(paneCount)
  const defaultChoice = preview?.choice || choices[0]?.id || 'single'
  const primaryLabel = paneCount >= 4 ? 'Replace pane' : (paneCount <= 0 ? 'Open in split' : 'Add split')
  const handleDrop = (event, choice = defaultChoice) => {
    event.preventDefault()
    event.stopPropagation()
    onChoose(choice, event)
  }

  return (
    <div
      data-testid="split-drop-overlay"
      className="absolute inset-0"
      style={{
        zIndex: 30,
        boxSizing: 'border-box',
      }}
      onDragOver={(event) => {
        event.preventDefault()
        event.dataTransfer.dropEffect = 'copy'
        onPreview?.(event)
      }}
      onDrop={(event) => handleDrop(event)}
    >
      <div
        className="flex items-center justify-center"
        style={{
          ...getDropTargetStyle(preview?.placement || 'right'),
          background: 'color-mix(in srgb, var(--bg-surface) 58%, transparent)',
          backdropFilter: 'blur(2.7px) saturate(113%)',
          WebkitBackdropFilter: 'blur(2.7px) saturate(113%)',
          border: '2px solid var(--blue)',
          borderRadius: 4,
          color: 'var(--text-primary)',
          overflow: 'hidden',
        }}
        onDragOver={(event) => {
          event.preventDefault()
          event.dataTransfer.dropEffect = 'copy'
          onPreview?.(event)
        }}
        onDrop={(event) => handleDrop(event)}
      >
        <button
          type="button"
          title={primaryLabel}
          className="inline-flex items-center justify-center"
          style={{
            height: 24,
            padding: '0 10px',
            background: 'var(--blue)',
            border: '1px solid var(--blue)',
            borderRadius: 4,
            color: 'var(--text-inverse)',
            cursor: 'copy',
            fontSize: 12,
            fontWeight: 600,
            transition: 'background 150ms ease, border-color 150ms ease',
          }}
          onDragOver={(event) => {
            event.preventDefault()
            event.dataTransfer.dropEffect = 'copy'
          }}
          onDrop={(event) => handleDrop(event)}
          onClick={(event) => event.preventDefault()}
        >
          {primaryLabel}
        </button>
      </div>
    </div>
  )
}

function SplitLayoutSwitcher({ count, layout, onLayout, metrics }) {
  const choices = layoutControlChoices(count)
  if (choices.length <= 1) return null
  return (
    <div
      data-testid="split-layout-switcher"
      className="absolute flex items-center gap-1"
      style={{
        top: metrics.top,
        left: metrics.left,
        zIndex: 6,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: metrics.panelPadding,
        gap: metrics.gap,
      }}
    >
      {choices.map((choice) => {
        const active = choice.id === layout
        return (
          <button
            key={choice.id}
            type="button"
            title={choice.title}
            onClick={() => onLayout(choice.id)}
            className="inline-flex items-center justify-center"
            style={{
              width: metrics.buttonSize,
              height: metrics.buttonSize,
              background: active ? 'var(--bg-elevated)' : 'transparent',
              border: `1px solid ${active ? 'var(--blue)' : 'transparent'}`,
              borderRadius: 4,
              color: active ? 'var(--blue)' : 'var(--text-dim)',
              cursor: 'pointer',
              transition: 'background 150ms ease, color 150ms ease, border-color 150ms ease',
            }}
            onMouseEnter={(event) => {
              event.currentTarget.style.color = active ? 'var(--blue)' : 'var(--text-secondary)'
              if (!active) event.currentTarget.style.background = 'var(--bg-elevated)'
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.color = active ? 'var(--blue)' : 'var(--text-dim)'
              if (!active) event.currentTarget.style.background = 'transparent'
            }}
          >
            <LayoutGlyph type={choice.id} size={metrics.iconSize} />
          </button>
        )
      })}
    </div>
  )
}

function SplitResizeHandles({ layout, ratios, resizingAxis, hoveredAxis, onHover, onStart }) {
  const hasColumnHandle = layout === 'two-columns' || layout === 'three-left' || layout === 'three-right' || layout === 'four'
  const hasRowHandle = layout === 'two-rows' || layout === 'three-left' || layout === 'three-right' || layout === 'four'
  const rowHandleStyle = layout === 'three-left'
    ? { left: `${ratios.column}%`, right: 0 }
    : layout === 'three-right'
      ? { left: 0, right: `${100 - ratios.column}%` }
      : { left: 0, right: 0 }

  const handleBase = {
    position: 'absolute',
    zIndex: 7,
    border: 'none',
    borderRadius: 0,
    padding: 0,
    background: 'transparent',
    transition: 'background 150ms ease',
  }
  const activeBackground = (axis) => (
    resizingAxis === axis || hoveredAxis === axis ? 'var(--blue)' : 'transparent'
  )

  return (
    <>
      {hasColumnHandle && (
        <button
          type="button"
          aria-label="Resize split columns"
          data-testid="split-resize-column"
          onPointerDown={(event) => onStart(event, 'column')}
          onMouseEnter={() => onHover('column')}
          onMouseLeave={() => onHover(null)}
          style={{
            ...handleBase,
            top: 0,
            bottom: 0,
            left: `calc(${ratios.column}% - 3px)`,
            width: 6,
            cursor: 'col-resize',
            background: activeBackground('column'),
          }}
        />
      )}
      {hasRowHandle && (
        <button
          type="button"
          aria-label="Resize split rows"
          data-testid="split-resize-row"
          onPointerDown={(event) => onStart(event, 'row')}
          onMouseEnter={() => onHover('row')}
          onMouseLeave={() => onHover(null)}
          style={{
            ...handleBase,
            ...rowHandleStyle,
            top: `calc(${ratios.row}% - 3px)`,
            height: 6,
            cursor: 'row-resize',
            background: activeBackground('row'),
          }}
        />
      )}
    </>
  )
}

function EmptySessionView() {
  return (
    <div
      className="flex flex-1 items-center justify-center"
      style={{
        minWidth: 0,
        minHeight: 0,
        background: 'var(--bg-base)',
        color: 'var(--text-dim)',
        fontSize: 12,
      }}
    />
  )
}

function LocalPaneMirrorBridge({ paneId, sessionId }) {
  useEffect(() => {
    if (!sessionId || typeof BroadcastChannel === 'undefined') return undefined
    const channel = new BroadcastChannel(`priva-session:${sessionId}`)
    let applyingRemote = false
    let timer = null
    const publish = () => {
      if (applyingRemote) return
      if (timer) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        timer = null
        channel.postMessage({ type: 'state', paneId, state: sessionSnapshot() })
      }, 80)
    }
    const unsubscribers = subscribeSessionSnapshot(publish)
    channel.onmessage = (event) => {
      if (!event.data || event.data.paneId === paneId) return
      if (event.data.type === 'ready') {
        publish()
        return
      }
      if (event.data.type === 'stop-request') {
        stopActiveStream({ broadcast: false })
        publish()
        return
      }
      if (event.data.type === 'focus') return
      if (event.data.type !== 'state' || !event.data.state) return
      applyingRemote = true
      applySessionSnapshot(event.data.state)
      window.setTimeout(() => { applyingRemote = false }, 0)
    }
    channel.postMessage({ type: 'ready', paneId })
    publish()
    return () => {
      if (timer) window.clearTimeout(timer)
      unsubscribers.forEach((unsubscribe) => unsubscribe())
      channel.close()
    }
  }, [paneId, sessionId])
  return null
}

export default function SplitSessionView({ fallback }) {
  const panes = useSplitStore((s) => s.panes)
  const layout = useSplitStore((s) => s.layout)
  const activePaneId = useSplitStore((s) => s.activePaneId)
  const setActivePane = useSplitStore((s) => s.setActivePane)
  const setLayout = useSplitStore((s) => s.setLayout)
  const addSessionWithLayout = useSplitStore((s) => s.addSessionWithLayout)
  const replacePaneSession = useSplitStore((s) => s.replacePaneSession)
  const closePane = useSplitStore((s) => s.closePane)
  const draggingSession = useSplitStore((s) => s.draggingSession)
  const endSessionDrag = useSplitStore((s) => s.endSessionDrag)
  const currentSessionId = useChatStore((s) => s.sessionId)
  const sessions = useSidebarStore((s) => s.sessions)
  const splitRootRef = useRef(null)
  const [dragOverActive, setDragOverActive] = useState(false)
  const [splitRatios, setSplitRatios] = useState({ column: 50, row: 50 })
  const [splitSize, setSplitSize] = useState({ width: 0, height: 0 })
  const [resizeDrag, setResizeDrag] = useState(null)
  const [hoveredResizeAxis, setHoveredResizeAxis] = useState(null)
  const [dropPreview, setDropPreview] = useState(DEFAULT_DROP_PREVIEW)

  const names = useMemo(() => {
    const map = new Map()
    for (const session of sessions) {
      map.set(session.id, session.name)
      if (session.sessionId) map.set(session.sessionId, session.name)
    }
    return map
  }, [sessions])

  useEffect(() => {
    const handler = (event) => {
      if (event.origin !== window.location.origin) return
      if (event.data?.type === 'priva:split-pane-focus') {
        setActivePane(event.data.paneId)
        return
      }
      if (event.data?.type === 'priva:split-pane-close') {
        closePane(event.data.paneId)
      }
    }
    window.addEventListener('message', handler)
    return () => window.removeEventListener('message', handler)
  }, [closePane, setActivePane])

  useEffect(() => {
    const node = splitRootRef.current
    if (!node || typeof ResizeObserver === 'undefined') return undefined
    const update = () => {
      const rect = node.getBoundingClientRect()
      setSplitSize({ width: rect.width, height: rect.height })
    }
    update()
    const observer = new ResizeObserver(update)
    observer.observe(node)
    return () => observer.disconnect()
  }, [panes.length])

  const controlMetrics = useMemo(
    () => getControlMetrics(layout, splitRatios, splitSize, panes.length),
    [layout, panes.length, splitRatios, splitSize],
  )

  const handleResizeStart = (event, axis) => {
    event.preventDefault()
    event.stopPropagation()
    const previousCursor = document.body.style.cursor
    const previousUserSelect = document.body.style.userSelect
    document.body.style.cursor = axis === 'column' ? 'col-resize' : 'row-resize'
    document.body.style.userSelect = 'none'
    setResizeDrag({ axis })

    const handlePointerMove = (moveEvent) => {
      const rect = splitRootRef.current?.getBoundingClientRect()
      if (!rect) return
      moveEvent.preventDefault()
      if (axis === 'column') {
        const next = ((moveEvent.clientX - rect.left) / rect.width) * 100
        setSplitRatios((s) => ({ ...s, column: clampSplitRatio(next) }))
      } else {
        const next = ((moveEvent.clientY - rect.top) / rect.height) * 100
        setSplitRatios((s) => ({ ...s, row: clampSplitRatio(next) }))
      }
    }
    const stopResize = () => {
      document.body.style.cursor = previousCursor
      document.body.style.userSelect = previousUserSelect
      setResizeDrag(null)
      window.removeEventListener('pointermove', handlePointerMove)
      window.removeEventListener('pointerup', stopResize)
      window.removeEventListener('pointercancel', stopResize)
    }

    window.addEventListener('pointermove', handlePointerMove, { passive: false })
    window.addEventListener('pointerup', stopResize)
    window.addEventListener('pointercancel', stopResize)
  }

  const handleDropChoice = (choice, event) => {
    const session = parseDraggedSession(event) || draggingSession
    if (!session?.sessionId) return
    if (choice === 'replace-active' && activePaneId) {
      replacePaneSession(activePaneId, session.sessionId)
    } else {
      addSessionWithLayout(session.sessionId, choice, currentSessionId)
    }
    setDragOverActive(false)
    setDropPreview(DEFAULT_DROP_PREVIEW)
    endSessionDrag()
  }

  const updateDropPreview = (event) => {
    const rect = splitRootRef.current?.getBoundingClientRect() || event.currentTarget.getBoundingClientRect()
    setDropPreview(getDropPreviewFromPoint(event, rect, Math.max(1, panes.length)))
  }

  const handleDragEnter = (event) => {
    if (!hasSessionDrag(event)) return
    event.preventDefault()
    updateDropPreview(event)
    setDragOverActive(true)
  }

  const handleDragOver = (event) => {
    if (!hasSessionDrag(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
    updateDropPreview(event)
  }

  const handleDragLeave = (event) => {
    if (!event.currentTarget.contains(event.relatedTarget)) {
      event.preventDefault()
      setDragOverActive(false)
      setDropPreview(DEFAULT_DROP_PREVIEW)
    }
  }

  if (panes.length === 0) {
    return (
      <div
        ref={splitRootRef}
        className="relative flex flex-1 min-w-0 min-h-0"
        onDragEnter={handleDragEnter}
        onDragOver={handleDragOver}
        onDragLeave={handleDragLeave}
      >
        {fallback || <EmptySessionView />}
        {!!(draggingSession || dragOverActive) && (
          <SplitDropOverlay
            paneCount={1}
            preview={dropPreview}
            onPreview={updateDropPreview}
            onChoose={handleDropChoice}
          />
        )}
      </div>
    )
  }

  return (
    <div
      ref={splitRootRef}
      className="relative flex-1 min-w-0 min-h-0"
      style={{ background: 'var(--bg-base)' }}
      onDragEnter={handleDragEnter}
      onDragOver={handleDragOver}
      onDragLeave={handleDragLeave}
    >
      <div style={getGridStyle(layout, splitRatios)}>
        {panes.map((pane, index) => {
          const active = panes.length > 1 && pane.id === activePaneId
          const paneTitle = pane.sessionId ? (names.get(pane.sessionId) || pane.sessionId) : 'Session view'
          return (
            <div
              key={pane.id}
              data-testid="split-pane"
              className="relative overflow-hidden"
              style={{
                ...getPaneStyle(layout, index),
                minWidth: 0,
                minHeight: 0,
                background: 'var(--bg-base)',
                outline: active ? '2px solid var(--blue)' : 'none',
                outlineOffset: -2,
              }}
              onMouseDownCapture={() => setActivePane(pane.id)}
              title={paneTitle}
            >
              {pane.local ? (
                <>
                  <LocalPaneMirrorBridge paneId={pane.id} sessionId={pane.sessionId} />
                  <div className="flex min-w-0 min-h-0" style={{ width: '100%', height: '100%' }}>
                    {fallback || <EmptySessionView />}
                  </div>
                </>
              ) : (
                <iframe
                  title={paneTitle}
                  src={paneSrc(pane)}
                  style={{
                    width: '100%',
                    height: '100%',
                    border: 'none',
                    display: 'block',
                    background: 'var(--bg-base)',
                    pointerEvents: draggingSession || dragOverActive || resizeDrag ? 'none' : 'auto',
                  }}
                />
              )}
            </div>
          )
        })}
      </div>
      <SplitResizeHandles
        layout={layout}
        ratios={splitRatios}
        resizingAxis={resizeDrag?.axis || null}
        hoveredAxis={hoveredResizeAxis}
        onHover={setHoveredResizeAxis}
        onStart={handleResizeStart}
      />
      <SplitLayoutSwitcher count={panes.length} layout={layout} onLayout={setLayout} metrics={controlMetrics} />
      {!!(draggingSession || dragOverActive) && (
        <SplitDropOverlay
          paneCount={panes.length}
          preview={dropPreview}
          onPreview={updateDropPreview}
          onChoose={handleDropChoice}
        />
      )}
    </div>
  )
}
