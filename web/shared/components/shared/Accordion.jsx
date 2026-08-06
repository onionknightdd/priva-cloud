import { useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate } from 'animejs'
import { ChevronDown } from 'lucide-react'
import { usePresence } from '../../motion/usePresence'
import { useReducedMotion } from '../../motion/useReducedMotion'
import { DUR_MIGRATION, EASE_ACCORDION, EASE_ACCORDION_CSS, EASE_OUT, EASE_TAB } from '../../motion/tokens'

export const ACCORDION_EASE = [0.22, 1, 0.36, 1]

// Legacy transition descriptor (framer-motion shape), kept for API
// compatibility — the collapse itself now runs on anime.js.
export function useAccordionTransition() {
  const shouldReduce = useReducedMotion()
  return shouldReduce
    ? { duration: 0 }
    : {
      height: { duration: 0.3, ease: ACCORDION_EASE },
      opacity: { duration: 0.2, ease: 'easeOut' },
    }
}

export function AnimatedCollapse({
  open,
  children,
  id,
  className,
  style,
  innerClassName,
  innerStyle,
  animateHeight = true,
  keepMounted = false,
  deferContentOnClose = false,
  heightDuration = DUR_MIGRATION.accordionHeight,
  opacityDuration = DUR_MIGRATION.accordionOpacity,
  heightEase = EASE_ACCORDION,
  opacityEase = EASE_OUT,
  modeBDuration = DUR_MIGRATION.accordionModeB,
  animateContentResize = false,
  resizeDuration = DUR_MIGRATION.accordionHeight,
  resizeEase = EASE_ACCORDION,
}) {
  const shouldReduce = useReducedMotion()
  const [hasMounted, setHasMounted] = useState(open)
  const [isDisplayed, setIsDisplayed] = useState(open)
  const [renderContent, setRenderContent] = useState(open)
  const innerRef = useRef(null)
  const [measuredHeight, setMeasuredHeight] = useState(0)

  // ---- shared animation plumbing -----------------------------------------
  const outerRef = useRef(null)
  const animRef = useRef(null)
  const resizeAnimRef = useRef(null)
  const openRef = useRef(open)
  openRef.current = open

  // Mode A presence: keeps the subtree mounted while the exit collapse plays.
  const { mounted, onExited } = usePresence(open)
  // AnimatePresence initial={false} parity: born-open renders statically open.
  const appearedOpenRef = useRef(open)
  const enteredRef = useRef(open)

  useLayoutEffect(() => {
    if (open) {
      setHasMounted(true)
      setIsDisplayed(true)
      setRenderContent(true)
    }
  }, [open])

  useLayoutEffect(() => {
    if (animateHeight || open || !deferContentOnClose || !innerRef.current) return
    const node = innerRef.current
    setMeasuredHeight(node.offsetHeight || node.scrollHeight)
    setRenderContent(false)
  }, [animateHeight, deferContentOnClose, open])

  useLayoutEffect(() => {
    if (
      animateHeight
      || !open
      || !renderContent
      || !innerRef.current
      || (!isDisplayed && !(keepMounted && hasMounted))
    ) return undefined

    const node = innerRef.current
    const measure = () => setMeasuredHeight(node.scrollHeight)
    measure()

    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(node)
    return () => observer.disconnect()
  }, [animateHeight, hasMounted, isDisplayed, keepMounted, open, renderContent])

  // ---- Mode A: height 0 ↔ auto with presence-latched exit -----------------
  useLayoutEffect(() => {
    if (!animateHeight) return
    if (!mounted) {
      enteredRef.current = false
      appearedOpenRef.current = false
      return
    }
    const el = outerRef.current
    if (!el) {
      if (!open) onExited()
      return
    }

    // Always cancel first: a replaced animation would otherwise still fire
    // its onComplete later (e.g. a stale enter snapping height to 'auto'
    // mid-close).
    animRef.current?.cancel()
    resizeAnimRef.current?.cancel()

    if (shouldReduce) {
      if (open) {
        enteredRef.current = true
        appearedOpenRef.current = false
        el.style.height = 'auto'
        el.style.opacity = '1'
      } else {
        onExited()
      }
      return
    }

    if (open) {
      if (appearedOpenRef.current) {
        // Born open: render statically, no enter animation (initial={false}).
        appearedOpenRef.current = false
        enteredRef.current = true
        return
      }
      if (!enteredRef.current) {
        // Fresh enter: pre-paint the from-state before first paint.
        el.style.height = '0px'
        el.style.opacity = '0'
      } else if (el.style.height === 'auto' || el.style.height === '') {
        // Already open at rest — nothing to animate.
        return
      }
      enteredRef.current = true
      const h = el.scrollHeight // content height (independent of current height)
      animRef.current = animate(el, {
        height: { to: `${h}px`, duration: heightDuration, ease: heightEase },
        opacity: { to: 1, duration: opacityDuration, ease: opacityEase },
        onComplete: () => {
          // Back to natural height so later content growth flows freely.
          if (openRef.current) el.style.height = 'auto'
        },
      })
    } else {
      if (el.style.height === 'auto' || el.style.height === '') {
        const h = el.offsetHeight || el.scrollHeight
        el.style.height = `${h}px`
      }
      // Commit the pixel height before tweening to zero; anime cannot
      // interpolate a visible close cleanly from CSS auto.
      void el.offsetHeight
      animRef.current = animate(el, {
        height: { to: '0px', duration: heightDuration, ease: heightEase },
        opacity: { to: 0, duration: opacityDuration, ease: opacityEase },
        onComplete: onExited,
      })
    }

    return () => animRef.current?.cancel()
  }, [animateHeight, heightDuration, heightEase, open, mounted, opacityDuration, opacityEase, shouldReduce, onExited])

  useLayoutEffect(() => {
    if (!animateHeight || !animateContentResize || !open || !mounted || shouldReduce) return undefined
    const outer = outerRef.current
    const inner = innerRef.current
    if (!outer || !inner || typeof ResizeObserver === 'undefined') return undefined

    let lastHeight = inner.scrollHeight
    let frame = null
    const observer = new ResizeObserver(() => {
      if (frame != null) cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() => {
        frame = null
        if (!openRef.current) return
        // Initial open/close transitions own the outer height. Content resize
        // smoothing only runs when the collapse is resting at natural height.
        if (outer.style.height && outer.style.height !== 'auto') {
          lastHeight = inner.scrollHeight
          return
        }
        const nextHeight = inner.scrollHeight
        const previousHeight = lastHeight
        lastHeight = nextHeight
        if (Math.abs(nextHeight - previousHeight) < 1) return

        resizeAnimRef.current?.cancel()
        outer.style.height = `${previousHeight}px`
        void outer.offsetHeight
        resizeAnimRef.current = animate(outer, {
          height: `${nextHeight}px`,
          duration: resizeDuration,
          ease: resizeEase,
          onComplete: () => {
            if (openRef.current) outer.style.height = 'auto'
          },
        })
      })
    })

    observer.observe(inner)
    return () => {
      if (frame != null) cancelAnimationFrame(frame)
      observer.disconnect()
      resizeAnimRef.current?.cancel()
    }
  }, [animateContentResize, animateHeight, mounted, open, resizeDuration, resizeEase, shouldReduce])

  // ---- Mode B: measured-height reveal (state machinery preserved) ---------
  useLayoutEffect(() => {
    if (animateHeight) return undefined
    const el = outerRef.current
    if (!el) return undefined

    // framer fired onAnimationComplete even at duration 0 — the close
    // bookkeeping below must run on every path.
    const complete = () => {
      if (!openRef.current) {
        setIsDisplayed(false)
        if (!keepMounted) setMeasuredHeight(0)
      }
    }

    animRef.current?.cancel()

    // Fresh element (remount): give it a sane inline from-state; computed
    // height on display:none parses as 'auto', which the tween can't use.
    if (!el.style.height) {
      el.style.height = open ? `${el.scrollHeight}px` : '0px'
    }

    const target = open ? measuredHeight : 0
    if (shouldReduce) {
      el.style.height = `${target}px`
      complete()
      return undefined
    }
    animRef.current = animate(el, {
      height: `${target}px`,
      duration: modeBDuration,
      ease: EASE_TAB,
      onComplete: complete,
    })
    return undefined
  }, [animateHeight, modeBDuration, open, measuredHeight, keepMounted, shouldReduce, isDisplayed, hasMounted])

  if (!animateHeight) {
    const shouldRender = open || isDisplayed || (keepMounted && hasMounted)
    if (!shouldRender) return null
    return (
      <div
        id={id}
        ref={outerRef}
        className={className}
        aria-hidden={!open}
        style={{
          display: open || isDisplayed ? undefined : 'none',
          pointerEvents: open ? undefined : 'none',
          overflow: 'hidden',
          contain: 'layout paint style',
          willChange: 'height',
          ...style,
        }}
      >
        <div
          ref={innerRef}
          className={innerClassName}
          style={{
            minHeight: 0,
            overflow: 'hidden',
            transform: 'translateZ(0)',
            ...innerStyle,
          }}
        >
          {open || renderContent ? (typeof children === 'function' ? children() : children) : null}
        </div>
      </div>
    )
  }

  if (!mounted) return null
  return (
    <div
      id={id}
      ref={outerRef}
      className={className}
      style={{ overflow: 'hidden', ...style }}
    >
      <div ref={innerRef} className={innerClassName} style={innerStyle}>
        {typeof children === 'function' ? children() : children}
      </div>
    </div>
  )
}

export function AnimatedChevron({
  open,
  children,
  className,
  style,
}) {
  const shouldReduce = useReducedMotion()

  return (
    <span
      className={className}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        transform: `rotate(${open ? 180 : 0}deg)`,
        transition: shouldReduce ? 'none' : `transform ${DUR_MIGRATION.chevron}ms ${EASE_ACCORDION_CSS}`,
        ...style,
      }}
    >
      {children || <ChevronDown size={12} strokeWidth={1.5} />}
    </span>
  )
}

function normalizeDefaultOpen(defaultOpen) {
  if (defaultOpen == null) return []
  return Array.isArray(defaultOpen) ? defaultOpen : [defaultOpen]
}

export default function Accordion({
  items,
  defaultOpen = null,
  allowMultiple = false,
  onChange,
}) {
  const rootId = useId()
  const [openIndices, setOpenIndices] = useState(() => normalizeDefaultOpen(defaultOpen))
  const itemList = useMemo(() => items || [], [items])

  const toggle = (index) => {
    setOpenIndices((current) => {
      const isOpen = current.includes(index)
      const next = allowMultiple
        ? isOpen
          ? current.filter((item) => item !== index)
          : [...current, index]
        : isOpen
          ? []
          : [index]
      onChange?.(next)
      return next
    })
  }

  return (
    <div
      style={{
        border: '1px solid var(--border)',
        borderRadius: 4,
        overflow: 'hidden',
        background: 'var(--bg-surface)',
      }}
    >
      {itemList.map((item, index) => {
        const isOpen = openIndices.includes(index)
        const bodyId = `${rootId}-body-${index}`

        return (
          <div
            key={item.key || index}
            style={{
              borderTop: index === 0 ? 'none' : '1px solid var(--border-subtle)',
            }}
          >
            <button
              type="button"
              aria-expanded={isOpen}
              aria-controls={bodyId}
              onClick={() => toggle(index)}
              className="flex items-center justify-between gap-3 w-full"
              style={{
                border: 'none',
                background: 'transparent',
                color: 'var(--text-primary)',
                cursor: 'pointer',
                padding: '10px 12px',
                fontSize: 13,
                fontWeight: 600,
                textAlign: 'left',
                transition: 'background 150ms ease',
              }}
              onMouseEnter={(event) => { event.currentTarget.style.background = 'var(--bg-elevated)' }}
              onMouseLeave={(event) => { event.currentTarget.style.background = 'transparent' }}
            >
              <span style={{ minWidth: 0 }}>{item.title}</span>
              <AnimatedChevron open={isOpen} style={{ color: 'var(--text-dim)' }} />
            </button>
            <AnimatedCollapse
              open={isOpen}
              id={bodyId}
              innerStyle={{
                padding: '0 12px 12px 12px',
                color: 'var(--text-secondary)',
                fontSize: 13,
                lineHeight: 1.6,
              }}
            >
              {item.body}
            </AnimatedCollapse>
          </div>
        )
      })}
    </div>
  )
}

export function AccordionDemo() {
  return (
    <Accordion
      defaultOpen={0}
      items={[
        { title: 'CONFIG', body: <p style={{ margin: 0 }}>Review runtime settings before the next run.</p> },
        {
          title: 'LOGS',
          body: (
            <div style={{ display: 'grid', gap: 8 }}>
              <p style={{ margin: 0 }}>Short messages and long traces both use their natural height.</p>
              <p style={{ margin: 0 }}>The collapse wrapper owns height; this inner div owns padding.</p>
            </div>
          ),
        },
        { title: 'ALERTS', body: <p style={{ margin: 0 }}>No active alerts.</p> },
      ]}
    />
  )
}
