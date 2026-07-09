import { useId, useLayoutEffect, useRef, useState } from 'react'
import { animate } from 'animejs'
import { ChevronDown } from 'lucide-react'
import { summarizeRun } from '../../utils/toolRunSummary'
import { RollingText } from '../shared/Odometer'
import AnimatedShimmerText from '@shared/components/shared/AnimatedShimmerText'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import { usePresence } from '@shared/motion/usePresence'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { DUR_MIGRATION, EASE_TAB } from '@shared/motion/tokens'

function findFileOp(fileOps, block) {
  if (!Array.isArray(fileOps) || !block) return null
  return fileOps.find((op) => (
    op.id === block.fileOpId ||
    op.id === block.id ||
    (block.fileOpId && op.toolUseId === block.fileOpId) ||
    (block.id && op.toolUseId === block.id)
  )) || null
}

function hasUnfinishedTool(run, fileOps) {
  if (!Array.isArray(run)) return false
  return run.some((block) => {
    if (!block || (block.type !== 'tool_use' && block.type !== 'file_ref')) return false
    const op = findFileOp(fileOps, block)
    const status = op?.status || block.status
    return status === 'running' || status === 'pending'
  })
}

function SummaryToken({ text, height, fontWeight, shimmer, color }) {
  const style = color ? { color } : undefined
  if (shimmer) {
    return (
      <AnimatedShimmerText
        style={{
          ...style,
          fontSize: height,
          fontWeight,
          lineHeight: `${height}px`,
          verticalAlign: 'middle',
        }}
      >
        {text}
      </AnimatedShimmerText>
    )
  }

  return (
    <span style={style}>
      <RollingText
        text={text}
        height={height}
        color="currentColor"
        fontWeight={fontWeight}
      />
    </span>
  )
}

function SummaryTokens({ summary, fallback, height = 12, fontWeight = 500, shimmer = false }) {
  if (!summary?.tokens?.length) {
    return (
      <SummaryToken
        text={fallback}
        height={height}
        fontWeight={fontWeight}
        shimmer={shimmer}
      />
    )
  }

  return summary.tokens.map((tok, i) => (
    <SummaryToken
      key={i}
      text={tok.text}
      height={height}
      fontWeight={fontWeight}
      color={tok.color}
      shimmer={shimmer}
    />
  ))
}

export function ToolSectionToggle({ collapsed, onToggle, run, fileOps, t, controlsId, compact = false }) {
  const [hovered, setHovered] = useState(false)
  const summary = summarizeRun(run, fileOps, t)
  const hasSummary = summary && summary.tokens.length > 0
  const hasRunningTools = hasUnfinishedTool(run, fileOps)
  const labelColor = hovered ? 'var(--text-primary)' : 'var(--text-secondary)'
  const fallback = t('toolCall.toolStepsFallback', { count: run.length })
  const tokenHeight = compact ? 11 : 12

  return (
    <button
      type="button"
      className="quiet-toggle overflow-hidden"
      style={{
        display: 'flex',
        alignItems: 'center',
        gap: compact ? 5 : 6,
        width: '100%',
        background: 'transparent',
        border: 'none',
        padding: compact ? '1px 0' : 0,
        cursor: 'pointer',
        color: labelColor,
        fontSize: compact ? 12 : 13,
        textAlign: 'left',
        transition: 'color 150ms ease',
      }}
      onClick={onToggle}
      aria-expanded={!collapsed}
      aria-controls={controlsId}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <AnimatedChevron open={!collapsed} style={{ color: 'var(--text-dim)' }}>
        <ChevronDown size={12} strokeWidth={1.5} />
      </AnimatedChevron>
      <span
        style={{
          minWidth: 0,
          color: labelColor,
          wordBreak: 'break-word',
          flex: '1 1 auto',
          display: 'block',
          lineHeight: compact ? '18px' : '20px',
        }}
      >
        {collapsed ? (
          hasSummary
            ? <SummaryTokens summary={summary} fallback={fallback} height={tokenHeight} shimmer={hasRunningTools} />
            : <SummaryTokens fallback={fallback} height={tokenHeight} shimmer={hasRunningTools} />
        ) : (
          <>
            <span>{t('toolCall.hideToolSteps')}</span>
            <span style={{ color: 'var(--text-dim)', margin: '0 6px' }}>·</span>
            {hasSummary
              ? <SummaryTokens summary={summary} fallback={fallback} height={tokenHeight} shimmer={hasRunningTools} />
              : <SummaryTokens fallback={fallback} height={tokenHeight} shimmer={hasRunningTools} />}
          </>
        )}
      </span>
    </button>
  )
}

export default function ToolRunSection({
  collapsed,
  onToggle,
  run,
  fileOps,
  t,
  renderBlock,
  getChildKey,
  compact = false,
}) {
  const bodyId = useId()
  const shouldReduce = useReducedMotion()

  // Compact reveal: quiet opacity + 3px drop, latched through its exit.
  const compactOpen = compact && !collapsed
  const { mounted: compactMounted, onExited: compactExited } = usePresence(compactOpen)
  const compactRef = useRef(null)
  const compactEnteredRef = useRef(compactOpen)

  useLayoutEffect(() => {
    if (!compact) return
    if (!compactMounted) {
      compactEnteredRef.current = false
      return
    }
    const el = compactRef.current
    if (!el) {
      if (!compactOpen) compactExited()
      return
    }
    if (shouldReduce) {
      if (compactOpen) {
        compactEnteredRef.current = true
        el.style.opacity = '1'
        el.style.transform = 'translateY(0px)'
      } else {
        compactExited()
      }
      return
    }
    if (compactOpen) {
      if (!compactEnteredRef.current) {
        // Fresh reveal: pre-paint the from-state.
        el.style.opacity = '0'
        el.style.transform = 'translateY(-3px)'
      }
      compactEnteredRef.current = true
      animate(el, { opacity: 1, translateY: '0px', duration: DUR_MIGRATION.toolReveal, ease: EASE_TAB })
    } else {
      animate(el, {
        opacity: 0,
        translateY: '-3px',
        duration: DUR_MIGRATION.toolReveal,
        ease: EASE_TAB,
        onComplete: compactExited,
      })
    }
  }, [compact, compactOpen, compactMounted, shouldReduce, compactExited])

  const renderToolTree = () => (
    <div className="tool-tree">
      {run.map((toolBlock, runIndex) => (
        <div
          key={getChildKey ? getChildKey(toolBlock, runIndex) : (toolBlock.id || runIndex)}
          className="tool-tree-child"
        >
          {renderBlock(toolBlock, runIndex)}
        </div>
      ))}
    </div>
  )

  return (
    <div style={{ marginTop: compact ? 2 : 0, marginBottom: compact ? 2 : 0 }}>
      <ToolSectionToggle
        collapsed={collapsed}
        onToggle={onToggle}
        run={run}
        fileOps={fileOps}
        t={t}
        controlsId={bodyId}
        compact={compact}
      />

      {compact ? (
        compactMounted ? (
          <div id={bodyId} ref={compactRef} style={{ overflow: 'hidden' }}>
            {renderToolTree()}
          </div>
        ) : null
      ) : (
        <AnimatedCollapse
          open={!collapsed}
          id={bodyId}
          animateHeight={false}
          keepMounted
          deferContentOnClose
        >
          {renderToolTree}
        </AnimatedCollapse>
      )}
    </div>
  )
}
