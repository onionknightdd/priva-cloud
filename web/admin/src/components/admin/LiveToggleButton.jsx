import { RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'

export default function LiveToggleButton({
  active,
  error,
  refreshing,
  onClick,
  spinAnimation = 'fleet-spin 1s linear infinite',
}) {
  const { t } = useTranslation()
  const borderColor = error ? 'var(--red)' : active ? 'var(--green)' : 'var(--border)'
  const textColor = error ? 'var(--red)' : active ? 'var(--green)' : 'var(--text-secondary)'
  const iconSpinning = active || (error && refreshing)

  return (
    <button
      className="flex items-center gap-2 px-2 py-1 text-xs uppercase flex-shrink-0"
      onClick={onClick}
      title={error ? t('admin.liveRetryTitle') : active ? t('admin.livePauseTitle') : t('admin.liveResumeTitle')}
      aria-pressed={active}
      style={{
        background: active ? 'var(--bg-surface)' : 'transparent',
        border: `1px solid ${borderColor}`,
        borderLeft: `2px solid ${borderColor}`,
        borderRadius: 4,
        color: textColor,
        cursor: 'pointer',
        letterSpacing: '0.06em',
        transition: 'color 150ms ease, border-color 150ms ease, background 150ms ease',
      }}
      onMouseEnter={(e) => {
        e.currentTarget.style.borderColor = error || active ? borderColor : 'var(--border-strong)'
      }}
      onMouseLeave={(e) => {
        e.currentTarget.style.borderColor = borderColor
      }}
    >
      <RefreshCw
        size={12}
        strokeWidth={1.5}
        style={iconSpinning ? { animation: spinAnimation } : undefined}
      />
      {error ? t('admin.retry') : t('admin.live')}
    </button>
  )
}
