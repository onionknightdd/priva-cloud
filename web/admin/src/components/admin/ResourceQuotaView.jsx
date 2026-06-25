import { useEffect, useRef, useState } from 'react'
import { Cpu, MemoryStick, HardDrive, RefreshCw, Gauge } from 'lucide-react'
import Chip from '@shared/components/shared/Chip'
import useAdminStore from '../../stores/adminStore'

// Resource Quota — Dashboard section. Live agent-runtime CPU/memory usage vs the
// allocated per-account quota, from /api/admin/resource-usage. Two fleet-wide
// usage bars (CPU, MEMORY) + a per-account breakdown table. Polls every POLL_MS;
// the skeleton shows only on the first load — background polls update in place.
// Status via a 2px left border, never dots. metrics-server unavailable → bars '—'.

const POLL_MS = 5000

// Runner type → chip color (same convention as UserManagement): persistent
// (always-on) gets a distinct semantic color from auto_scale.
function runnerColor(type) {
  return type === 'persistent' ? 'var(--orange)' : 'var(--cyan)'
}

// Utilization → bar color: calm blue until it gets tight, then warn, then critical.
function barColor(pct) {
  if (pct >= 90) return 'var(--red)'
  if (pct >= 75) return 'var(--yellow)'
  return 'var(--blue)'
}

const fmtCpu = (m) => `${Math.round(m)}m`
const fmtMem = (mb) => `${Math.round(mb)}Mi`
const fmtVol = (g) => {
  const n = Number(g ?? 0)
  return `${Number.isInteger(n) ? n : n.toFixed(2)}Gi`
}

function pctOf(used, allocated) {
  if (!allocated || allocated <= 0) return 0
  return Math.min(100, (used / allocated) * 100)
}

// One fleet-wide usage bar: label · track with a filled portion · used/allocated + pct.
// available=false renders a single '—' instead of a misleading empty bar.
function UsageBar({ icon: Icon, label, used, allocated, fmt, available }) {
  const pct = pctOf(used, allocated)
  return (
    <div className="flex items-center gap-3" style={{ minWidth: 0 }}>
      <div
        className="flex items-center gap-2 uppercase flex-shrink-0"
        style={{ width: 88, color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}
      >
        <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      {!available ? (
        <span className="font-bold flex-1" style={{ color: 'var(--text-dim)', fontSize: 16 }}>—</span>
      ) : (
        <>
          <div
            className="flex-1"
            style={{ minWidth: 0, height: 8, background: 'var(--bg-base)', border: '1px solid var(--border)', borderRadius: 2, overflow: 'hidden' }}
          >
            <div
              style={{
                width: `${pct}%`, height: '100%', background: barColor(pct),
                transition: 'width 200ms cubic-bezier(0.16, 1, 0.3, 1)',
              }}
            />
          </div>
          <div
            className="flex items-baseline gap-2 flex-shrink-0"
            style={{ width: 188, justifyContent: 'flex-end', fontFamily: "'JetBrains Mono', monospace" }}
          >
            <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
              {fmt(used)} <span style={{ color: 'var(--text-dim)' }}>/ {fmt(allocated)}</span>
            </span>
            <span className="text-xs font-light" style={{ width: 44, textAlign: 'right', color: 'var(--text-secondary)' }}>
              {pct.toFixed(pct < 10 ? 1 : 0)}%
            </span>
          </div>
        </>
      )}
    </div>
  )
}

function SummarySkeleton() {
  return (
    <div className="flex flex-col gap-3" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '16px 18px' }}>
      <div className="skeleton" style={{ width: 120, height: 11 }} />
      {[1, 2].map((i) => (
        <div key={i} className="flex items-center gap-3">
          <div className="skeleton flex-shrink-0" style={{ width: 88, height: 11 }} />
          <div className="skeleton flex-1" style={{ height: 8 }} />
          <div className="skeleton flex-shrink-0" style={{ width: 188, height: 13 }} />
        </div>
      ))}
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="flex flex-col">
      {[1, 2, 3].map((i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3" style={{ borderLeft: '2px solid transparent' }}>
          <div className="skeleton flex-1" style={{ height: 13, maxWidth: 180 }} />
          <div className="skeleton" style={{ width: 88, height: 13 }} />
          <div className="skeleton" style={{ width: 96, height: 13 }} />
          <div className="skeleton" style={{ width: 112, height: 13 }} />
          <div className="skeleton" style={{ width: 104, height: 13 }} />
        </div>
      ))}
    </div>
  )
}

export default function ResourceQuotaView() {
  const usage = useAdminStore((s) => s.resourceUsage)
  const loading = useAdminStore((s) => s.resourceUsageLoading)
  const refreshing = useAdminStore((s) => s.resourceUsageRefreshing)
  const error = useAdminStore((s) => s.resourceUsageError)
  const fetchResourceUsage = useAdminStore((s) => s.fetchResourceUsage)

  const fetchRef = useRef(fetchResourceUsage)
  fetchRef.current = fetchResourceUsage

  useEffect(() => {
    const poll = () => fetchRef.current()
    poll()
    const pid = setInterval(poll, POLL_MS)
    return () => clearInterval(pid)
  }, [])

  const accounts = usage?.accounts || []
  const available = !!usage?.available
  // Volume usage has its own source (the quota-manager), independent of metrics-server:
  // available when any account reports a backend usage figure.
  const volAvailable = accounts.some((a) => a.volume_used_gb != null)
  const initialLoad = loading && !usage

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0" style={{ padding: '20px 24px 0 24px' }}>
        <div>
          <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>Resource Quota</h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            Agent-runtime CPU, memory and volume consumption vs allocated quota, across all accounts.
          </p>
        </div>
        <button
          className="flex items-center gap-2 px-2 py-1 text-xs uppercase flex-shrink-0"
          onClick={() => fetchResourceUsage()}
          title="Refresh now"
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 4,
            color: error ? 'var(--red)' : 'var(--text-secondary)',
            cursor: 'pointer',
            letterSpacing: '0.06em',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <RefreshCw size={12} strokeWidth={1.5} style={refreshing ? { animation: 'rq-spin 1s linear infinite' } : undefined} />
          {error ? 'Retry' : 'Live'}
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 24px 24px' }}>
        {/* Fleet-wide summary card */}
        {initialLoad ? (
          <SummarySkeleton />
        ) : (
          <div className="flex flex-col gap-3" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '16px 18px' }}>
            <div className="flex items-center gap-2 uppercase" style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}>
              <Gauge size={14} strokeWidth={1.5} className="flex-shrink-0" />
              <span className="truncate">Agent Runtime</span>
            </div>
            <UsageBar icon={Cpu} label="CPU" used={usage?.cpu_used_m ?? 0} allocated={usage?.cpu_allocated_m ?? 0} fmt={fmtCpu} available={available} />
            <UsageBar icon={MemoryStick} label="Memory" used={usage?.memory_used_mb ?? 0} allocated={usage?.memory_allocated_mb ?? 0} fmt={fmtMem} available={available} />
            <UsageBar icon={HardDrive} label="Volume" used={usage?.volume_used_gb ?? 0} allocated={usage?.volume_allocated_gb ?? 0} fmt={fmtVol} available={volAvailable} />
            <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--text-dim)', fontWeight: 300 }}>
              <span style={{ color: 'var(--green)' }}>{usage?.awake ?? 0} awake</span>
              <span>·</span>
              <span>{usage?.sleeping ?? 0} sleeping</span>
              <span>·</span>
              <span>{usage?.total_accounts ?? 0} accounts</span>
              {!available && (
                <>
                  <span>·</span>
                  <span style={{ color: 'var(--yellow)' }}>metrics-server unavailable</span>
                </>
              )}
              {!volAvailable && (
                <>
                  <span>·</span>
                  <span style={{ color: 'var(--yellow)' }}>volume usage unavailable</span>
                </>
              )}
            </div>
          </div>
        )}

        {/* Per-account table */}
        <div style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
          <div
            className="flex items-center gap-3 px-4 py-2 text-xs uppercase"
            style={{ background: 'var(--bg-surface)', color: 'var(--text-secondary)', letterSpacing: '0.06em', borderBottom: '1px solid var(--border)' }}
          >
            <span className="flex-1 min-w-0">Account</span>
            <span style={{ width: 96, flexShrink: 0 }}>Runner</span>
            <span style={{ width: 112, flexShrink: 0, textAlign: 'right' }}>CPU</span>
            <span style={{ width: 132, flexShrink: 0, textAlign: 'right' }}>Memory</span>
            <span style={{ width: 104, flexShrink: 0, textAlign: 'right' }}>Vol</span>
          </div>

          {initialLoad ? (
            <TableSkeleton />
          ) : accounts.length === 0 ? (
            <div className="flex items-center justify-center" style={{ padding: '32px 16px', color: 'var(--text-dim)', fontSize: 13 }}>
              No agent-runner accounts yet.
            </div>
          ) : (
            accounts.map((a) => (
              <div
                key={a.account_id}
                className="flex items-center gap-3 px-4 py-2"
                style={{ borderBottom: '1px solid var(--border-subtle)', borderLeft: `2px solid ${a.awake ? 'var(--green)' : 'var(--status-idle)'}` }}
              >
                <span className="flex flex-col flex-1 min-w-0">
                  <span className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
                    {a.username || a.account_id}
                  </span>
                  {a.username && (
                    <span className="text-xs truncate" style={{ color: 'var(--text-dim)', fontWeight: 300, fontFamily: "'JetBrains Mono', monospace" }}>
                      {a.account_id}
                    </span>
                  )}
                </span>
                <span style={{ width: 96, flexShrink: 0 }}>
                  <Chip color={runnerColor(a.runner_type)}>{(a.runner_type || 'auto_scale').toUpperCase()}</Chip>
                </span>
                <span className="text-sm" style={{ width: 112, flexShrink: 0, textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                  <span style={{ color: (available && a.awake) ? 'var(--text-primary)' : 'var(--text-dim)' }}>
                    {available && a.awake ? fmtCpu(a.cpu_used_m) : '—'}
                  </span>
                  <span style={{ color: 'var(--text-dim)' }}>/{fmtCpu(a.cpu_allocated_m)}</span>
                </span>
                <span className="text-sm" style={{ width: 132, flexShrink: 0, textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                  <span style={{ color: (available && a.awake) ? 'var(--text-primary)' : 'var(--text-dim)' }}>
                    {available && a.awake ? fmtMem(a.memory_used_mb) : '—'}
                  </span>
                  <span style={{ color: 'var(--text-dim)' }}>/{fmtMem(a.memory_allocated_mb)}</span>
                </span>
                <span className="text-sm" style={{ width: 104, flexShrink: 0, textAlign: 'right', fontFamily: "'JetBrains Mono', monospace" }}>
                  <span style={{ color: a.volume_used_gb != null ? 'var(--text-primary)' : 'var(--text-dim)' }}>
                    {a.volume_used_gb != null ? fmtVol(a.volume_used_gb) : '—'}
                  </span>
                  <span style={{ color: 'var(--text-dim)' }}>/{a.volume_gb}Gi</span>
                </span>
              </div>
            ))
          )}
        </div>
      </div>

      <style>{`@keyframes rq-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
