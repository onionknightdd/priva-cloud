import { useState } from 'react'
import { X } from 'lucide-react'
import { tagColorStyle } from '../../utils/sessionTags'

export default function TagFilterChip({
  active, label, tag, colorIndex, onClick, onRemove, removeLabel,
}) {
  const [hovered, setHovered] = useState(false)
  const tagStyle = active && tag ? tagColorStyle(tag, colorIndex) : null
  const color = tagStyle?.color || (active
    ? 'var(--text-inverse)'
    : (hovered ? 'var(--text-secondary)' : 'var(--text-dim)'))
  const background = tagStyle?.background || (active
    ? 'var(--blue)'
    : (hovered ? 'var(--border)' : 'var(--bg-elevated)'))

  return (
    <span
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
      className="inline-flex items-center justify-center"
      style={{
        ...(tagStyle || {}),
        background,
        border: 'none',
        borderRadius: 2,
        padding: 0,
        fontSize: 12,
        fontWeight: 600,
        lineHeight: '14px',
        textAlign: 'center',
        color,
        overflow: 'hidden',
        transition: 'color 150ms ease, background 150ms ease',
      }}
    >
      <button
        type="button"
        aria-pressed={active}
        onClick={onClick}
        className="inline-flex items-center justify-center min-w-0"
        style={{
          background: 'transparent',
          border: 'none',
          color: 'inherit',
          cursor: 'pointer',
          font: 'inherit',
          lineHeight: 'inherit',
          minWidth: 0,
          padding: active && onRemove ? '1px 2px 1px 6px' : '1px 6px',
        }}
      >
        <span className="truncate" style={{ maxWidth: 110 }}>{label}</span>
      </button>
      {active && onRemove && (
        <button
          type="button"
          aria-label={removeLabel}
          title={removeLabel}
          onClick={(event) => {
            event.stopPropagation()
            onRemove()
          }}
          className="inline-flex items-center justify-center flex-shrink-0"
          style={{
            alignSelf: 'stretch',
            background: 'transparent',
            border: 'none',
            color: 'inherit',
            cursor: 'pointer',
            padding: '1px 4px 1px 2px',
            opacity: 0.78,
            transition: 'opacity 150ms ease',
          }}
          onMouseEnter={(event) => { event.currentTarget.style.opacity = '1' }}
          onMouseLeave={(event) => { event.currentTarget.style.opacity = '0.78' }}
        >
          <X size={10} strokeWidth={1.5} />
        </button>
      )}
    </span>
  )
}
