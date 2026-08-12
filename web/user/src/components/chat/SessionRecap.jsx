import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { animate } from 'animejs'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { EASE_TAB } from '@shared/motion/tokens'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import useChatStore from '../../stores/chatStore'

/**
 * One-line recap of the current session, rendered as a markdown-style
 * blockquote just above the composer.
 *
 * Self-gating like RewindBanner: returns null unless there is something to
 * show, so ChatPanel can mount it unconditionally. It is deliberately hidden
 * while streaming — MessageList is virtualized with `overflowAnchor: none`, so
 * a sibling that changes height mid-turn would yank the transcript's scroll
 * position. Waiting also matches when the text is refreshed: the backend only
 * regenerates once a turn has finished.
 */
export default function SessionRecap({ onHeightChange }) {
  const { t } = useTranslation()
  const recapRef = useRef(null)
  const dismissAnimationRef = useRef(null)
  const [closing, setClosing] = useState(false)
  const reducedMotion = useReducedMotion()
  const recap = useChatStore((s) => s.recap)
  const dismissed = useChatStore((s) => s.recapDismissed)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const dismissRecap = useChatStore((s) => s.dismissRecap)
  const visible = Boolean(recap) && !dismissed && !isStreaming

  useLayoutEffect(() => {
    if (!visible) {
      onHeightChange?.(0)
      setClosing(false)
      return undefined
    }

    const element = recapRef.current
    if (!element) return undefined
    const reportHeight = () => {
      onHeightChange?.(Math.ceil(element.getBoundingClientRect().height))
    }

    reportHeight()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(reportHeight)
    observer.observe(element)
    return () => observer.disconnect()
  }, [onHeightChange, visible, recap])

  useEffect(() => () => dismissAnimationRef.current?.cancel(), [])

  const handleDismiss = () => {
    const element = recapRef.current
    if (!element || closing || reducedMotion) {
      onHeightChange?.(0)
      dismissRecap()
      return
    }

    setClosing(true)
    dismissAnimationRef.current?.cancel()
    element.style.height = `${element.getBoundingClientRect().height}px`
    element.style.overflow = 'hidden'
    element.style.willChange = 'height, opacity, transform'
    void element.offsetHeight
    dismissAnimationRef.current = animate(element, {
      height: '0px',
      opacity: 0,
      translateY: '4px',
      duration: 250,
      ease: EASE_TAB,
      onUpdate: () => {
        onHeightChange?.(Math.ceil(element.getBoundingClientRect().height))
      },
      onComplete: () => {
        dismissAnimationRef.current = null
        onHeightChange?.(0)
        dismissRecap()
      },
    })
  }

  if (!visible) return null

  return (
    <div ref={recapRef} className="flex-shrink-0" style={{ pointerEvents: 'none' }}>
      {/* Same track as ChatInput's inner column so the quote bar lines up
          with the composer's left edge. */}
      <div
        style={{
          width: 'auto',
          maxWidth: 'none',
          marginLeft: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
          marginRight: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
          background: 'var(--bg-base)',
          pointerEvents: closing ? 'none' : 'auto',
          transition: 'margin-left var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1)), margin-right var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
        }}
      >
        <div
          className="flex items-start gap-2"
          style={{
            borderLeft: '2px solid var(--border-strong)',
            paddingLeft: 12,
            color: 'var(--text-secondary)',
          }}
        >
          <span
            className="flex-1"
            title={recap}
            style={{
              minWidth: 0,
              fontSize: 12,
              lineHeight: 1.5,
              wordBreak: 'break-word',
              overflowWrap: 'break-word',
              // Two lines, then ellipsis. -webkit-* is the only cross-browser
              // way to clamp at a line count rather than a pixel height.
              display: '-webkit-box',
              WebkitBoxOrient: 'vertical',
              WebkitLineClamp: 2,
              overflow: 'hidden',
            }}
          >
            {recap}
          </span>
          <button
            type="button"
            onClick={handleDismiss}
            disabled={closing}
            title={t('chat.recapDismiss')}
            aria-label={t('chat.recapDismiss')}
            className="flex-shrink-0 inline-flex items-center justify-center"
            style={{
              background: 'transparent',
              border: 'none',
              padding: 0,
              cursor: 'pointer',
              // Always visible, unlike the hover-revealed copy button: a
              // control that only appears on hover does not exist on touch.
              color: 'var(--text-dim)',
              transition: 'color 150ms ease',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>
      </div>
    </div>
  )
}
