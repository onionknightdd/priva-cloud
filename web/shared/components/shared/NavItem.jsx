import { ChevronDown } from 'lucide-react'
import { AnimatedChevron } from './Accordion'

/**
 * Shared sidebar navigation row (style guide: `.nav-item`).
 *
 * Borderless by default; active/hover paint `--bg-elevated` + `--text-primary`,
 * active adds a 2px `--blue` left border. Collapsed → icon-only with a tooltip.
 *
 * Props:
 *   icon        lucide component
 *   label       row text
 *   active      active/selected state (bg-elevated + blue left border)
 *   disabled    dim + not-allowed, no hover
 *   collapsed   icon-only (label/badge/chevron hidden), title used as tooltip
 *   badge       optional right-aligned count (e.g. session total)
 *   indent      extra left padding (px) for sub-items
 *   expandable  show a trailing chevron
 *   expanded    chevron rotation state
 *   iconColor   override icon color (defaults to currentColor)
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
  onClick,
  title,
}) {
  const baseColor = active ? 'var(--text-primary)' : 'var(--text-secondary)'
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={disabled ? undefined : onClick}
      title={title || (collapsed ? label : undefined)}
      className="flex items-center w-full"
      style={{
        gap: 8,
        minHeight: 32,
        paddingTop: 6,
        paddingBottom: 6,
        paddingRight: collapsed ? 0 : 8,
        paddingLeft: collapsed ? 0 : 8 + indent,
        justifyContent: collapsed ? 'center' : 'flex-start',
        border: 0,
        borderLeft: `2px solid ${active ? 'var(--blue)' : 'transparent'}`,
        // Full-bleed (square) like SessionItem — the 2px left bar is the active indicator.
        borderRadius: 0,
        background: active ? 'var(--bg-elevated)' : 'transparent',
        color: disabled ? 'var(--text-dim)' : baseColor,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        textAlign: 'left',
        minWidth: 0,
        transition: 'background 150ms ease, color 150ms ease, border-color 150ms ease',
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
      {Icon && (
        <Icon
          size={16}
          strokeWidth={1.5}
          style={{ flexShrink: 0, color: iconColor || 'currentColor' }}
        />
      )}
      {!collapsed && (
        <span className="flex-1 truncate" style={{ minWidth: 0, fontSize: 13 }}>{label}</span>
      )}
      {!collapsed && badge != null && (
        <span style={{ flexShrink: 0, fontSize: 11, fontWeight: 600, color: 'var(--text-dim)' }}>
          {badge}
        </span>
      )}
      {!collapsed && expandable && (
        <AnimatedChevron open={expanded} style={{ color: 'var(--text-dim)' }}>
          <ChevronDown size={14} strokeWidth={1.5} />
        </AnimatedChevron>
      )}
    </button>
  )
}
