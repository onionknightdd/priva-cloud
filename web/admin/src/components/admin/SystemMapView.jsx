import { useEffect, useRef, useState } from 'react'
import { Network } from 'lucide-react'
import { useReducedMotion } from 'framer-motion'
import { useTranslation } from 'react-i18next'
import useAdminStore from '../../stores/adminStore'
import LiveToggleButton from './LiveToggleButton'
import SystemTopologyDiagram from './SystemTopologyDiagram'

// System Topology — Dashboard view. Live topology + per-module health from
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
  const { t } = useTranslation()
  const reducedMotion = useReducedMotion()
  const systemHealth = useAdminStore((s) => s.systemHealth)
  const systemHealthLoading = useAdminStore((s) => s.systemHealthLoading)
  const systemHealthRefreshing = useAdminStore((s) => s.systemHealthRefreshing)
  const systemHealthError = useAdminStore((s) => s.systemHealthError)
  const fetchSystemHealth = useAdminStore((s) => s.fetchSystemHealth)
  const [liveEnabled, setLiveEnabled] = useState(true)

  const fetchRef = useRef(fetchSystemHealth)
  fetchRef.current = fetchSystemHealth

  useEffect(() => {
    if (!liveEnabled) return undefined
    const poll = () => fetchRef.current()
    poll()
    const pid = setInterval(poll, POLL_MS)
    return () => clearInterval(pid)
  }, [liveEnabled])

  const initialLoad = systemHealthLoading && !systemHealth
  const handleLiveToggle = () => {
    if (systemHealthError) {
      setLiveEnabled(true)
      fetchSystemHealth()
      return
    }
    setLiveEnabled((enabled) => {
      const next = !enabled
      if (next) fetchSystemHealth()
      return next
    })
  }

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0" style={{ padding: '20px 24px 0 24px' }}>
        <div>
          <h2 className="font-semibold text-lg flex items-center gap-2" style={{ color: 'var(--text-primary)', margin: 0 }}>
            <Network size={18} strokeWidth={1.5} />
            {t('admin.systemTopologyTitle')}
          </h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            {t('admin.systemTopologyDescription')}
          </p>
        </div>
        <LiveToggleButton
          active={liveEnabled && !systemHealthError}
          error={!!systemHealthError}
          refreshing={systemHealthRefreshing}
          onClick={handleLiveToggle}
          spinAnimation="fleet-spin 1s linear infinite"
        />
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 24px 24px' }}>
        <div style={{ width: '100%', minWidth: 0 }}>
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
