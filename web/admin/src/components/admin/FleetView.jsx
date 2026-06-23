import { useEffect, useRef, useState } from 'react'
import { Server, Activity, RefreshCw } from 'lucide-react'
import useAdminStore from '../../stores/adminStore'

// Fleet — Dashboard overview. Live agent-runner snapshot from /api/admin/fleet:
// two summary tiles (awake sandboxes, running sessions) + a per-account table.
// Polls every POLL_MS; the skeleton only shows on the first load — background
// polls update in place. Status is shown via a 2px left border, never dots.

const POLL_MS = 5000

// State derived from the operator phase + the pod's in-flight runs. Colors come
// from the design-spec status palette (running=purple, online=green, waking=yellow,
// idle=neutral border) and drive both the row's left border and the label.
function stateOf(acct) {
  if (acct.phase === 'Waking') return { label: 'WAKING', color: 'var(--status-pending)' }
  if (acct.awake) {
    if ((acct.active_runs || 0) > 0) return { label: 'RUNNING', color: 'var(--status-running)' }
    return { label: 'ONLINE', color: 'var(--green)' }
  }
  return { label: 'IDLE', color: 'var(--status-idle)' }
}

function relTime(ts) {
  if (!ts) return '—'
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (secs < 5) return 'just now'
  if (secs < 60) return `${secs}s ago`
  const mins = Math.floor(secs / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  return `${Math.floor(hrs / 24)}d ago`
}

function Tile({ icon: Icon, label, value, suffix }) {
  return (
    <div
      className="flex flex-col gap-2"
      style={{
        flex: 1,
        minWidth: 0,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '16px 18px',
      }}
    >
      <div className="flex items-center gap-2 uppercase" style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}>
        <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="font-bold" style={{ color: 'var(--text-primary)', fontSize: 28, lineHeight: 1 }}>{value}</span>
        {suffix != null && (
          <span style={{ color: 'var(--text-dim)', fontSize: 16, fontWeight: 300 }}>/ {suffix}</span>
        )}
      </div>
    </div>
  )
}

function TilesSkeleton() {
  return (
    <div className="flex gap-3">
      {[1, 2].map((i) => (
        <div key={i} style={{ flex: 1, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '16px 18px' }}>
          <div className="skeleton" style={{ width: 120, height: 11, marginBottom: 12 }} />
          <div className="skeleton" style={{ width: 64, height: 28 }} />
        </div>
      ))}
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="flex flex-col">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3" style={{ borderLeft: '2px solid transparent' }}>
          <div className="skeleton flex-1" style={{ height: 13, maxWidth: 180 }} />
          <div className="skeleton" style={{ width: 64, height: 13 }} />
          <div className="skeleton" style={{ width: 28, height: 13 }} />
          <div className="skeleton" style={{ width: 72, height: 13 }} />
        </div>
      ))}
    </div>
  )
}

export default function FleetView() {
  const fleet = useAdminStore((s) => s.fleet)
  const fleetLoading = useAdminStore((s) => s.fleetLoading)
  const fleetRefreshing = useAdminStore((s) => s.fleetRefreshing)
  const fleetError = useAdminStore((s) => s.fleetError)
  const fetchFleet = useAdminStore((s) => s.fetchFleet)

  // Re-render once a second so relative timestamps ("12s ago") stay fresh between polls.
  const [, setTick] = useState(0)
  const fetchRef = useRef(fetchFleet)
  fetchRef.current = fetchFleet

  useEffect(() => {
    fetchRef.current()
    const poll = setInterval(() => fetchRef.current(), POLL_MS)
    const tick = setInterval(() => setTick((n) => n + 1), 1000)
    return () => { clearInterval(poll); clearInterval(tick) }
  }, [])

  const accounts = fleet?.accounts || []
  const initialLoad = fleetLoading && !fleet

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0" style={{ padding: '20px 24px 0 24px' }}>
        <div>
          <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>Fleet</h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            Live agent-runner sandboxes and in-flight agent sessions across all accounts.
          </p>
        </div>
        <button
          className="flex items-center gap-2 px-2 py-1 text-xs uppercase flex-shrink-0"
          onClick={() => fetchFleet()}
          title="Refresh now"
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 4,
            color: fleetError ? 'var(--red)' : 'var(--text-secondary)',
            cursor: 'pointer',
            letterSpacing: '0.06em',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <RefreshCw
            size={12}
            strokeWidth={1.5}
            style={fleetRefreshing ? { animation: 'fleet-spin 1s linear infinite' } : undefined}
          />
          {fleetError ? 'Retry' : 'Live'}
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 24px 24px' }}>
        {/* Tiles */}
        {initialLoad ? (
          <TilesSkeleton />
        ) : (
          <div className="flex gap-3">
            <Tile icon={Server} label="Awake Sandboxes" value={fleet?.awake_sandboxes ?? 0} suffix={fleet?.total_accounts ?? 0} />
            <Tile icon={Activity} label="Running Sessions" value={fleet?.running_sessions ?? 0} />
          </div>
        )}

        {/* Per-account table */}
        <div style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
          {/* Header row */}
          <div
            className="flex items-center gap-3 px-4 py-2 text-xs uppercase"
            style={{
              background: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              letterSpacing: '0.06em',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <span className="flex-1 min-w-0">Account</span>
            <span style={{ width: 80, flexShrink: 0 }}>State</span>
            <span style={{ width: 48, flexShrink: 0, textAlign: 'right' }}>Runs</span>
            <span style={{ width: 96, flexShrink: 0, textAlign: 'right' }}>Last Activity</span>
          </div>

          {initialLoad ? (
            <TableSkeleton />
          ) : accounts.length === 0 ? (
            <div className="flex items-center justify-center" style={{ padding: '32px 16px', color: 'var(--text-dim)', fontSize: 13 }}>
              No agent-runner accounts yet.
            </div>
          ) : (
            accounts.map((a) => {
              const st = stateOf(a)
              const runs = a.active_runs == null ? '—' : a.active_runs
              return (
                <div
                  key={a.account_id}
                  className="flex items-center gap-3 px-4 py-2"
                  style={{
                    borderBottom: '1px solid var(--border-subtle)',
                    borderLeft: `2px solid ${st.color}`,
                  }}
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
                  <span
                    className="uppercase"
                    style={{ width: 80, flexShrink: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', color: st.label === 'IDLE' ? 'var(--text-dim)' : st.color }}
                  >
                    {st.label}
                  </span>
                  <span
                    className="text-sm"
                    style={{ width: 48, flexShrink: 0, textAlign: 'right', color: a.active_runs ? 'var(--text-primary)' : 'var(--text-dim)', fontFamily: "'JetBrains Mono', monospace" }}
                  >
                    {runs}
                  </span>
                  <span className="text-xs font-light" style={{ width: 96, flexShrink: 0, textAlign: 'right', color: 'var(--text-dim)' }}>
                    {relTime(a.last_activity_ts)}
                  </span>
                </div>
              )
            })
          )}
        </div>
      </div>

      <style>{`@keyframes fleet-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
