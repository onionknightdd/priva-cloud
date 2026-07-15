import { useEffect } from 'react'
import UsageStatsOverview from '../chat/UsageStatsOverview'
import useUserDataStore from '../../stores/userDataStore'

export default function UserUsage() {
  const stats = useUserDataStore((state) => state.stats)
  const statsLoading = useUserDataStore((state) => state.statsLoading)
  const fetchStats = useUserDataStore((state) => state.fetchStats)

  useEffect(() => { fetchStats() }, [fetchStats])

  return (
    <div className="flex flex-col flex-1 overflow-y-auto" style={{ padding: '36px 56px 48px', minHeight: 0 }}>
      <section className="min-w-0" style={{ width: '100%', maxWidth: 840, margin: '0 auto' }}>
        <UsageStatsOverview
          showTitle={false}
          workspaceStats={stats}
          workspaceStatsLoading={statsLoading}
        />
      </section>
    </div>
  )
}
