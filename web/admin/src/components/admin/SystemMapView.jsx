import { useEffect, useRef } from 'react'
import { Network, RefreshCw } from 'lucide-react'
import { useReducedMotion } from 'framer-motion'
import useAdminStore from '../../stores/adminStore'
import SystemTopologyDiagram from './SystemTopologyDiagram'

// System Map — Dashboard view. Live topology + per-module health from
// /api/admin/system-health on its own 5s poll. Edges are curved bezier routes; the
// byte-path edges animate a constant particle flow while healthy, freeze + show an
// ✕ when unreachable, and are fully disabled under prefers-reduced-motion.

const POLL_MS = 5000

// Skeleton matching the diagram footprint (1000×700 → 70% aspect) on first load.
function DiagramSkeleton() {
  return (
    <div className="w-full" style={{ minWidth: 0 }}>
      <div className="skeleton" style={{ width: '100%', paddingTop: '70%', borderRadius: 4 }} />
      <div className="flex items-center gap-4" style={{ marginTop: 12 }}>
        {[1, 2, 3, 4, 5].map((i) => (
          <div key={i} className="skeleton" style={{ width: 64, height: 11 }} />
        ))}
      </div>
    </div>
  )
}

export default function SystemMapView() {
  const reducedMotion = useReducedMotion()
  const systemHealth = useAdminStore((s) => s.systemHealth)
  const systemHealthLoading = useAdminStore((s) => s.systemHealthLoading)
  const systemHealthRefreshing = useAdminStore((s) => s.systemHealthRefreshing)
  const systemHealthError = useAdminStore((s) => s.systemHealthError)
  const fetchSystemHealth = useAdminStore((s) => s.fetchSystemHealth)

  const fetchRef = useRef(fetchSystemHealth)
  fetchRef.current = fetchSystemHealth

  useEffect(() => {
    const poll = () => fetchRef.current()
    poll()
    const pid = setInterval(poll, POLL_MS)
    return () => clearInterval(pid)
  }, [])

  const initialLoad = systemHealthLoading && !systemHealth

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0" style={{ padding: '20px 24px 0 24px' }}>
        <div>
          <h2 className="font-semibold text-lg flex items-center gap-2" style={{ color: 'var(--text-primary)', margin: 0 }}>
            <Network size={18} strokeWidth={1.5} />
            System Map
          </h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            Live module topology and health across the four planes. Byte-path edges flow while healthy; an ✕ marks an unreachable path.
          </p>
        </div>
        <button
          className="flex items-center gap-2 px-2 py-1 text-xs uppercase flex-shrink-0"
          onClick={() => fetchSystemHealth()}
          title="Refresh now"
          style={{
            background: 'transparent',
            border: '1px solid var(--border)',
            borderRadius: 4,
            color: systemHealthError ? 'var(--red)' : 'var(--text-secondary)',
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
            style={systemHealthRefreshing ? { animation: 'fleet-spin 1s linear infinite' } : undefined}
          />
          {systemHealthError ? 'Retry' : 'Live'}
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 24px 24px' }}>
        <div style={{ maxWidth: 1100, margin: '0 auto' }}>
          {initialLoad ? (
            <DiagramSkeleton />
          ) : (
            <SystemTopologyDiagram data={systemHealth} reducedMotion={reducedMotion} />
          )}
        </div>
      </div>

      <style>{`@keyframes fleet-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
