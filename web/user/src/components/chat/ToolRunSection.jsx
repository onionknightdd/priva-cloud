import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { animate } from 'animejs'
import { ChevronDown } from 'lucide-react'
import AnimatedShimmerText from '@shared/components/shared/AnimatedShimmerText'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import { usePresence } from '@shared/motion/usePresence'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { EASE_SPRING } from '@shared/motion/tokens'
import { formatDuration, getRunMetrics } from '../../utils/toolPresentation'

const TOOL_SWAP_DURATION = 180

function AnimatedLineSwap({ itemKey, children, animateOnMount = false, block = false, className = '', style }) {
  const reduceMotion = useReducedMotion()
  const previousKeyRef = useRef(itemKey)
  const previousNodeRef = useRef(children)
  const generationRef = useRef(0)
  const elementRefs = useRef(new Map())
  const animationsRef = useRef([])
  const initialEnteredRef = useRef(false)
  const [entries, setEntries] = useState(() => ([{
    instanceKey: String(itemKey),
    itemKey,
    node: children,
    phase: 'current',
    generation: 0,
  }]))

  useLayoutEffect(() => {
    const previousKey = previousKeyRef.current
    const previousNode = previousNodeRef.current
    previousNodeRef.current = children
    if (previousKey === itemKey) return

    previousKeyRef.current = itemKey
    const generation = generationRef.current + 1
    generationRef.current = generation
    setEntries([
      {
        instanceKey: String(previousKey),
        itemKey: previousKey,
        node: previousNode,
        phase: 'out',
        generation,
      },
      {
        instanceKey: String(itemKey),
        itemKey,
        node: children,
        phase: 'in',
        generation,
      },
    ])
  }, [children, itemKey])

  useLayoutEffect(() => {
    animationsRef.current.forEach((motion) => motion.cancel())
    animationsRef.current = []

    if (entries.length === 1) {
      const entry = entries[0]
      const element = elementRefs.current.get(entry.instanceKey)
      if (!element || initialEnteredRef.current || !animateOnMount) {
        initialEnteredRef.current = true
        return undefined
      }
      initialEnteredRef.current = true
      if (reduceMotion) return undefined
      element.style.opacity = '0'
      element.style.transform = 'translateY(8px)'
      const motion = animate(element, {
        opacity: 1,
        translateY: '0px',
        duration: TOOL_SWAP_DURATION,
        ease: EASE_SPRING,
        onComplete: () => {
          element.style.opacity = ''
          element.style.transform = ''
        },
      })
      animationsRef.current = [motion]
      return () => motion.cancel()
    }

    const outgoing = entries.find((entry) => entry.phase === 'out')
    const incoming = entries.find((entry) => entry.phase === 'in')
    const outgoingElement = outgoing ? elementRefs.current.get(outgoing.instanceKey) : null
    const incomingElement = incoming ? elementRefs.current.get(incoming.instanceKey) : null
    if (!incoming || !incomingElement) return undefined

    const finish = () => {
      if (generationRef.current !== incoming.generation) return
      incomingElement.style.opacity = ''
      incomingElement.style.transform = ''
      setEntries([{
        ...incoming,
        instanceKey: String(incoming.itemKey),
        phase: 'current',
      }])
    }

    if (reduceMotion) {
      finish()
      return undefined
    }

    incomingElement.style.opacity = '0'
    incomingElement.style.transform = 'translateY(8px)'
    const motions = []
    if (outgoingElement) {
      motions.push(animate(outgoingElement, {
        opacity: 0,
        translateY: '-8px',
        duration: TOOL_SWAP_DURATION,
        ease: EASE_SPRING,
      }))
    }
    motions.push(animate(incomingElement, {
      opacity: 1,
      translateY: '0px',
      duration: TOOL_SWAP_DURATION,
      ease: EASE_SPRING,
      onComplete: finish,
    }))
    animationsRef.current = motions
    return () => motions.forEach((motion) => motion.cancel())
  }, [animateOnMount, entries, reduceMotion])

  useEffect(() => () => {
    animationsRef.current.forEach((motion) => motion.cancel())
  }, [])

  const Root = block ? 'div' : 'span'
  const Item = block ? 'div' : 'span'

  return (
    <Root className={`tool-line-swap ${className}`.trim()} style={style}>
      {entries.map((entry) => (
        <Item
          key={entry.instanceKey}
          ref={(element) => {
            if (element) elementRefs.current.set(entry.instanceKey, element)
            else elementRefs.current.delete(entry.instanceKey)
          }}
          className="tool-line-swap-item"
        >
          {entry.itemKey === itemKey ? children : entry.node}
        </Item>
      ))}
    </Root>
  )
}

function GroupSummary({ live, run, fileOps, t, compact }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    if (!live) return undefined
    const update = () => setNow(Date.now())
    const timer = window.setInterval(update, 100)
    return () => window.clearInterval(timer)
  }, [live])

  const metrics = getRunMetrics(run, fileOps, now, live)
  const label = live
    ? t('toolCall.group.running', {
        count: metrics.count,
        defaultValue: `Running ${metrics.count} ${metrics.count === 1 ? 'tool' : 'tools'}`,
      })
    : t('toolCall.group.used', {
        count: metrics.count,
        defaultValue: `Used ${metrics.count} ${metrics.count === 1 ? 'tool' : 'tools'}`,
      })
  const failed = metrics.failed > 0
    ? t('toolCall.group.failed', {
        count: metrics.failed,
        defaultValue: `${metrics.failed} failed`,
      })
    : ''
  const duration = formatDuration(metrics.duration)
  const fontSize = compact ? 'var(--text-sm)' : 'var(--text-base)'

  return (
    <AnimatedLineSwap itemKey={live ? 'running' : 'complete'}>
      <span className="tool-run-summary-content" style={{ fontSize }}>
        {live ? (
          <AnimatedShimmerText
            style={{
              fontSize,
              fontWeight: 400,
              lineHeight: '20px',
              verticalAlign: 'top',
            }}
          >
            {label}
          </AnimatedShimmerText>
        ) : (
          <span>{label}</span>
        )}
        {duration && <span className="tool-run-summary-meta"> · {duration}</span>}
        {failed && <span style={{ color: 'var(--red)' }}> · {failed}</span>}
      </span>
    </AnimatedLineSwap>
  )
}

export function ToolSectionToggle({ collapsed, onToggle, run, fileOps, t, controlsId, compact = false, live = false }) {
  return (
    <button
      type="button"
      className="quiet-toggle tool-run-toggle"
      onClick={onToggle}
      aria-expanded={!collapsed}
      aria-controls={controlsId}
    >
      <AnimatedChevron open={!collapsed} className="tool-run-chevron">
        <ChevronDown size={12} strokeWidth={1.5} />
      </AnimatedChevron>
      <GroupSummary live={live} run={run} fileOps={fileOps} t={t} compact={compact} />
    </button>
  )
}

function LiveToolPreview({
  visible,
  latest,
  latestIndex,
  renderBlock,
  getChildKey,
}) {
  const snapshotRef = useRef(null)
  if (latest) snapshotRef.current = { latest, latestIndex }
  const snapshot = snapshotRef.current
  const reduceMotion = useReducedMotion()
  const { mounted, onExited } = usePresence(Boolean(visible && snapshot))
  const wrapperRef = useRef(null)
  const enteredRef = useRef(false)

  useLayoutEffect(() => {
    const element = wrapperRef.current
    if (!mounted || !element) return undefined

    if (visible) {
      if (!enteredRef.current) {
        enteredRef.current = true
        element.style.opacity = '1'
        element.style.transform = ''
        element.style.height = ''
        return undefined
      }
      if (reduceMotion) {
        element.style.opacity = '1'
        element.style.transform = ''
        element.style.height = ''
        return undefined
      }
      const naturalHeight = element.scrollHeight
      const motion = animate(element, {
        opacity: 1,
        translateY: '0px',
        height: `${naturalHeight}px`,
        duration: TOOL_SWAP_DURATION,
        ease: EASE_SPRING,
        onComplete: () => { element.style.height = '' },
      })
      return () => motion.cancel()
    }

    if (reduceMotion) {
      onExited()
      return undefined
    }
    const height = element.offsetHeight
    element.style.height = `${height}px`
    const motion = animate(element, {
      opacity: 0,
      translateY: '-8px',
      height: '0px',
      duration: TOOL_SWAP_DURATION,
      ease: EASE_SPRING,
      onComplete: onExited,
    })
    return () => motion.cancel()
  }, [mounted, onExited, reduceMotion, visible])

  if (!mounted || !snapshot) return null
  const key = getChildKey
    ? getChildKey(snapshot.latest, snapshot.latestIndex)
    : (snapshot.latest.id || snapshot.latestIndex)

  return (
    <div ref={wrapperRef} className="tool-live-preview">
      <div className="tool-tree tool-tree-live">
        <div className="tool-tree-child is-last">
          <span aria-hidden="true" className="chat-branch-connector">└─</span>
          <div className="tool-tree-child-content">
            <AnimatedLineSwap itemKey={key} animateOnMount block>
              {renderBlock(snapshot.latest, snapshot.latestIndex, { livePreview: true })}
            </AnimatedLineSwap>
          </div>
        </div>
      </div>
    </div>
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
  live = false,
}) {
  const bodyId = useId()
  const latestIndex = Math.max(run.length - 1, 0)
  const latest = run[latestIndex] || null

  const renderToolTree = () => (
    <div className="tool-tree">
      {run.map((toolBlock, runIndex) => {
        const last = runIndex === run.length - 1
        return (
          <div
            key={getChildKey ? getChildKey(toolBlock, runIndex) : (toolBlock.id || runIndex)}
            className={`tool-tree-child${last ? ' is-last' : ''}`}
          >
            <span aria-hidden="true" className="chat-branch-connector">
              {last ? '└─' : '├─'}
            </span>
            <div className="tool-tree-child-content">
              {renderBlock(toolBlock, runIndex, { livePreview: false })}
            </div>
          </div>
        )
      })}
    </div>
  )

  return (
    <div className={`tool-run${compact ? ' tool-run-compact' : ''}`}>
      <ToolSectionToggle
        collapsed={collapsed}
        onToggle={onToggle}
        run={run}
        fileOps={fileOps}
        t={t}
        controlsId={bodyId}
        compact={compact}
        live={live}
      />

      <div id={bodyId}>
        <LiveToolPreview
          visible={live && !collapsed}
          latest={latest}
          latestIndex={latestIndex}
          renderBlock={renderBlock}
          getChildKey={getChildKey}
        />

        <AnimatedCollapse
          open={!live && !collapsed}
          animateHeight={false}
          keepMounted
          deferContentOnClose
        >
          {renderToolTree}
        </AnimatedCollapse>
      </div>
    </div>
  )
}
