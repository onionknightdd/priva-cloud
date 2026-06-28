import { ChevronDown } from 'lucide-react'
import { AnimatedChevron } from './Accordion'

/**
 * Shared section header (style guide: `.panel-header`) — a label on the left and
 * right-aligned icon actions. Used for the sidebar "PROJECT" header and any dense
 * panel header. Label is uppercase, dim, and optionally clickable.
 *
 * Props:
 *   label    header text (rendered uppercase, letter-spaced)
 *   onClick  optional — makes the label a button (e.g. collapse-all)
 *   title    optional tooltip for the label button
 *   open     optional boolean — when set, renders a leading collapse chevron
 *   actions  [{ icon, title, onClick, active?, spinning?, disabled? }]
 */
export default function PanelHeader({ label, onClick, title, open, actions = [] }) {
  const collapsible = typeof open === 'boolean'
  return (
    <div className="flex items-center justify-between" style={{ padding: '4px 12px', gap: 8 }}>
      <button
        type="button"
        onClick={onClick}
        title={title}
        className="flex items-center min-w-0 uppercase"
        style={{
          background: 'transparent',
          border: 'none',
          cursor: onClick ? 'pointer' : 'default',
          color: 'var(--text-dim)',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
          gap: 4,
          padding: 0,
          minWidth: 0,
          transition: 'color 150ms ease',
        }}
        onMouseEnter={(e) => { if (onClick) e.currentTarget.style.color = 'var(--text-secondary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
      >
        {collapsible && (
          <AnimatedChevron open={open} style={{ color: 'var(--text-dim)' }}>
            <ChevronDown size={12} strokeWidth={1.5} />
          </AnimatedChevron>
        )}
        <span className="truncate">{label}</span>
      </button>
      <div className="flex items-center" style={{ gap: 2, flexShrink: 0 }}>
        {actions.map((action, i) => {
          const Icon = action.icon
          return (
            <button
              key={action.key || i}
              type="button"
              onClick={action.onClick}
              disabled={action.disabled}
              title={action.title}
              className="flex items-center justify-center"
              style={{
                width: 24,
                height: 24,
                background: 'transparent',
                border: 'none',
                borderRadius: 4,
                cursor: action.disabled ? 'default' : 'pointer',
                color: action.active ? 'var(--text-secondary)' : 'var(--text-dim)',
                transition: 'color 150ms ease, background 150ms ease',
              }}
              onMouseEnter={(e) => {
                if (action.disabled) return
                e.currentTarget.style.color = 'var(--text-secondary)'
                e.currentTarget.style.background = 'var(--bg-elevated)'
              }}
              onMouseLeave={(e) => {
                e.currentTarget.style.color = action.active ? 'var(--text-secondary)' : 'var(--text-dim)'
                e.currentTarget.style.background = 'transparent'
              }}
            >
              <Icon
                size={14}
                strokeWidth={1.5}
                style={{ animation: action.spinning ? 'spin 1s linear infinite' : 'none' }}
              />
            </button>
          )
        })}
      </div>
    </div>
  )
}
