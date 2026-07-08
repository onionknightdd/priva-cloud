import { useState } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useListLifecycle, LifecycleItem } from '@shared/motion/ListLifecycle'
import useChatStore from '../../stores/chatStore'

function QueuedMessageRow({ entry }) {
  const { t } = useTranslation()
  const [hovered, setHovered] = useState(false)
  const removeQueuedMessage = useChatStore((s) => s.removeQueuedMessage)
  const queueSender = useChatStore((s) => s.queueSender)

  const handleCancel = () => {
    removeQueuedMessage(entry.id)
    if (queueSender?.sendQueueCancel) queueSender.sendQueueCancel(entry.id)
  }

  return (
    <div
      className="flex items-center gap-2 px-3 py-1 overflow-hidden"
      style={{
        borderLeft: '2px solid var(--status-pending)',
        background: 'var(--bg-surface)',
        color: 'var(--text-secondary)',
        borderRadius: 2,
        minWidth: 0,
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      <span
        className="uppercase flex-shrink-0"
        style={{
          color: 'var(--yellow)',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
        }}
      >
        {t('chat.queuedLabel')}
      </span>
      <span className="flex-shrink-0" style={{ color: 'var(--text-dim)', fontSize: 12 }}>
        {'\u203A'}
      </span>
      <span
        className="truncate flex-1"
        style={{ fontSize: 13, minWidth: 0, color: 'var(--text-secondary)' }}
      >
        {entry.text}
      </span>
      <button
        className="flex items-center justify-center flex-shrink-0"
        style={{
          background: 'transparent',
          border: 'none',
          cursor: 'pointer',
          color: 'var(--text-dim)',
          opacity: hovered ? 1 : 0,
          transition: 'opacity 150ms ease, color 150ms ease',
          padding: 2,
        }}
        onClick={handleCancel}
        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
        title={t('chat.cancelQueued')}
      >
        <X size={14} strokeWidth={1.5} />
      </button>
    </div>
  )
}

export default function QueuedMessagesStack({ entries, style }) {
  // Real exits: sent/cancelled entries fade + collapse, survivors slide up.
  // Exiting entries are retained by the lifecycle map, so keep rendering
  // until the last exit finishes (not until `entries` empties).
  const [lifecycleEntries, removeExited] = useListLifecycle(entries || [], (e) => e.id)
  if (!lifecycleEntries.length) return null

  const hasLive = Boolean(entries?.length)

  return (
    <div className="overflow-hidden" style={style}>
      {lifecycleEntries.map(({ key, item, present }) => (
        <LifecycleItem
          key={key}
          present={present}
          onExited={() => removeExited(key)}
          // Spacing lives inside the collapsing shell (a row margin would
          // leave a 4px hole while the exit height animates to 0).
          style={{ paddingTop: 4 }}
        >
          <QueuedMessageRow entry={item} />
        </LifecycleItem>
      ))}
      {/* Hint collapses in step with the last row instead of popping away. */}
      <LifecycleItem present={hasLive} onExited={() => {}} style={{ paddingTop: 2 }}>
        <div
          className="px-3 py-1"
          style={{
            color: 'var(--text-dim)',
            fontSize: 11,
            fontWeight: 300,
          }}
        >
          {'\u2192 will send after next tool returns'}
        </div>
      </LifecycleItem>
    </div>
  )
}
