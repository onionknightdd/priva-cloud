import { useEffect, useMemo, useState } from 'react'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useStaggerEntrance } from '@shared/motion/useStaggerEntrance'
import useSidebarStore from '../../stores/sidebarStore'
import useUserDataStore from '../../stores/userDataStore'
import { openSession } from '../../session/openSession'

function compactRelativeTime(value) {
  if (!value) return ''
  const ms = Number(value) < 1_000_000_000_000 ? Number(value) * 1000 : Number(value)
  if (!Number.isFinite(ms)) return ''
  const diff = Math.max(0, Date.now() - ms)
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  const month = 30 * day
  const year = 365 * day
  if (diff < minute) return 'now'
  if (diff < hour) return `${Math.floor(diff / minute)}m`
  if (diff < day) return `${Math.floor(diff / hour)}h`
  if (diff < month) return `${Math.floor(diff / day)}d`
  if (diff < year) return `${Math.floor(diff / month)}mo`
  return `${Math.floor(diff / year)}y`
}

function cwdLabel(cwd) {
  if (!cwd) return ''
  const parts = String(cwd).split('/').filter(Boolean)
  return parts.at(-1) || cwd
}

function shortSessionId(id) {
  if (!id) return ''
  return String(id).slice(0, 8)
}

function buildRows(recentActivities, sessions) {
  const byId = new Map(sessions.map((s) => [s.sessionId || s.id, s]))
  return (recentActivities || []).slice(0, 5).map((activity) => {
    const sessionId = activity.session_id || activity.sessionId
    const session = byId.get(sessionId)
    const cwd = session?.cwd || activity.cwd || ''
    const title = session?.customTitle || session?.firstPrompt || session?.name || activity.title || cwdLabel(cwd) || shortSessionId(sessionId)
    const recap = activity.recap || session?.recap || ''
    const lastModified = session?.createdAt || activity.last_modified || activity.lastModified || null
    return {
      id: sessionId || `${cwd}:${title}`,
      sessionId,
      title,
      recap,
      time: compactRelativeTime(lastModified),
    }
  })
}

function RecentActivitySkeleton() {
  return (
    <div className="flex flex-col" style={{ gap: 6 }}>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="skeleton"
          style={{
            height: 32,
            borderRadius: 4,
          }}
        />
      ))}
    </div>
  )
}

export default function RecentActivities({ showTitle = true }) {
  const { t } = useTranslation()
  const recentActivities = useSidebarStore((s) => s.recentActivities)
  const sessions = useSidebarStore((s) => s.sessions)
  const dismissRecentActivity = useSidebarStore((s) => s.dismissRecentActivity)
  const overviewLoading = useUserDataStore((s) => s.overviewLoading)
  const fetchOverview = useUserDataStore((s) => s.fetchOverview)
  const [loadingSessionId, setLoadingSessionId] = useState(null)
  const [dismissingSessionId, setDismissingSessionId] = useState(null)
  const entranceRef = useStaggerEntrance({ duration: 220, rise: 6, stepMs: 35 })

  // The overview bootstrap hydrates recent_activities. Keep this dependency
  // local now that the home screen no longer mounts UsageStatsOverview.
  useEffect(() => { fetchOverview() }, [fetchOverview])

  const rows = useMemo(
    () => buildRows(recentActivities, sessions),
    [recentActivities, sessions]
  )

  const openActivity = async (row) => {
    if (!row.sessionId || loadingSessionId || dismissingSessionId) return
    setLoadingSessionId(row.sessionId)
    try {
      await openSession(row.sessionId)
    } catch (err) {
      console.error('Failed to load recent activity:', err)
    } finally {
      setLoadingSessionId(null)
    }
  }

  const closeActivity = async (row) => {
    if (!row.sessionId || loadingSessionId || dismissingSessionId) return
    setDismissingSessionId(row.sessionId)
    await dismissRecentActivity(row.sessionId)
    setDismissingSessionId(null)
  }

  return (
    <div className="flex flex-col min-w-0" style={{ gap: 5 }}>
      {showTitle && (
        <div
          className="font-semibold"
          style={{
            color: 'var(--text-primary)',
            fontSize: 11,
            lineHeight: 1.3,
          }}
        >
          Recent activities
        </div>
      )}

      <div
        className="flex flex-col min-w-0"
        style={{
          gap: 5,
        }}
      >
        {overviewLoading && rows.length === 0 ? (
          <RecentActivitySkeleton />
        ) : rows.length === 0 ? (
          <div
            className="text-xs"
            style={{
              color: 'var(--text-dim)',
              padding: '14px 8px',
              textAlign: 'center',
            }}
          >
            No recent activity
          </div>
        ) : (
          rows.map((row) => {
            const loading = loadingSessionId === row.sessionId
            const dismissing = dismissingSessionId === row.sessionId
            return (
              <div
                key={row.id}
                ref={entranceRef(row.id)}
                className="flex items-center min-w-0"
                style={{
                  minHeight: 32,
                  width: '100%',
                  background: 'var(--bg-elevated)',
                  borderRadius: 4,
                  color: 'var(--text-secondary)',
                  transition: 'background 150ms ease, color 150ms ease',
                }}
                onMouseEnter={(event) => {
                  event.currentTarget.style.background = 'var(--bg-surface)'
                  event.currentTarget.style.color = 'var(--text-primary)'
                }}
                onMouseLeave={(event) => {
                  event.currentTarget.style.background = 'var(--bg-elevated)'
                  event.currentTarget.style.color = 'var(--text-secondary)'
                }}
              >
                <button
                  type="button"
                  disabled={loading || dismissing}
                  onClick={() => openActivity(row)}
                  className="flex items-center gap-2 min-w-0 flex-1"
                  style={{
                    alignSelf: 'stretch',
                    padding: '5px 4px 5px 8px',
                    background: 'transparent',
                    border: 'none',
                    color: 'inherit',
                    cursor: loading || dismissing ? 'default' : 'pointer',
                    textAlign: 'left',
                  }}
                >
                  <div className="flex items-center min-w-0 flex-1" style={{ gap: 5 }}>
                    <span
                      className="truncate"
                      style={{
                        color: 'var(--text-primary)',
                        fontSize: 11,
                        fontWeight: 500,
                        maxWidth: '38%',
                        minWidth: 0,
                      }}
                      title={row.title}
                    >
                      {row.title}
                    </span>
                    {row.recap && (
                      <span
                        className="truncate"
                        style={{
                          color: 'var(--text-secondary)',
                          fontSize: 10,
                          fontWeight: 300,
                          minWidth: 0,
                          flex: 1,
                        }}
                        title={row.recap}
                      >
                        {row.recap}
                      </span>
                    )}
                  </div>
                  {row.time && (
                    <span
                      style={{
                        color: 'var(--text-dim)',
                        fontSize: 9,
                        fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                        flexShrink: 0,
                      }}
                    >
                      {row.time}
                    </span>
                  )}
                </button>
                <button
                  type="button"
                  disabled={loading || dismissing}
                  onClick={() => closeActivity(row)}
                  title={t('chat.recentActivityDismiss')}
                  aria-label={t('chat.recentActivityDismiss')}
                  className="flex-shrink-0 inline-flex items-center justify-center"
                  style={{
                    alignSelf: 'stretch',
                    padding: '0 8px 0 4px',
                    background: 'transparent',
                    border: 'none',
                    color: 'var(--text-dim)',
                    cursor: loading || dismissing ? 'default' : 'pointer',
                    transition: 'color 150ms ease',
                  }}
                  onMouseEnter={(event) => {
                    event.currentTarget.style.color = 'var(--text-primary)'
                  }}
                  onMouseLeave={(event) => {
                    event.currentTarget.style.color = 'var(--text-dim)'
                  }}
                >
                  <X size={12} strokeWidth={1.5} />
                </button>
              </div>
            )
          })
        )}
      </div>
    </div>
  )
}
