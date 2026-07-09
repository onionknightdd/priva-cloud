import { useLayoutEffect, useRef } from 'react'
import { ChevronDown } from 'lucide-react'
import { animate } from 'animejs'
import { AnimatedChevron } from './Accordion'
import { SlidingTabIndicator } from './Tabs'
import { useReducedMotion } from '../../motion/useReducedMotion'
import { EASE_OUT } from '../../motion/tokens'

/**
 * Shared sidebar navigation row (style guide: `.nav-item`).
 *
 * Borderless by default; active/hover paint `--bg-elevated` + `--text-primary`,
 * active adds a 2px `--blue` left rail. The rail gives a one-shot scaleY
 * 0.6→1 tick when a row is activated LIVE (I5 spec) — rows that mount
 * already-active render it static. Collapsed → icon-only with a tooltip.
 *
 * Props:
 *   icon        lucide component
 *   label       row text
 *   active      active/selected state (bg-elevated + blue left rail)
 *   disabled    dim + not-allowed, no hover
 *   collapsed   icon-only (label/badge/chevron hidden), title used as tooltip
 *   badge       optional right-aligned count (e.g. session total)
 *   indent      extra left padding (px) for sub-items
 *   expandable  show a trailing chevron
 *   expanded    chevron rotation state
 *   iconColor   override icon color (defaults to currentColor)
 *   scale       visual size, "md" default or "lg" for primary rows
 *   onClick, title
 */
export default function NavItem({
  icon: Icon,
  label,
  active = false,
  disabled = false,
  collapsed = false,
  badge,
  indent = 0,
  expandable = false,
  expanded = false,
  iconColor,
  scale = 'md',
  itemRef,
  showActiveRail = true,
  activeRailLayoutId,
  activeRailOffset = -2,
  onClick,
  title,
}) {
  const baseColor = active ? 'var(--text-primary)' : 'var(--text-secondary)'
  const large = scale === 'lg'
  const railRef = useRef(null)
  const wasActiveRef = useRef(active)
  const reducedMotion = useReducedMotion()

  // One-shot activation tick on the rail — live transitions only.
  useLayoutEffect(() => {
    if (active === wasActiveRef.current) return
    wasActiveRef.current = active
    const el = railRef.current
    if (!showActiveRail || !active || !el || reducedMotion) return
    el.style.transform = 'scaleY(0.6)'
    animate(el, { scaleY: 1, duration: 150, ease: EASE_OUT })
  }, [active, reducedMotion, showActiveRail])

  return (
    <button
      ref={itemRef}
      type="button"
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      title={title || (collapsed ? label : undefined)}
      className="flex items-center w-full relative"
      style={{
        gap: 8,
        minHeight: large ? 34 : 32,
        paddingTop: 6,
        paddingBottom: 6,
        paddingRight: collapsed ? 0 : 8,
        paddingLeft: collapsed ? 0 : 8 + indent,
        justifyContent: collapsed ? 'center' : 'flex-start',
        border: 0,
        // Reserved 2px lane; the paintable rail below overlays it so it can
        // scaleY-tick (a border can't be transformed).
        borderLeft: '2px solid transparent',
        // Full-bleed (square) like SessionItem — the 2px left bar is the active indicator.
        borderRadius: 0,
        background: active ? 'var(--bg-elevated)' : 'transparent',
        color: disabled ? 'var(--text-dim)' : baseColor,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        textAlign: 'left',
        minWidth: 0,
        transition: 'background 150ms ease, color 150ms ease',
      }}
      onMouseEnter={(e) => {
        if (disabled || active) return
        e.currentTarget.style.background = 'var(--bg-elevated)'
        e.currentTarget.style.color = 'var(--text-primary)'
      }}
      onMouseLeave={(e) => {
        if (disabled || active) return
        e.currentTarget.style.background = 'transparent'
        e.currentTarget.style.color = baseColor
      }}
    >
      {showActiveRail && active && activeRailLayoutId ? (
        <SlidingTabIndicator
          variant="left-border"
          layoutId={activeRailLayoutId}
          style={{ left: activeRailOffset, zIndex: 2 }}
        />
      ) : showActiveRail && active ? (
        <span
          ref={railRef}
          aria-hidden="true"
          style={{
            position: 'absolute',
            left: activeRailOffset,
            top: 0,
            bottom: 0,
            width: 2,
            background: 'var(--blue)',
            pointerEvents: 'none',
          }}
        />
      ) : null}
      {Icon && (
        <Icon
          size={large ? 18 : 16}
          strokeWidth={1.5}
          style={{ flexShrink: 0, color: iconColor || 'currentColor' }}
        />
      )}
      {!collapsed && (
        <span className="flex-1 truncate" style={{ minWidth: 0, fontSize: large ? 14 : 13 }}>{label}</span>
      )}
      {!collapsed && badge != null && (
        <span style={{ flexShrink: 0, fontSize: 11, fontWeight: 600, color: 'var(--text-dim)' }}>
          {badge}
        </span>
      )}
      {!collapsed && expandable && (
        <AnimatedChevron open={expanded} style={{ color: 'var(--text-dim)' }}>
          <ChevronDown size={large ? 16 : 14} strokeWidth={1.5} />
        </AnimatedChevron>
      )}
    </button>
  )
}
