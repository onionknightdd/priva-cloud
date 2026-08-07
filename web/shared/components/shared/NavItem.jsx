import { useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { AnimatedChevron } from './Accordion'
import { SlidingTabIndicator } from './Tabs'
import { DURATION, EASE_SPRING } from '../../motion/tokens'

/**
 * Shared sidebar navigation row (style guide: `.nav-item`).
 *
 * Borderless by default; active/hover paint `--bg-elevated` + `--text-primary`.
 * Collapsed → icon-only with a tooltip.
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
 *   selectionLayoutId  Anime.js FLIP group id for a moving active background
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
  selectionLayoutId = null,
  onClick,
  title,
}) {
  const [hovered, setHovered] = useState(false)
  const baseColor = active ? 'var(--text-primary)' : 'var(--text-secondary)'
  const large = scale === 'lg'
  const animatedSelection = Boolean(selectionLayoutId)
  const rowHighlighted = active || hovered
  const rowBackground = active && animatedSelection
    ? 'transparent'
    : rowHighlighted
      ? 'var(--bg-elevated)'
      : 'transparent'

  return (
    <button
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
        paddingLeft: collapsed ? 0 : 10 + indent,
        justifyContent: collapsed ? 'center' : 'flex-start',
        border: 0,
        borderLeft: 'none',
        // Active and hover fills share the same restrained row treatment.
        borderRadius: 8,
        background: rowBackground,
        color: disabled ? 'var(--text-dim)' : rowHighlighted ? 'var(--text-primary)' : baseColor,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        textAlign: 'left',
        minWidth: 0,
        transition: 'background 150ms ease, color 150ms ease',
      }}
      onMouseEnter={() => { if (!disabled) setHovered(true) }}
      onMouseLeave={() => setHovered(false)}
    >
      {active && animatedSelection && (
        <SlidingTabIndicator
          variant="frame"
          layoutId={selectionLayoutId}
          duration={DURATION.canvas}
          ease={EASE_SPRING}
          animateInitial
          style={{ background: 'var(--bg-elevated)', border: 'none', borderRadius: 8 }}
        />
      )}
      <span
        className="flex items-center min-w-0"
        style={{ position: 'relative', zIndex: 1, width: '100%', gap: 8, justifyContent: collapsed ? 'center' : 'flex-start' }}
      >
        {Icon && (
          <Icon
            size={large ? 19 : 17}
            strokeWidth={1.5}
            style={{ flexShrink: 0, color: iconColor || 'var(--sidebar-icon-color, currentColor)' }}
          />
        )}
        {!collapsed && (
          <span className="sidebar-menu-label truncate" style={{ flex: '0 1 auto', minWidth: 0, fontSize: large ? 15 : 14 }}>{label}</span>
        )}
        {!collapsed && badge != null && (
          <span style={{ flexShrink: 0, fontSize: 12, fontWeight: 600, color: 'var(--text-dim)' }}>
            {badge}
          </span>
        )}
        {!collapsed && expandable && (
          <AnimatedChevron open={expanded} style={{ color: 'var(--sidebar-icon-color, var(--text-dim))' }}>
            <ChevronDown size={large ? 17 : 15} strokeWidth={1.5} />
          </AnimatedChevron>
        )}
      </span>
    </button>
  )
}
