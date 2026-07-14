import { memo, useRef } from 'react'
import { Bot, Loader, AlertTriangle, Clock } from 'lucide-react'
import DrawIcon from '@shared/components/shared/DrawIcon'
import { useStatusSettle } from '@shared/motion/useStatusSettle'
import useChatStore from '../../stores/chatStore'
import useTaskStore from '../../stores/taskStore'
import useUiStore from '@shared/stores/uiStore'
import { RollingInteger } from '../shared/Odometer'
import AnimatedShimmerText from '@shared/components/shared/AnimatedShimmerText'

function formatDuration(ms) {
  if (!ms) return null
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.round(s % 60)
  return `${m}m ${rs}s`
}

function ToolUseCountLabel({ count }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 2 }}>
      <RollingInteger value={count} height={12} color="currentColor" />
      <span>{count === 1 ? 'tool use' : 'tool uses'}</span>
    </span>
  )
}

/**
 * SubagentFrame — renders an `Agent` / `Task` tool_use block as a compact
 * summary in the message timeline. The full subagent transcript lives in the
 * Canvas Inspector; clicking the description focuses that inspector node.
 *
 * Reads children from `chatStore.subagentContent[block.id]` — populated by
 * useSSE.js (streaming) and sessionTransform.js (replay) — for summary counts.
 */
const EMPTY_CONTENT = []

function SubagentFrame({ block, reverted = false }) {
  const status = block.status || 'running'
  const isError = status === 'error' || block.result?.is_error
  const isComplete = status === 'success' || status === 'error'
  // Draw the Done check only when the frame resolves LIVE — frames that mount
  // already-complete (history, scroll-back remounts) render it static.
  const mountedIncompleteRef = useRef(!isComplete)
  // M9: one-shot 0.98→1 settle on the status chip when the status flips live.
  const chipSettleRef = useStatusSettle(status)

  const subagentContent = useChatStore((s) => s.subagentContent[block.id]) || EMPTY_CONTENT
  const activeSubagentId = useTaskStore((s) => s.activeSubagentId)
  const focusSubagent = useTaskStore((s) => s.focusSubagent)
  const showCanvas = useUiStore((s) => s.showCanvas)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)
  const isActive = activeSubagentId === block.id

  const agentType = block.input?.subagent_type || (block.name === 'Task' ? 'Agent' : block.name)
  const agentTitle = `Sub Agent: ${agentType}`
  const description = block.input?.description || block.input?.prompt || ''

  // Derive stats from the streamed child content.
  const toolUseCount = subagentContent.filter((b) => b.type === 'tool_use').length
  const latestToolUse = [...subagentContent].reverse().find((b) => b.type === 'tool_use')

  const durationStr = block.duration ? formatDuration(block.duration) : null

  const borderColor = isError ? 'var(--red)' : 'var(--purple)'
  const bgTint = isActive
    ? 'var(--bg-elevated)'
    : isError
      ? 'rgba(248, 81, 73, 0.06)'
      : 'rgba(188, 140, 255, 0.04)'

  const handleCardClick = (event) => {
    event.stopPropagation()
    if (block.id) {
      focusSubagent(block.id)
      showCanvas()
      setActiveCanvasTab('tasks')
    }
  }

  // Pick a status chip.
  let statusNode = null
  if (status === 'running') {
    statusNode = (
      <span className="chip" style={{
        color: 'var(--purple)',
        background: 'rgba(188, 140, 255, 0.1)',
        borderColor: 'rgba(188, 140, 255, 0.3)',
        opacity: 1,
        gap: 3,
      }}>
        <Loader size={10} strokeWidth={1.5} className="icon-running" style={{ marginRight: 2 }} />
        <ToolUseCountLabel count={toolUseCount} />
        {latestToolUse ? (
          <AnimatedShimmerText style={{ fontSize: 11 }}>· {latestToolUse.name}...</AnimatedShimmerText>
        ) : (
          <AnimatedShimmerText style={{ fontSize: 11 }}>· running</AnimatedShimmerText>
        )}
      </span>
    )
  } else if (isError) {
    statusNode = (
      <span ref={chipSettleRef} className="chip" style={{
        color: 'var(--text-inverse)',
        background: 'var(--red)',
        borderColor: 'var(--red)',
        fontWeight: 600,
        letterSpacing: '0.06em',
        opacity: 1,
      }}>
        <AlertTriangle size={10} strokeWidth={1.5} style={{ marginRight: 2 }} /> ERROR
      </span>
    )
  } else {
    statusNode = (
      <span ref={chipSettleRef} className="chip" style={{
        color: 'var(--green)',
        background: 'rgba(63, 185, 80, 0.15)',
        borderColor: 'rgba(63, 185, 80, 0.4)',
        opacity: 1,
        gap: 3,
      }}>
        <DrawIcon name="check" size={10} strokeWidth={1.5} draw={mountedIncompleteRef.current} style={{ marginRight: 2 }} />
        <span>Done</span>
        {toolUseCount > 0 && (
          <>
            <span>·</span>
            <ToolUseCountLabel count={toolUseCount} />
          </>
        )}
      </span>
    )
  }

  return (
    <div
      className="overflow-hidden"
      data-subagent-frame
      data-tool-use-id={block.id}
      style={{
        borderRadius: 0,
        opacity: reverted ? 0.55 : 1,
        filter: reverted ? 'grayscale(0.4)' : 'none',
      }}
    >
      {/* Header row */}
      <button
        type="button"
        className="flex items-center gap-2 w-full px-3 py-2 text-sm overflow-hidden"
        style={{
          background: bgTint,
          border: '1px solid var(--border)',
          borderLeft: `3px solid ${borderColor}`,
          borderRadius: 0,
          cursor: 'pointer',
          textAlign: 'left',
          color: 'var(--text-primary)',
          transition: 'background 150ms ease',
        }}
        onClick={handleCardClick}
        onMouseEnter={(event) => { if (!isActive) event.currentTarget.style.background = 'var(--bg-elevated)' }}
        onMouseLeave={(event) => { if (!isActive) event.currentTarget.style.background = bgTint }}
      >
        <Bot size={14} strokeWidth={1.5} style={{ color: borderColor, flexShrink: 0 }} />
        <span
          className="font-semibold"
          style={{
            color: 'var(--text-primary)',
            display: 'inline-flex',
            alignItems: 'center',
            gap: 6,
            minWidth: 0,
            flex: '0 1 auto',
            overflow: 'hidden',
            whiteSpace: 'nowrap',
          }}
        >
          <span className="truncate" style={{ flexShrink: 0 }}>{agentTitle}</span>
          {description && (
            <>
              <span className="font-normal" style={{ color: 'var(--text-secondary)', flexShrink: 0 }}>·</span>
              <span
                className="font-normal"
                style={{
                  color: 'var(--text-secondary)',
                  fontWeight: 400,
                  minWidth: 0,
                  overflow: 'hidden',
                  textOverflow: 'ellipsis',
                  whiteSpace: 'nowrap',
                }}
              >
                {description}
              </span>
            </>
          )}
        </span>
        <span className="flex-1" />
        {durationStr && (
          <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
            <Clock size={10} strokeWidth={1.5} />
            {durationStr}
          </span>
        )}
        {statusNode}
      </button>
    </div>
  )
}

export default memo(SubagentFrame)
