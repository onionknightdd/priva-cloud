import { useEffect, useState, useRef, useCallback, useMemo } from 'react'
import { Search, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUserDataStore from '../../stores/userDataStore'
import AuditCharts from '@shared/components/admin/charts/AuditCharts'
import DateRangePicker from '@shared/components/shared/DateRangePicker'
import Dropdown from '@shared/components/shared/Dropdown'
import AuditEntryList from '@shared/components/shared/AuditEntryList'

const FILTER_CATEGORIES = [
  { value: '', labelKey: 'admin.filterAll', filterField: 'target' },
  { value: 'login', labelKey: 'admin.filterLogin', filterField: 'target' },
  { value: 'user', labelKey: 'admin.filterUser', filterField: 'target' },
  { value: 'session', labelKey: 'admin.filterSession', filterField: 'session_id' },
  { value: 'skill', labelKey: 'admin.filterSkill', filterField: 'target' },
  { value: 'tool', labelKey: 'admin.filterTool', filterField: 'target' },
]

function useDebouncedCallback(callback, delay) {
  const timerRef = useRef(null)
  const callbackRef = useRef(callback)
  callbackRef.current = callback

  return useCallback((...args) => {
    if (timerRef.current) clearTimeout(timerRef.current)
    timerRef.current = setTimeout(() => callbackRef.current(...args), delay)
  }, [delay])
}

function EntrySkeleton() {
  return (
    <div className="flex flex-col gap-4">
      {[1, 2, 3, 4, 5].map((i) => (
        <div
          key={i}
          className="flex flex-col gap-1 px-4 py-3"
          style={{ borderLeft: '2px solid var(--border)' }}
        >
          <div className="flex items-center justify-between">
            <div className="skeleton" style={{ width: 120, height: 11 }} />
            <div className="skeleton" style={{ width: 60, height: 11 }} />
          </div>
          <div className="skeleton" style={{ width: '70%', height: 13 }} />
        </div>
      ))}
    </div>
  )
}

export default function UserAuditLog() {
  const { t } = useTranslation()
  const auditEntries = useUserDataStore((s) => s.auditEntries)
  const cpAuditEntries = useUserDataStore((s) => s.cpAuditEntries)
  const auditTotal = useUserDataStore((s) => s.auditTotal)
  const auditNextCursor = useUserDataStore((s) => s.auditNextCursor)
  const auditLoading = useUserDataStore((s) => s.auditLoading)
  const auditActionFilter = useUserDataStore((s) => s.auditActionFilter)
  const auditTargetFilter = useUserDataStore((s) => s.auditTargetFilter)
  const auditSessionFilter = useUserDataStore((s) => s.auditSessionFilter)
  const fetchAuditLog = useUserDataStore((s) => s.fetchAuditLog)
  const setAuditActionFilter = useUserDataStore((s) => s.setAuditActionFilter)
  const setAuditTargetFilter = useUserDataStore((s) => s.setAuditTargetFilter)
  const setAuditSessionFilter = useUserDataStore((s) => s.setAuditSessionFilter)
  const auditChartEntries = useUserDataStore((s) => s.auditChartEntries)
  const auditChartLoading = useUserDataStore((s) => s.auditChartLoading)
  const fetchAuditLogForCharts = useUserDataStore((s) => s.fetchAuditLogForCharts)
  const auditStartTime = useUserDataStore((s) => s.auditStartTime)
  const auditEndTime = useUserDataStore((s) => s.auditEndTime)
  const setAuditTimeRange = useUserDataStore((s) => s.setAuditTimeRange)
  const filterActive = !!(auditActionFilter || auditTargetFilter || auditSessionFilter || auditStartTime || auditEndTime)

  // Determine which filter field the current category uses
  const activeCategory = FILTER_CATEGORIES.find((c) => c.value === (auditActionFilter || '')) || FILTER_CATEGORIES[0]
  const isSessionCategory = activeCategory.filterField === 'session_id'
  const localFilterValue = isSessionCategory ? auditSessionFilter : auditTargetFilter

  const [localValue, setLocalValue] = useState(localFilterValue)
  const debouncedSetValue = useDebouncedCallback((v) => {
    if (isSessionCategory) {
      setAuditSessionFilter(v)
      setAuditTargetFilter('')
    } else {
      setAuditTargetFilter(v)
      setAuditSessionFilter('')
    }
  }, 300)

  // Sync local input when category changes
  useEffect(() => {
    setLocalValue(isSessionCategory ? auditSessionFilter : auditTargetFilter)
  }, [auditActionFilter])

  useEffect(() => { fetchAuditLog(false) }, [auditActionFilter, auditTargetFilter, auditSessionFilter, auditStartTime, auditEndTime])
  useEffect(() => { fetchAuditLogForCharts() }, [auditActionFilter, auditTargetFilter, auditSessionFilter, auditStartTime, auditEndTime])

  const hasMore = !!auditNextCursor

  // Merge the agent-runtime feed (paginated, runner) with the bounded
  // control-plane feed (login/auth, control-panel) by timestamp. The two stores
  // are disjoint, so no dedupe is needed; "load more" extends the agent feed.
  const mergedEntries = useMemo(
    () => [...auditEntries, ...cpAuditEntries]
      .sort((a, b) => new Date(b.timestamp) - new Date(a.timestamp)),
    [auditEntries, cpAuditEntries],
  )

  const handleRefresh = () => {
    fetchAuditLog(false)
    fetchAuditLogForCharts()
  }

  return (
    <div className="flex flex-col flex-1" style={{ padding: '24px 56px 0 56px', minHeight: 0, overflow: 'hidden' }}>
      {/* Two-column: entries left, chart right */}
      <div className="flex gap-5 flex-1" style={{ minHeight: 0, overflow: 'hidden' }}>
        {/* Left column */}
        <div className="flex flex-col flex-1" style={{ minWidth: 0, minHeight: 0 }}>
          {/* Pinned filters */}
          <div className="flex flex-col gap-3 flex-shrink-0 pb-3" style={{ width: 'fit-content' }}>
            {/* Time range filter */}
            <div className="flex items-center gap-3">
              <DateRangePicker
                startTime={auditStartTime}
                endTime={auditEndTime}
                onChange={(start, end) => setAuditTimeRange(start, end)}
              />
              <button
                style={{
                  background: 'transparent',
                  border: 'none',
                  cursor: auditLoading ? 'not-allowed' : 'pointer',
                  color: 'var(--text-dim)',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 4,
                  borderRadius: 4,
                  transition: 'color 150ms ease',
                }}
                disabled={auditLoading}
                onClick={handleRefresh}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                title={t('scheduler.reload')}
              >
                <RefreshCw size={14} strokeWidth={1.5} />
              </button>
            </div>

            {/* FILTER BY: [dropdown] : [value] */}
            <div className="flex items-center gap-2">
              <span
                className="text-xs uppercase font-semibold flex-shrink-0"
                style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}
              >
                {t('admin.filterBy')}
              </span>

              {/* Category dropdown */}
              <Dropdown
                size="sm"
                options={FILTER_CATEGORIES.map((c) => ({ value: c.value, label: t(c.labelKey) }))}
                value={auditActionFilter || ''}
                onChange={(val) => {
                  setAuditActionFilter(val || null)
                  setAuditTargetFilter('')
                  setAuditSessionFilter('')
                  setLocalValue('')
                }}
              />

              <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-dim)' }}>:</span>

              {/* Value input */}
              <div
                className="flex items-center gap-2 px-3 py-1"
                style={{
                  background: 'var(--bg-surface)',
                  border: '1px solid var(--border)',
                  borderRadius: '4px',
                  flex: 1,
                }}
              >
                <Search size={14} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
                <input
                  className="flex-1"
                  placeholder={isSessionCategory ? t('admin.searchSession') : t('admin.searchTarget')}
                  value={localValue}
                  onChange={(e) => {
                    setLocalValue(e.target.value)
                    debouncedSetValue(e.target.value)
                  }}
                  style={{
                    background: 'transparent',
                    border: 'none',
                    outline: 'none',
                    color: 'var(--text-primary)',
                    fontSize: 13,
                    minWidth: 0,
                    padding: '2px 0',
                  }}
                />
              </div>
            </div>
          </div>

          {/* Scrollable entries list */}
          {auditLoading && mergedEntries.length === 0 ? (
            <div className="flex-1 overflow-y-auto" style={{ minHeight: 0, paddingBottom: 32 }}>
              <EntrySkeleton />
            </div>
          ) : mergedEntries.length === 0 ? (
            <div className="flex-1 overflow-y-auto" style={{ minHeight: 0, paddingBottom: 32 }}>
              <div className="text-sm" style={{ color: 'var(--text-dim)', padding: '20px 0' }}>
                {t('admin.noAuditEntries')}
              </div>
            </div>
          ) : (
            <AuditEntryList
              className="flex-1 overflow-y-auto"
              style={{ minHeight: 0, paddingBottom: 32 }}
              entries={mergedEntries}
              hasMore={hasMore}
              loading={auditLoading}
              onLoadMore={() => fetchAuditLog(true)}
            />
          )}
        </div>

        {/* Right column: chart */}
        <div className="flex-1" style={{ minWidth: 0, borderLeft: '1px solid var(--border)', paddingLeft: 20, overflowY: 'auto' }}>
          <AuditCharts entries={auditChartEntries} loading={auditChartLoading} />
        </div>
      </div>
    </div>
  )
}
