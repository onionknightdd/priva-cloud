import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { useAnimatedNumber } from '@shared/motion/useAnimatedNumber'
import useUserDataStore from '../../stores/userDataStore'

function formatBytes(bytes) {
  if (bytes === 0) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.floor(Math.log(bytes) / Math.log(1024))
  return `${(bytes / Math.pow(1024, i)).toFixed(i > 0 ? 1 : 0)} ${units[i]}`
}

function relativeTime(dateStr, t) {
  if (!dateStr) return t('admin.never')
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

function CardSkeleton() {
  return (
    <div
      className="flex flex-col gap-2 px-5 py-4"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '4px', minWidth: 160 }}
    >
      <div className="skeleton" style={{ width: 80, height: 11 }} />
      <div className="skeleton" style={{ width: 60, height: 20 }} />
    </div>
  )
}

function StatCard({ label, value, format }) {
  // Refetches tween numeric values to the new reading (formatter applied per
  // frame); first paint snaps. Non-numeric values (relative time) render as-is.
  const isNumeric = typeof value === 'number' && Number.isFinite(value)
  const animatedValue = useAnimatedNumber(isNumeric ? value : 0)
  const shown = isNumeric
    ? (format ? format(animatedValue) : animatedValue)
    : value
  return (
    <div
      className="flex flex-col gap-1 px-5 py-4"
      style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: '4px', minWidth: 160 }}
    >
      <span className="text-xs uppercase font-semibold" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
        {label}
      </span>
      <span className="text-lg font-bold" style={{ color: 'var(--text-primary)', fontVariantNumeric: 'tabular-nums' }}>
        {shown}
      </span>
    </div>
  )
}

export default function UserUsage() {
  const { t } = useTranslation()
  const stats = useUserDataStore((s) => s.stats)
  const statsLoading = useUserDataStore((s) => s.statsLoading)
  const fetchStats = useUserDataStore((s) => s.fetchStats)

  useEffect(() => { fetchStats() }, [])

  return (
    <div className="flex flex-col gap-4 flex-1 overflow-y-auto" style={{ padding: '24px 56px 32px', minHeight: 0 }}>
      <div className="flex items-stretch gap-4 flex-wrap">
        {statsLoading ? (
          <>
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
            <CardSkeleton />
          </>
        ) : stats ? (
          <>
            <StatCard label={t('userData.sessions')} value={stats.session_count} />
            <StatCard label={t('userData.storage')} value={stats.storage_bytes} format={formatBytes} />
            <StatCard label={t('userData.files')} value={stats.file_count} />
            <StatCard label={t('userData.totalFileSize')} value={stats.total_file_size} format={formatBytes} />
            <StatCard label={t('userData.lastActive')} value={relativeTime(stats.last_active, t)} />
          </>
        ) : null}
      </div>
    </div>
  )
}
