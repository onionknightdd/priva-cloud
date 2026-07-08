import { useEffect, useRef } from 'react'
import { Wifi, WifiOff, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useOverlayTransition from '@shared/motion/useOverlayTransition'
import useConnectionStore from '../../stores/connectionStore'

export default function ConnectionBanner() {
  const { t } = useTranslation()
  const state = useConnectionStore((s) => s.state)
  const attempt = useConnectionStore((s) => s.attempt)
  const maxAttempts = useConnectionStore((s) => s.maxAttempts)
  const delaySeconds = useConnectionStore((s) => s.delaySeconds)
  const tickDelay = useConnectionStore((s) => s.tickDelay)

  useEffect(() => {
    if (state !== 'reconnecting') return undefined
    const id = setInterval(tickDelay, 1000)
    return () => clearInterval(id)
  }, [state, tickDelay])

  // Exit animation: the store resets state/attempt data the instant the
  // socket comes back, so render the closing frames from a snapshot while
  // the banner slides out.
  const open = state !== 'connected'
  const { mounted, panelRef } = useOverlayTransition({ open, variant: 'slide' })
  const shownRef = useRef(null)
  if (open) shownRef.current = { state, attempt, maxAttempts, delaySeconds }
  const shown = shownRef.current

  if (!mounted || !shown) return null

  const isReconnecting = shown.state === 'reconnecting'
  const color = isReconnecting ? 'var(--yellow)' : 'var(--red)'
  const Icon = isReconnecting ? Wifi : WifiOff

  return (
    <div
      ref={panelRef}
      role="status"
      style={{
        position: 'sticky',
        top: 0,
        zIndex: 100,
        background: 'var(--bg-surface)',
        borderBottom: '1px solid var(--border)',
        borderLeft: `2px solid ${color}`,
        padding: '6px 12px',
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      <Icon size={14} strokeWidth={1.5} style={{ color }} />
      <span
        className="uppercase font-semibold"
        style={{ color: 'var(--text-primary)', fontSize: 11, letterSpacing: '0.06em' }}
      >
        {isReconnecting ? t('connection.reconnecting') : t('connection.disconnected')}
      </span>
      {isReconnecting && (
        <span
          className="text-xs"
          style={{
            color: 'var(--text-secondary)',
            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          }}
        >
          {t('connection.attempt', { attempt: shown.attempt, max: shown.maxAttempts || '?' })}
          {shown.delaySeconds > 0 ? ` · ${t('connection.inSeconds', { seconds: shown.delaySeconds })}` : ''}
        </span>
      )}
      {!isReconnecting && (
        <>
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
            {t('connection.dropped')}
          </span>
          <button
            type="button"
            className="flex items-center gap-1 text-xs"
            style={{
              marginLeft: 'auto',
              padding: '2px 8px',
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 2,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'border-color 150ms ease, color 150ms ease',
            }}
            onClick={() => window.location.reload()}
            onMouseEnter={(e) => {
              e.currentTarget.style.borderColor = 'var(--border-strong)'
              e.currentTarget.style.color = 'var(--text-primary)'
            }}
            onMouseLeave={(e) => {
              e.currentTarget.style.borderColor = 'var(--border)'
              e.currentTarget.style.color = 'var(--text-secondary)'
            }}
          >
            <RefreshCw size={12} strokeWidth={1.5} />
            {t('connection.reload')}
          </button>
        </>
      )}
    </div>
  )
}
