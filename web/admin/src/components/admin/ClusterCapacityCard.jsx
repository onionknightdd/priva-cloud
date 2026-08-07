import { useState } from 'react'
import { ChevronDown, ChevronRight, Cpu, MemoryStick, Server } from 'lucide-react'
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
    <div className="flex flex-col min-w-0" style={{ textAlign: 'right' }}>
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
          fontFamily: 'var(--font-code)',
          fontVariantNumeric: 'tabular-nums',
        }}
      >
        {value}
      </span>
    </div>
  )
}


const NODE_REASON_KEYS = {
  eligible: 'admin.nodeEligible',
  not_ready: 'admin.nodeNotReady',
  cordoned: 'admin.nodeCordoned',
  untolerated_taint: 'admin.nodeUntoleratedTaint',
}


function nodeStatusColor(node) {
  if (node?.eligible) return 'var(--green)'
  if (node?.eligibility_reason === 'not_ready') return 'var(--red)'
  return 'var(--yellow)'
}


function NodeDetailCell({
  label,
  value,
  color = 'var(--text-primary)',
  wrap = false,
  align = 'right',
}) {
  return (
    <div className="flex flex-col min-w-0" style={{ textAlign: align }}>
      <span
        className="text-xs uppercase"
        style={{
          color: 'var(--text-dim)',
          fontWeight: 600,
          letterSpacing: '0.06em',
          overflowWrap: 'break-word',
        }}
      >
        {label}
      </span>
      <span
        className="text-sm font-semibold"
        style={{
          color,
          fontFamily: 'var(--font-code)',
          fontVariantNumeric: 'tabular-nums',
          overflowWrap: 'break-word',
          wordBreak: wrap ? 'break-word' : 'normal',
        }}
      >
        {value}
      </span>
    </div>
  )
}


function NodeBreakdown({ id, nodes, resourceKey, formatValue, t }) {
  return (
    <div
      id={id}
      style={{
        padding: '10px 12px 12px',
        background: 'var(--bg-base)',
        borderTop: '1px solid var(--border-subtle)',
        minWidth: 0,
      }}
    >
      <div
        className="flex items-center justify-between gap-3 text-xs uppercase"
        style={{
          color: 'var(--text-dim)',
          fontWeight: 600,
          letterSpacing: '0.06em',
          marginBottom: 8,
          flexWrap: 'wrap',
        }}
      >
        <span>{t('admin.perNodeView')}</span>
        <span>{t('admin.nodeCount', { count: nodes.length })}</span>
      </div>

      <div className="flex flex-col gap-2 min-w-0">
        {nodes.map((node) => {
          const resource = node?.[resourceKey] || {}
          const remaining = resource.current_remaining
          const statusKey = NODE_REASON_KEYS[node?.eligibility_reason] || 'admin.nodeUnavailable'
          return (
            <div
              key={node.name}
              style={{
                padding: '9px 10px',
                background: 'var(--bg-surface)',
                border: '1px solid var(--border-subtle)',
                borderLeft: `2px solid ${nodeStatusColor(node)}`,
                borderRadius: 2,
                minWidth: 0,
              }}
            >
              <div
                className="grid gap-3 min-w-0"
                style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(112px, 1fr))' }}
              >
                <NodeDetailCell label={t('admin.nodeName')} value={node.name} wrap align="left" />
                <NodeDetailCell
                  label={t('admin.nodeStatus')}
                  value={t(statusKey)}
                  color={nodeStatusColor(node)}
                  wrap
                  align="left"
                />
                <NodeDetailCell
                  label={t('admin.nodeAllocatable')}
                  value={formatValue(resource.allocatable ?? 0)}
                />
                <NodeDetailCell
                  label={t('admin.nodeFixedRequests')}
                  value={formatValue(resource.non_runner_requested ?? 0)}
                />
                <NodeDetailCell
                  label={t('admin.nodeRuntimeRequests')}
                  value={formatValue(resource.runtime_requested ?? 0)}
                  color="var(--cyan)"
                />
                <NodeDetailCell
                  label={t('admin.nodeCurrentRemaining')}
                  value={remaining == null ? '—' : formatValue(remaining)}
                  color={remaining == null
                    ? 'var(--text-dim)'
                    : remaining < 0
                      ? 'var(--red)'
                      : 'var(--text-primary)'}
                />
              </div>
            </div>
          )
        })}
      </div>

      <div
        className="text-xs"
        style={{
          color: 'var(--text-dim)',
          fontWeight: 400,
          marginTop: 8,
          overflowWrap: 'break-word',
        }}
      >
        {t('admin.nodeCapacityHint')}
      </div>
    </div>
  )
}


function CapacityRow({
  icon: Icon,
  label,
  metric,
  formatValue,
  available,
  nodes,
  resourceKey,
  expanded,
  onToggle,
  t,
}) {
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
  const canExpand = available && nodes.length > 0
  const detailId = `cluster-capacity-nodes-${resourceKey}`

  return (
    <div
      style={{
        borderLeft: `2px solid ${statusColor}`,
        borderTop: '1px solid var(--border-subtle)',
        minWidth: 0,
      }}
    >
      <div
        style={{
          display: 'grid',
          gridTemplateColumns: '88px minmax(0, 1fr)',
          columnGap: 12,
          rowGap: 10,
          padding: '12px 16px 12px 14px',
          minWidth: 0,
        }}
      >
        <button
          type="button"
          className="capacity-expand-button flex items-center gap-1 uppercase min-w-0"
          onClick={onToggle}
          disabled={!canExpand}
          aria-expanded={canExpand ? expanded : false}
          aria-controls={detailId}
          aria-label={t(expanded ? 'admin.collapseNodeView' : 'admin.expandNodeView', { resource: label })}
          style={{
            color: canExpand ? 'var(--text-secondary)' : 'var(--text-dim)',
            background: 'transparent',
            border: 'none',
            padding: 0,
            cursor: canExpand ? 'pointer' : 'default',
            fontFamily: 'var(--font-ui)',
            fontSize: 11,
            fontWeight: 600,
            letterSpacing: '0.06em',
            textAlign: 'left',
            transition: 'color 150ms ease',
          }}
        >
          {expanded && canExpand
            ? <ChevronDown size={14} strokeWidth={1.5} className="flex-shrink-0" />
            : <ChevronRight size={14} strokeWidth={1.5} className="flex-shrink-0" />}
          <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
          <span className="truncate">{label}</span>
        </button>

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

        <div
          aria-hidden="true"
        />
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

      {expanded && canExpand && (
        <NodeBreakdown
          id={detailId}
          nodes={nodes}
          resourceKey={resourceKey}
          formatValue={formatValue}
          t={t}
        />
      )}
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
  const [expandedResources, setExpandedResources] = useState({ cpu: false, memory: false })
  if (loading && !capacity) return <ClusterCapacitySkeleton />

  const available = !!capacity?.available
  const stale = !!error && !!capacity
  const timestamp = capacity?.scraped_at
    ? new Date(capacity.scraped_at * 1000).toLocaleTimeString([], { hour12: false })
    : '—'
  const formatCpu = (millicores) => `${(Number(millicores || 0) / 1000).toFixed(2)} ${t('admin.cpuCoreUnit')}`
  const formatMemory = (memoryMb) => `${(Number(memoryMb || 0) / 1024).toFixed(2)} Gi`
  const nodes = capacity?.nodes || []
  const toggleResource = (resource) => {
    setExpandedResources((current) => ({ ...current, [resource]: !current[resource] }))
  }

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
          <span style={{ fontFamily: 'var(--font-code)', fontVariantNumeric: 'tabular-nums' }}>
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
        nodes={nodes}
        resourceKey="cpu"
        expanded={expandedResources.cpu}
        onToggle={() => toggleResource('cpu')}
        t={t}
      />
      <CapacityRow
        icon={MemoryStick}
        label={t('admin.memory')}
        metric={capacity?.memory}
        formatValue={formatMemory}
        available={available}
        nodes={nodes}
        resourceKey="memory"
        expanded={expandedResources.memory}
        onToggle={() => toggleResource('memory')}
        t={t}
      />

      <div
        className="flex items-center gap-2 text-xs"
        style={{
          padding: '9px 16px',
          color: stale || !available ? 'var(--yellow)' : 'var(--text-dim)',
          fontWeight: 400,
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
      <style>{`
        .capacity-expand-button:hover:not(:disabled) {
          color: var(--text-primary) !important;
        }
      `}</style>
    </div>
  )
}
