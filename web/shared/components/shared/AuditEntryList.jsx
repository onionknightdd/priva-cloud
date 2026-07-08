import { memo, useMemo, useRef, useState, useId } from 'react'
import { ChevronDown } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useVirtualizer } from '@tanstack/react-virtual'
import { AnimatedChevron, AnimatedCollapse } from './Accordion'
import { useStaggerEntrance } from '../../motion/useStaggerEntrance'

function getActionBorderColor(action) {
  if (action === 'login.success' || action === 'user.created') return 'var(--green)'
  if (action === 'login.failed' || action === 'user.deleted' || action === 'user.apikey_revoked') return 'var(--red)'
  if (action === 'user.role_changed' || action === 'user.password_reset') return 'var(--yellow)'
  if (action?.startsWith('tool.')) return 'var(--cyan)'
  if (action?.startsWith('skill.')) return 'var(--purple)'
  return 'var(--blue)'
}

function formatDescription(entry, t) {
  const { action, actor, target, details } = entry
  switch (action) {
    case 'login.success':
      return t('admin.auditLoginSuccess', { actor })
    case 'login.failed':
      return t('admin.auditLoginFailed', { target })
    case 'user.created':
      return t('admin.auditUserCreated', { actor, target, role: details?.role || 'user' })
    case 'user.deleted':
      return t('admin.auditUserDeleted', { actor, target })
    case 'user.role_changed':
      return t('admin.auditRoleChanged', { actor, target, oldRole: details?.old_role, newRole: details?.new_role })
    case 'user.password_reset':
      return t('admin.auditPasswordReset', { actor, target })
    case 'user.apikey_generated':
      return t('admin.auditApikeyGenerated', { actor, target })
    case 'user.apikey_revoked':
      return t('admin.auditApikeyRevoked', { actor, target })
    case 'session.deleted':
      return t('admin.auditSessionDeleted', { actor, target })
    default:
      return `${actor}: ${action}${target ? ` → ${target}` : ''}`
  }
}

function relativeTime(dateStr, t) {
  if (!dateStr) return ''
  const now = Date.now()
  const diff = now - new Date(dateStr).getTime()
  const minutes = Math.floor(diff / 60000)
  if (minutes < 1) return t('admin.justNow')
  if (minutes < 60) return t('admin.minutesAgo', { count: minutes })
  const hours = Math.floor(minutes / 60)
  if (hours < 24) return t('admin.hoursAgo', { count: hours })
  const days = Math.floor(hours / 24)
  return t('admin.daysAgo', { count: days })
}

function DetailsBlock({ details }) {
  const [expanded, setExpanded] = useState(false)
  const [everExpanded, setEverExpanded] = useState(false)
  const bodyId = useId()
  // Serialize lazily: rows render by the hundred and most are never expanded.
  const json = useMemo(
    () => (everExpanded && details ? JSON.stringify(details, null, 2) : null),
    [everExpanded, details]
  )
  if (!details || Object.keys(details).length === 0) return null

  return (
    <div>
      <button
        className="flex items-center gap-1 text-xs"
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          padding: '2px 0',
          transition: 'color 150ms ease',
        }}
        onClick={() => { setEverExpanded(true); setExpanded(!expanded) }}
        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
        aria-expanded={expanded}
        aria-controls={bodyId}
      >
        <AnimatedChevron open={expanded}>
          <ChevronDown size={12} strokeWidth={1.5} />
        </AnimatedChevron>
        details
      </button>
      <AnimatedCollapse open={expanded} id={bodyId}>
        <pre
          style={{
            background: 'var(--bg-elevated)',
            borderRadius: '2px',
            padding: '8px 12px',
            margin: '4px 0 0 0',
            fontSize: '11px',
            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
            color: 'var(--text-secondary)',
            overflowX: 'auto',
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
          }}
        >
          {json}
        </pre>
      </AnimatedCollapse>
    </div>
  )
}

const AuditEntryRow = memo(function AuditEntryRow({ entry, entranceKey, entranceRef }) {
  const { t } = useTranslation()
  return (
    <div
      // Entrance animates this inner div — the virtualizer's absolutely
      // positioned shell must never move.
      ref={entranceRef ? entranceRef(entranceKey) : undefined}
      className="flex flex-col gap-1 px-4 py-3"
      style={{
        borderLeft: `2px solid ${getActionBorderColor(entry.action)}`,
        borderBottom: '1px solid var(--border)',
        transition: 'background 150ms ease',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
    >
      <div className="flex items-center justify-between">
        <span
          className="text-xs uppercase"
          style={{ color: getActionBorderColor(entry.action), letterSpacing: '0.06em' }}
        >
          {entry.action}
        </span>
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
          {relativeTime(entry.timestamp, t)}
        </span>
      </div>
      <span className="text-sm" style={{ color: 'var(--text-secondary)' }}>
        {formatDescription(entry, t)}
      </span>
      <DetailsBlock details={entry.details} />
    </div>
  )
})

// Virtualized audit feed shared by the admin and user audit logs. Owns its
// scroll container (pass sizing via className/style); "load more" appends the
// backing array unboundedly, so only the visible window mounts.
export default function AuditEntryList({ entries, hasMore = false, loading = false, onLoadMore, className = '', style }) {
  const { t } = useTranslation()
  const scrollRef = useRef(null)
  const entranceRef = useStaggerEntrance()
  const virtualizer = useVirtualizer({
    count: entries.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 84,
    overscan: 10,
    getItemKey: (i) => entries[i].id ?? `${entries[i].timestamp ?? ''}-${i}`,
  })

  return (
    <div ref={scrollRef} className={className} style={{ overflowAnchor: 'none', ...style }}>
      <div style={{ height: virtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
        {virtualizer.getVirtualItems().map((vi) => (
          <div
            key={vi.key}
            data-index={vi.index}
            ref={virtualizer.measureElement}
            style={{
              position: 'absolute',
              top: vi.start,
              left: 0,
              width: '100%',
            }}
          >
            <AuditEntryRow entry={entries[vi.index]} entranceKey={vi.key} entranceRef={entranceRef} />
          </div>
        ))}
      </div>

      {hasMore && (
        <button
          className="px-4 py-2 text-xs w-full"
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--blue)',
            cursor: loading ? 'not-allowed' : 'pointer',
            opacity: loading ? 0.5 : 1,
            textAlign: 'center',
            transition: 'opacity 150ms ease',
          }}
          disabled={loading}
          onClick={onLoadMore}
        >
          {loading ? t('sidebar.loading') : t('sidebar.loadMore')}
        </button>
      )}
    </div>
  )
}
