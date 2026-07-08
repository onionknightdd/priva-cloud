import { useLayoutEffect, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'

const MONO_STACK = "'JetBrains Mono', 'Source Han Mono SC', monospace"

// First row carries the 12px top padding, last row the 12px bottom padding, so
// the virtualized rows reproduce the old <table>'s cell padding exactly.
function cellPadding(i, count, right) {
  const top = i === 0 ? 12 : 0
  const bottom = i === count - 1 ? 12 : 0
  return `${top}px ${right}px ${bottom}px 12px`
}

// Virtualized replacement for the per-line <table> code renderers: identical
// typography, gutter and padding, but only visible lines are mounted.
//
// Two mounting modes:
// - default: owns its scroll container (height:100% of the parent)
// - `scrollRef` provided: renders in normal flow inside that ancestor scroll
//   container and offsets the virtual window by its own top position
//   (the TanStack `scrollMargin` pattern) — used when the code block sits
//   below other content in an already-scrolling pane.
export default function VirtualizedCodeLines({ lines, lineNumberStart = 1, scrollRef = null }) {
  const ownScrollRef = useRef(null)
  const listRef = useRef(null)
  const [scrollMargin, setScrollMargin] = useState(0)
  const external = scrollRef != null

  useLayoutEffect(() => {
    if (!external) return
    const scrollEl = scrollRef.current
    const listEl = listRef.current
    if (!scrollEl || !listEl) return
    const margin = listEl.getBoundingClientRect().top
      - scrollEl.getBoundingClientRect().top
      + scrollEl.scrollTop
    setScrollMargin(margin)
  }, [external, scrollRef, lines])

  const lastLineNumber = lineNumberStart + Math.max(lines.length - 1, 0)
  const gutterWidth = String(lastLineNumber).length * 8 + 16
  const margin = external ? scrollMargin : 0

  const virtualizer = useVirtualizer({
    count: lines.length,
    getScrollElement: () => (external ? scrollRef.current : ownScrollRef.current),
    estimateSize: () => 20, // 12px * 1.6 line-height; wrapped lines re-measure
    overscan: 24,
    scrollMargin: margin,
  })

  const body = (
    <div
      ref={listRef}
      style={{
        height: virtualizer.getTotalSize(),
        position: 'relative',
        width: '100%',
        fontSize: 12,
        lineHeight: 1.6,
        fontFamily: MONO_STACK,
        background: 'var(--bg-elevated)',
      }}
    >
      {virtualizer.getVirtualItems().map((vi) => {
        const i = vi.index
        const line = lines[i]
        const lineNumber = lineNumberStart + i
        const codeStyle = {
          padding: cellPadding(i, lines.length, 16),
          whiteSpace: 'pre-wrap',
          overflowWrap: 'anywhere',
          wordBreak: 'break-word',
          color: 'var(--text-primary)',
        }
        return (
          <div
            key={vi.key}
            data-index={i}
            data-line-number={lineNumber}
            ref={virtualizer.measureElement}
            className="flex"
            style={{
              position: 'absolute',
              top: vi.start - margin,
              left: 0,
              width: '100%',
            }}
          >
            <div
              className="flex-shrink-0"
              style={{
                width: gutterWidth,
                minWidth: gutterWidth,
                padding: cellPadding(i, lines.length, 8),
                textAlign: 'right',
                color: 'var(--text-dim)',
                userSelect: 'none',
                borderRight: '1px solid var(--border)',
                background: 'var(--bg-elevated)',
              }}
            >
              {lineNumber}
            </div>
            {line.html != null ? (
              <div
                className="flex-1 min-w-0"
                style={codeStyle}
                dangerouslySetInnerHTML={{ __html: line.html || '&nbsp;' }}
              />
            ) : (
              <div className="flex-1 min-w-0" style={codeStyle}>
                {line.text || ' '}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )

  if (external) return body

  return (
    <div
      ref={ownScrollRef}
      className="overflow-y-auto overflow-x-hidden"
      style={{ height: '100%', background: 'var(--bg-elevated)', overflowAnchor: 'none' }}
    >
      {body}
    </div>
  )
}
