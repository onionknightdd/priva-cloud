import { useState } from 'react'
import { tagColorStyle } from '../../utils/sessionTags'

export default function TagFilterChip({ active, label, tag, colorIndex, onClick }) {
  const [hovered, setHovered] = useState(false)
  const tagStyle = tag ? tagColorStyle(tag, colorIndex) : null
  const color = tagStyle?.color || (active
    ? 'var(--text-inverse)'
    : (hovered ? 'var(--text-primary)' : 'var(--text-secondary)'))
  const background = tagStyle?.background || (active
    ? 'var(--blue)'
    : (hovered ? 'var(--border)' : 'var(--bg-elevated)'))

  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="inline-flex items-center"
      style={{
        ...(tagStyle || {}),
        background,
        border: 'none',
        borderRadius: 8,
        padding: '0 5px',
        fontSize: 12,
        fontWeight: 600,
        lineHeight: '14px',
        color,
        cursor: 'pointer',
        opacity: tag ? (active ? 1 : (hovered ? 0.92 : 0.78)) : 1,
        transition: 'color 150ms ease, background 150ms ease, opacity 150ms ease',
      }}
    >
      <span className="truncate" style={{ maxWidth: 110 }}>{label}</span>
    </button>
  )
}
