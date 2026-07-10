import { useTranslation } from 'react-i18next'

// Session-list status dot (replaces the old MessageSquare icon):
//   purple = running · orange = needs a decision · green = finished unseen ·
//   gray = finished & seen · invisible placeholder when idle (keeps names aligned).
const DOT_COLORS = {
  running: 'var(--purple)',
  attention: 'var(--orange)',
  unseen: 'var(--green)',
  seen: 'var(--text-dim)',
}

const DOT_LABEL_KEYS = {
  running: 'sidebar.statusRunning',
  attention: 'sidebar.statusAttention',
  unseen: 'sidebar.statusCompleted',
  seen: 'sidebar.statusSeen',
}

export default function SessionStatusDot({ status, size = 7 }) {
  const { t } = useTranslation()
  const color = DOT_COLORS[status] || null
  return (
    <span
      title={color ? t(DOT_LABEL_KEYS[status]) : undefined}
      aria-hidden={color ? undefined : true}
      style={{
        width: size,
        height: size,
        // Approved design-spec exception: ≤18px circular indicators may be round.
        borderRadius: '50%',
        background: color || 'transparent',
        display: 'inline-block',
        flexShrink: 0,
        transition: 'background 150ms ease',
      }}
    />
  )
}
