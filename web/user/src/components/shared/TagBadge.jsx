import { X } from 'lucide-react'
import { tagColorStyle } from '../../utils/sessionTags'

export default function TagBadge({ tag, colorIndex, onRemove, maxWidth = '100%' }) {
  return (
    <span
      className="inline-flex items-center min-w-0"
      style={{
        ...tagColorStyle(tag, colorIndex),
        border: 'none',
        borderRadius: 8,
        fontSize: 12,
        fontWeight: 600,
        lineHeight: '14px',
        padding: onRemove ? '0 3px 0 5px' : '0 5px',
        maxWidth,
      }}
      title={tag}
    >
      <span className="truncate" style={{ minWidth: 0 }}>{tag}</span>
      {onRemove && (
        <button
          type="button"
          aria-label={`Remove ${tag}`}
          onClick={(event) => {
            event.stopPropagation()
            onRemove(tag)
          }}
          className="inline-flex items-center justify-center flex-shrink-0"
          style={{
            width: 12,
            height: 12,
            padding: 0,
            marginLeft: 2,
            background: 'transparent',
            border: 'none',
            borderRadius: 2,
            color: 'currentColor',
            cursor: 'pointer',
            opacity: 0.75,
            transition: 'opacity 150ms ease',
          }}
          onMouseEnter={(event) => { event.currentTarget.style.opacity = '1' }}
          onMouseLeave={(event) => { event.currentTarget.style.opacity = '0.75' }}
        >
          <X size={10} strokeWidth={1.5} />
        </button>
      )}
    </span>
  )
}
