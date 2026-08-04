import { Cpu, MemoryStick, Server } from 'lucide-react'
import { useTranslation } from 'react-i18next'


function percentText(value) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  const number = Number(value)
  return `${number.toFixed(number >= 100 ? 0 : 1)}%`
}


function rowStatusColor(metric, available) {
  if (!available) return 'var(--yellow)'
  if (metric?.remaining < 0 || (metric?.overcommit_percent ?? 0) > 0) return 'var(--red)'
  if ((metric?.allocation_percent ?? 0) >= 80) return 'var(--yellow)'
  return 'var(--green)'
}


function barFillColor(metric) {
  if ((metric?.overcommit_percent ?? 0) > 0) return 'var(--red)'
  if ((metric?.allocation_percent ?? 0) >= 80) return 'var(--yellow)'
  return 'var(--blue)'
}


function MetricCell({ label, value, color = 'var(--text-primary)' }) {
  return (
    <div className="flex flex-col min-w-0">
      <span
        className="text-xs uppercase truncate"
        style={{ color: 'var(--text-dim)', fontWeight: 600, letterSpacing: '0.06em' }}
      >
        {label}
      </span>
      <span
        className="text-base font-semibold truncate"
        style={{
          color,
          fontFamily: "'JetBrains Mono', monospace",
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </span>
    </div>
  )
}


function CapacityRow({ icon: Icon, label, metric, formatValue, available, t }) {
  const statusColor = rowStatusColor(metric, available)
  const allocation = metric?.allocation_percent
  const fillPercent = available && allocation != null
    ? Math.min(100, Math.max(0, Number(allocation)))
    : 0
  const valueOrDash = (value) => (available ? formatValue(value) : '—')
  const ratioColor = !available
    ? 'var(--text-dim)'
    : (metric?.overcommit_percent ?? 0) > 0
      ? 'var(--red)'
      : (metric?.allocation_percent ?? 0) >= 80
        ? 'var(--yellow)'
        : 'var(--text-primary)'

  return (
    <div
      style={{
        display: 'grid',
        gridTemplateColumns: '72px minmax(0, 1fr)',
        columnGap: 12,
        rowGap: 10,
        padding: '12px 16px 12px 14px',
        borderLeft: `2px solid ${statusColor}`,
        borderTop: '1px solid var(--border-subtle)',
        minWidth: 0,
      }}
    >
      <div
        className="flex items-center gap-2 uppercase"
        style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}
      >
        <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
        <span className="truncate">{label}</span>
      </div>

      <div
        className="grid gap-3 min-w-0"
        style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))' }}
      >
        <MetricCell
          label={t('admin.capacityRemaining')}
          value={valueOrDash(metric?.remaining ?? 0)}
          color={available && (metric?.remaining ?? 0) < 0 ? 'var(--red)' : 'var(--text-primary)'}
        />
        <MetricCell
          label={t('admin.capacityAllocated')}
          value={formatValue(metric?.allocated ?? 0)}
        />
        <MetricCell
          label={t('admin.capacityAssignable')}
          value={valueOrDash(metric?.assignable ?? 0)}
        />
        <MetricCell
          label={t('admin.capacityAllocationRate')}
          value={available ? percentText(metric?.allocation_percent) : '—'}
          color={ratioColor}
        />
        <MetricCell
          label={t('admin.capacityOvercommitRate')}
          value={available ? percentText(metric?.overcommit_percent) : '—'}
          color={available && (metric?.overcommit_percent ?? 0) > 0 ? 'var(--red)' : 'var(--text-primary)'}
        />
      </div>

      <span aria-hidden="true" />
      <div
        role="img"
        aria-label={t('admin.capacityBarAria', {
          resource: label,
          percent: available ? percentText(metric?.allocation_percent) : t('admin.unavailableShort'),
        })}
        style={{
          height: 8,
          minWidth: 0,
          background: 'var(--bg-base)',
          border: '1px solid var(--border)',
          borderRadius: 2,
          overflow: 'hidden',
        }}
      >
        <div
          style={{
            width: `${fillPercent}%`,
            height: '100%',
            background: barFillColor(metric),
            transition: 'width 200ms cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        />
      </div>
    </div>
  )
}


function ClusterCapacitySkeleton() {
  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        marginBottom: 16,
        overflow: 'hidden',
      }}
    >
      <div className="flex items-center justify-between gap-3" style={{ padding: '14px 16px' }}>
        <div className="skeleton" style={{ width: 176, height: 12 }} />
        <div className="skeleton" style={{ width: 108, height: 11 }} />
      </div>
      {[1, 2].map((row) => (
        <div
          key={row}
          style={{
            display: 'grid',
            gridTemplateColumns: '72px minmax(0, 1fr)',
            gap: 12,
            padding: '12px 16px',
            borderLeft: '2px solid var(--border)',
            borderTop: '1px solid var(--border-subtle)',
          }}
        >
          <div className="skeleton" style={{ width: 52, height: 12 }} />
          <div className="grid gap-3" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(96px, 1fr))' }}>
            {[1, 2, 3, 4, 5].map((cell) => (
              <div key={cell} className="flex flex-col gap-1">
                <div className="skeleton" style={{ width: '70%', height: 10 }} />
                <div className="skeleton" style={{ width: '86%', height: 14 }} />
              </div>
            ))}
          </div>
          <span />
          <div className="skeleton" style={{ width: '100%', height: 8 }} />
        </div>
      ))}
    </div>
  )
}


export default function ClusterCapacityCard({ capacity, loading, error }) {
  const { t } = useTranslation()
  if (loading && !capacity) return <ClusterCapacitySkeleton />

  const available = !!capacity?.available
  const stale = !!error && !!capacity
  const timestamp = capacity?.scraped_at
    ? new Date(capacity.scraped_at * 1000).toLocaleTimeString([], { hour12: false })
    : '—'
  const formatCpu = (millicores) => `${(Number(millicores || 0) / 1000).toFixed(2)} ${t('admin.cpuCoreUnit')}`
  const formatMemory = (memoryMb) => `${(Number(memoryMb || 0) / 1024).toFixed(2)} Gi`

  return (
    <div
      style={{
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        marginBottom: 16,
        overflow: 'hidden',
        minWidth: 0,
      }}
    >
      <div
        className="flex items-center justify-between gap-3"
        style={{ padding: '12px 16px', minWidth: 0, flexWrap: 'wrap' }}
      >
        <div className="flex items-center gap-2 min-w-0">
          <Server size={14} strokeWidth={1.5} className="flex-shrink-0" />
          <span
            className="uppercase font-semibold truncate"
            style={{ color: 'var(--text-secondary)', fontSize: 11, letterSpacing: '0.06em' }}
          >
            {t('admin.clusterCapacityTitle')}
          </span>
        </div>
        <div
          className="flex items-center gap-2 text-xs uppercase flex-shrink-0"
          style={{ color: stale || !available ? 'var(--yellow)' : 'var(--text-dim)', letterSpacing: '0.06em' }}
        >
          <span>{available
            ? t('admin.eligibleNodes', { eligible: capacity?.eligible_nodes ?? 0, total: capacity?.total_nodes ?? 0 })
            : t('admin.capacityUnavailable')}</span>
          <span>·</span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontVariantNumeric: 'tabular-nums' }}>
            {timestamp}
          </span>
        </div>
      </div>

      <CapacityRow
        icon={Cpu}
        label="CPU"
        metric={capacity?.cpu}
        formatValue={formatCpu}
        available={available}
        t={t}
      />
      <CapacityRow
        icon={MemoryStick}
        label={t('admin.memory')}
        metric={capacity?.memory}
        formatValue={formatMemory}
        available={available}
        t={t}
      />

      <div
        className="flex items-center gap-2 text-xs"
        style={{
          padding: '9px 16px',
          color: stale || !available ? 'var(--yellow)' : 'var(--text-dim)',
          fontWeight: 300,
          borderTop: '1px solid var(--border-subtle)',
          overflowWrap: 'break-word',
          flexWrap: 'wrap',
        }}
      >
        <span>{stale
          ? t('admin.capacityStale')
          : available
            ? t('admin.capacityFormula')
            : t('admin.capacityUnavailableHint')}</span>
        {available && <><span>·</span><span>{t('admin.activeQuotaAccounts', { count: capacity?.active_accounts ?? 0 })}</span></>}
        {available && (capacity?.pending_non_runner_pods ?? 0) > 0 && (
          <><span>·</span><span style={{ color: 'var(--yellow)' }}>{t('admin.pendingFixedPods', { count: capacity.pending_non_runner_pods })}</span></>
        )}
      </div>
    </div>
  )
}
