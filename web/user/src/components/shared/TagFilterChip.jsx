import { useState } from 'react'

export default function TagFilterChip({ active, label, onClick }) {
  const [hovered, setHovered] = useState(false)
  const color = active
    ? 'var(--text-inverse)'
    : (hovered ? 'var(--text-primary)' : 'var(--text-secondary)')
  const background = active
    ? 'var(--orange)'
    : (hovered ? 'var(--border)' : 'var(--bg-elevated)')

  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="inline-flex items-center"
      style={{
        background,
        border: 'none',
        borderRadius: 12,
        padding: '0 5px',
        fontSize: 10,
        fontWeight: 600,
        lineHeight: '14px',
        color,
        cursor: 'pointer',
        transition: 'color 150ms ease, background 150ms ease',
      }}
    >
      <span className="truncate" style={{ maxWidth: 110 }}>{label}</span>
    </button>
  )
}
