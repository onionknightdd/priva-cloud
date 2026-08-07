import { createContext, useContext, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate } from 'animejs'
import { DUR_MIGRATION, EASE_TAB } from '../../motion/tokens'
import { useReducedMotion } from '../../motion/useReducedMotion'

// Shared sliding-tab primitive (mirrors web/user's Tabs) so the admin app gets
// the exact same animated tab switch. The active indicator renders inside the
// active button and FLIP-slides between tabs on change: the outgoing indicator
// stores its live viewport rect on unmount (React 18 runs layout cleanup
// before detach), the incoming one inverts from that rect and animates to
// identity — translate + px width/height, never scale, so 1px borders and 2px
// bars stay crisp.

// Kept for API compatibility (legacy framer-motion transition descriptor).
export const SLIDING_TAB_TRANSITION = {
  type: 'tween',
  duration: 0.25,
  ease: [0.4, 0, 0.2, 1],
}

function normalizeTab(tab, index) {
  if (typeof tab === 'string') {
    return { id: tab, label: tab, value: index }
  }
  return {
    id: tab.id ?? tab.key ?? tab.value ?? String(index),
    label: tab.label ?? tab.name ?? tab.id ?? String(index),
    value: tab.value ?? tab.id ?? index,
    disabled: Boolean(tab.disabled),
    ...tab,
  }
}

// Per-group last-known indicator rects, keyed by layoutId. Indicators used
// outside a SlidingTabGroup share the module-level map (same semantics as a
// framer layoutId without a LayoutGroup: global scope).
const TabGroupContext = createContext(null)
const globalIndicatorRects = new Map()

export function SlidingTabGroup({ children, id }) { // eslint-disable-line no-unused-vars
  const rectsRef = useRef(null)
  if (rectsRef.current === null) rectsRef.current = new Map()
  return (
    <TabGroupContext.Provider value={rectsRef.current}>
      {children}
    </TabGroupContext.Provider>
  )
}

export function SlidingTabIndicator({
  variant = 'underline',
  layoutId = 'tab-indicator',
  style,
  duration = DUR_MIGRATION.tabSlide,
  ease = EASE_TAB,
  animateInitial = false,
}) {
  const rects = useContext(TabGroupContext) || globalIndicatorRects
  const reducedMotion = useReducedMotion()
  const ref = useRef(null)
  const animationRef = useRef(null)
  const reducedRef = useRef(reducedMotion)
  reducedRef.current = reducedMotion

  // Mount-only FLIP. StrictMode's double pass is a no-op: the doubled cleanup
  // stores the just-inverted visual rect, so the second run's delta is ~0.
  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return undefined
    const settle = () => {
      el.style.opacity = ''
      el.style.transform = ''
      el.style.width = variant === 'left-border' ? '2px' : ''
      el.style.height = variant === 'underline' ? '2px' : ''
    }
    const cur = el.getBoundingClientRect()
    const prev = rects.get(layoutId)
    rects.set(layoutId, cur)

    if (prev && !reducedRef.current) {
      const dx = prev.left - cur.left
      const dy = prev.top - cur.top
      const dw = prev.width - cur.width
      const dh = prev.height - cur.height
      if (Math.abs(dx) > 0.5 || Math.abs(dy) > 0.5 || Math.abs(dw) > 0.5 || Math.abs(dh) > 0.5) {
        const horizontalOnly = variant === 'underline'
        // Underlines stay at a fixed 2px height. Animating the vertical axis
        // or height creates a visible handoff jolt as the active button remounts.
        el.style.transform = horizontalOnly
          ? `translateX(${dx}px)`
          : `translateX(${dx}px) translateY(${dy}px)`
        el.style.width = `${prev.width}px`
        if (!horizontalOnly) el.style.height = `${prev.height}px`

        const animation = {
          translateX: 0,
          width: `${cur.width}px`,
          duration: DUR_MIGRATION.tabSlide,
          ease: EASE_TAB,
          onComplete: () => {
            // Restore anchored (left/right/inset) sizing so the indicator
            // keeps tracking its button through resizes.
            settle()
          },
        }
        if (!horizontalOnly) {
          animation.translateY = 0
          animation.height = `${cur.height}px`
        }
        // …then play to the new tab's anchored position.
        animation.duration = duration
        animation.ease = ease
        animationRef.current = animate(el, animation)
      } else {
        // StrictMode replays layout effects against the same DOM node. Always
        // restore the settled state when there is no meaningful FLIP delta.
        settle()
      }
    } else if (!prev && animateInitial && !reducedRef.current) {
      el.style.opacity = '0'
      animationRef.current = animate(el, {
        opacity: 1,
        duration,
        ease,
        onComplete: settle,
      })
    }

    return () => {
      // Seamless handoff: store the live (possibly mid-flight) rect for the
      // next indicator to invert from.
      const live = el.getBoundingClientRect()
      if (live.width || live.height) rects.set(layoutId, live)
      animationRef.current?.cancel()
    }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const baseStyle = variant === 'frame'
    ? {
      position: 'absolute',
      inset: 0,
      background: 'var(--bg-elevated)',
      border: '1px solid var(--border)',
      borderRadius: '4px',
      pointerEvents: 'none',
      zIndex: 0,
    }
    : variant === 'left-border'
      ? {
        position: 'absolute',
        top: 0,
        bottom: 0,
        left: 0,
        width: 2,
        background: 'var(--blue)',
        pointerEvents: 'none',
        zIndex: 0,
      }
      : {
        position: 'absolute',
        left: 0,
        right: 0,
        bottom: 0,
        height: 2,
        background: 'var(--blue)',
        pointerEvents: 'none',
        zIndex: 0,
      }

  return <div ref={ref} style={{ ...baseStyle, ...style }} />
}

export default function Tabs({
  tabs,
  defaultActive = 0,
  activeIndex,
  activeKey,
  onChange,
  variant = 'underline',
  className,
  style,
  buttonClassName,
  buttonStyle,
  indicatorStyle,
  getButtonStyle,
  renderLabel,
  layoutId = 'tab-indicator',
}) {
  const normalizedTabs = useMemo(
    () => (tabs || []).map((tab, index) => normalizeTab(tab, index)),
    [tabs]
  )
  const [localActive, setLocalActive] = useState(defaultActive)
  const [hoveredIndex, setHoveredIndex] = useState(null)
  const groupId = useId()

  const resolvedActiveIndex = activeIndex != null
    ? activeIndex
    : activeKey != null
      ? Math.max(0, normalizedTabs.findIndex((tab) => tab.id === activeKey || tab.key === activeKey || tab.value === activeKey))
      : localActive

  const handleSelect = (tab, index) => {
    if (tab.disabled) return
    if (activeIndex == null && activeKey == null) setLocalActive(index)
    onChange?.(index, tab)
  }

  return (
    <SlidingTabGroup id={groupId}>
      <div className={className} style={style}>
        {normalizedTabs.map((tab, index) => {
          const active = index === resolvedActiveIndex
          const hovered = hoveredIndex === index
          const disabled = tab.disabled
          const resolvedButtonStyle = {
            position: 'relative',
            border: 'none',
            background: 'transparent',
            cursor: disabled ? 'default' : 'pointer',
            color: active ? 'var(--text-primary)' : disabled ? 'var(--text-dim)' : 'var(--text-secondary)',
            transition: 'color 150ms ease, background 150ms ease, border-color 150ms ease',
            ...buttonStyle,
            ...getButtonStyle?.({ tab, index, active, hovered, disabled }),
          }

          return (
            <button
              key={tab.id}
              type="button"
              disabled={disabled}
              className={buttonClassName}
              style={resolvedButtonStyle}
              onClick={() => handleSelect(tab, index)}
              onMouseEnter={() => setHoveredIndex(index)}
              onMouseLeave={() => setHoveredIndex((current) => (current === index ? null : current))}
            >
              {active && (
                <SlidingTabIndicator
                  variant={variant}
                  layoutId={layoutId}
                  style={indicatorStyle}
                />
              )}
              <span style={{ position: 'relative', zIndex: 1 }}>
                {renderLabel ? renderLabel(tab, index, active) : tab.label}
              </span>
            </button>
          )
        })}
      </div>
    </SlidingTabGroup>
  )
}
