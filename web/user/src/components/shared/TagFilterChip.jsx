import { useState } from 'react'
import { Flag } from 'lucide-react'

export default function TagFilterChip({ active, label, onClick, showIcon = true }) {
  const [hovered, setHovered] = useState(false)
  const color = active
    ? 'var(--text-primary)'
    : (hovered ? 'var(--text-secondary)' : 'var(--text-dim)')

  return (
    <button
      type="button"
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="inline-flex items-center uppercase"
      style={{
        gap: 3,
        background: active ? 'var(--bg-elevated)' : 'transparent',
        border: '1px solid var(--border-subtle)',
        borderLeft: active ? '2px solid var(--blue)' : '2px solid transparent',
        borderRadius: 2,
        padding: '1px 6px',
        fontSize: 10,
        letterSpacing: '0.05em',
        fontWeight: 600,
        lineHeight: 1.4,
        color,
        cursor: 'pointer',
        transition: 'color 150ms ease, background 150ms ease',
      }}
    >
      {showIcon && <Flag size={10} strokeWidth={1.5} />}
      <span className="truncate" style={{ maxWidth: 110 }}>{label}</span>
    </button>
  )
}
