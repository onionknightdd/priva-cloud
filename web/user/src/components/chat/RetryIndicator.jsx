import { useEffect } from 'react'
import { RefreshCw, Check } from 'lucide-react'
import { useAnimatedNumber } from '@shared/motion/useAnimatedNumber'
import useChatStore from '../../stores/chatStore'

// Countdown ticks roll to the next second instead of snapping. Mounts snap
// (first paint), so the banner appears already showing the full delay.
function CountdownSeconds({ seconds }) {
  const shown = useAnimatedNumber(seconds, { decimals: 0 })
  return <>{shown}s</>
}

export default function RetryIndicator() {
  const retryState = useChatStore((s) => s.retryState)
  const tickRetryDelay = useChatStore((s) => s.tickRetryDelay)

  useEffect(() => {
    if (!retryState) return undefined
    const id = setInterval(tickRetryDelay, 1000)
    return () => clearInterval(id)
  }, [retryState, tickRetryDelay])

  if (!retryState) return null

  const { attempt, max, delaySeconds, message, succeeded } = retryState
  const accent = succeeded ? 'var(--green)' : 'var(--yellow)'
  const Icon = succeeded ? Check : RefreshCw
  return (
    <div
      style={{
        background: 'var(--bg-elevated)',
        borderLeft: `2px solid ${accent}`,
        borderRadius: 2,
        padding: '8px 10px',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        transition: 'border-color 150ms ease',
      }}
    >
      <div className="flex items-center gap-2">
        <Icon
          size={12}
          strokeWidth={1.5}
          className={succeeded ? undefined : 'icon-running'}
          style={{ color: accent, flexShrink: 0 }}
        />
        <span
          className="text-xs"
          style={{
            color: 'var(--text-secondary)',
            fontStyle: succeeded ? 'normal' : 'italic',
          }}
        >
          {succeeded
            ? 'Reconnect successful'
            : (delaySeconds > 0 ? 'Retrying in ' : 'Retrying ')}
          {!succeeded && (
            <span
              style={{
                fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                fontStyle: 'normal',
                color: 'var(--text-primary)',
                fontVariantNumeric: 'tabular-nums',
              }}
            >
              {delaySeconds > 0 ? <CountdownSeconds seconds={delaySeconds} /> : 'now'}
            </span>
          )}
          {' · attempt '}
          <span
            style={{
              fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
              fontStyle: 'normal',
              color: 'var(--text-primary)',
            }}
          >
            {attempt}/{max}
          </span>
        </span>
      </div>
      {message && !succeeded && (
        <div
          className="text-xs"
          style={{
            color: 'var(--text-dim)',
            fontStyle: 'italic',
            paddingLeft: 18,
            wordBreak: 'break-word',
          }}
        >
          {message}
        </div>
      )}
    </div>
  )
}
