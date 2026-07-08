import { useEffect, useState, useRef, useCallback } from 'react'
import { Search, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useAdminStore from '../../stores/adminStore'
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
  { value: '_actor', labelKey: 'admin.filterActor', filterField: 'actor' },
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

export default function AuditLog() {
  const { t } = useTranslation()
  const auditEntries = useAdminStore((s) => s.auditEntries)
  const auditNextCursor = useAdminStore((s) => s.auditNextCursor)
  const auditLoading = useAdminStore((s) => s.auditLoading)
  const auditActionFilter = useAdminStore((s) => s.auditActionFilter)
  const auditActorFilter = useAdminStore((s) => s.auditActorFilter)
  const auditTargetFilter = useAdminStore((s) => s.auditTargetFilter)
  const auditSessionFilter = useAdminStore((s) => s.auditSessionFilter)
  const fetchAuditLog = useAdminStore((s) => s.fetchAuditLog)
  const setAuditActionFilter = useAdminStore((s) => s.setAuditActionFilter)
  const setAuditActorFilter = useAdminStore((s) => s.setAuditActorFilter)
  const setAuditTargetFilter = useAdminStore((s) => s.setAuditTargetFilter)
  const setAuditSessionFilter = useAdminStore((s) => s.setAuditSessionFilter)
  const auditChartEntries = useAdminStore((s) => s.auditChartEntries)
  const auditChartLoading = useAdminStore((s) => s.auditChartLoading)
  const fetchAuditLogForCharts = useAdminStore((s) => s.fetchAuditLogForCharts)
  const auditStartTime = useAdminStore((s) => s.auditStartTime)
  const auditEndTime = useAdminStore((s) => s.auditEndTime)
  const setAuditTimeRange = useAdminStore((s) => s.setAuditTimeRange)

  // Determine which filter field the current category uses
  // _actor is a pseudo-value used only for the dropdown; it doesn't map to an action prefix
  const selectedCategory = auditActionFilter === '_actor' ? '_actor' : (auditActionFilter || '')
  const activeCategory = FILTER_CATEGORIES.find((c) => c.value === selectedCategory) || FILTER_CATEGORIES[0]
  const filterField = activeCategory.filterField // 'target' | 'session_id' | 'actor'

  const currentFilterValue = filterField === 'session_id' ? auditSessionFilter
    : filterField === 'actor' ? auditActorFilter
    : auditTargetFilter

  const [localValue, setLocalValue] = useState(currentFilterValue)

  const clearAllValueFilters = () => {
    setAuditTargetFilter('')
    setAuditSessionFilter('')
    setAuditActorFilter('')
  }

  const debouncedSetValue = useDebouncedCallback((v) => {
    clearAllValueFilters()
    if (filterField === 'session_id') setAuditSessionFilter(v)
    else if (filterField === 'actor') setAuditActorFilter(v)
    else setAuditTargetFilter(v)
  }, 300)

  // Sync local input when category changes
  useEffect(() => {
    setLocalValue(filterField === 'session_id' ? auditSessionFilter
      : filterField === 'actor' ? auditActorFilter
      : auditTargetFilter)
  }, [auditActionFilter])

  useEffect(() => { fetchAuditLog(false) }, [auditActionFilter, auditActorFilter, auditTargetFilter, auditSessionFilter, auditStartTime, auditEndTime])
  useEffect(() => { fetchAuditLogForCharts() }, [auditActionFilter, auditActorFilter, auditTargetFilter, auditSessionFilter, auditStartTime, auditEndTime])

  const hasMore = !!auditNextCursor

  const handleRefresh = () => {
    fetchAuditLog(false)
    fetchAuditLogForCharts()
  }

  return (
    <div className="flex flex-col gap-4" style={{ padding: '32px var(--admin-section-x-wide)' }}>
      <div className="flex items-center gap-3">
        <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>
          {t('admin.auditLog')}
        </h2>
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

      {/* Shared filters — above both columns */}
      <div className="flex flex-col gap-3 flex-shrink-0" style={{ width: 'fit-content' }}>
        {/* Row 1: Time range */}
        <DateRangePicker
          startTime={auditStartTime}
          endTime={auditEndTime}
          onChange={(start, end) => setAuditTimeRange(start, end)}
        />

        {/* Row 2: FILTER BY [dropdown] : [value] */}
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
            value={selectedCategory}
            onChange={(val) => {
              setAuditActionFilter(val || null)
              clearAllValueFilters()
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
              placeholder={
                filterField === 'session_id' ? t('admin.searchSession')
                : filterField === 'actor' ? t('admin.searchActor')
                : t('admin.searchTarget')
              }
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

      {/* Two-column: entries left, chart right */}
      <div className="flex gap-5" style={{ maxHeight: 'calc(100vh - var(--navbar-height) - 260px)', overflow: 'hidden' }}>
        {/* Left column: entries */}
        <div className="flex flex-col flex-1" style={{ minWidth: 0, minHeight: 0 }}>
          {auditLoading && auditEntries.length === 0 ? (
            <EntrySkeleton />
          ) : auditEntries.length === 0 ? (
            <div className="text-sm" style={{ color: 'var(--text-dim)', padding: '20px 0' }}>
              {t('admin.noAuditEntries')}
            </div>
          ) : (
            /* Height must stay content-driven (clamped like the row above): the
               virtualized list has no intrinsic height, so `flex-1` would let the
               shorter chart column dictate the row height. */
            <AuditEntryList
              className="overflow-y-auto"
              style={{ maxHeight: 'calc(100vh - var(--navbar-height) - 260px)' }}
              entries={auditEntries}
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
