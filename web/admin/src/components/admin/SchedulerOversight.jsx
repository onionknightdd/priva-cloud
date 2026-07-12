import { useEffect, useMemo, useState } from 'react'
import { CalendarClock, Check, ChevronDown, ChevronRight, CircleSlash, Pause, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import Dropdown from '@shared/components/shared/Dropdown'
import CopyButton from '@shared/components/shared/CopyButton'
import {
  getFleet, getSchedulerJobs, getSchedulerRuns, pauseAllSchedulerJobs,
} from '@shared/api/admin'

// Admin Dashboard → Scheduler (D12, layout locked 2026-07-13): master-detail —
// accounts left (fleet-sourced, job counts lazily), selected account's jobs +
// runs right, [Pause all] as the US-9 kill switch. Read-only otherwise.

const RUN_BORDER = {
  running: 'var(--status-running)',
  success: 'var(--status-success)',
  error: 'var(--status-error)',
  cancelled: 'var(--status-error)',
  skipped: 'var(--status-pending)',
}

function fmtDuration(ms) {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

function fmtWhen(iso) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  return d.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

function formatTrigger(trigger) {
  if (!trigger) return ''
  if (trigger.type === 'interval') {
    const parts = []
    for (const [k, suffix] of [['weeks', 'w'], ['days', 'd'], ['hours', 'h'], ['minutes', 'm'], ['seconds', 's']]) {
      if (trigger[k]) parts.push(`${trigger[k]}${suffix}`)
    }
    return `every ${parts.join(' ') || '—'}`
  }
  return trigger.expr || ''
}

function RunGlyph({ status }) {
  const common = { size: 12, strokeWidth: 1.5 }
  if (status === 'success') return <Check {...common} style={{ color: 'var(--green)' }} />
  if (status === 'error' || status === 'cancelled') return <X {...common} style={{ color: 'var(--red)' }} />
  if (status === 'skipped') return <CircleSlash {...common} style={{ color: 'var(--yellow)' }} />
  return <span aria-hidden style={{ width: 8, height: 8, borderRadius: '50%', background: 'var(--purple)', display: 'inline-block' }} />
}

function AdminRunRow({ run, t }) {
  const [expanded, setExpanded] = useState(false)
  const isError = run.status === 'error'
  const reason = run.error_message || ''
  return (
    <div
      style={{
        borderLeft: `2px solid ${RUN_BORDER[run.status] || 'var(--status-idle)'}`,
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div className="flex items-center gap-3 min-w-0" style={{ padding: '6px 10px' }}>
        <span className="inline-flex items-center justify-center flex-shrink-0" style={{ width: 14 }}>
          <RunGlyph status={run.status} />
        </span>
        <span className="flex-shrink-0" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--text-secondary)', width: 60 }}>
          {fmtDuration(run.duration_ms)}
        </span>
        <span className="flex-shrink-0" style={{ fontSize: 12, color: 'var(--text-secondary)', width: 120 }}>
          {fmtWhen(run.started_at)}
        </span>
        <span className="truncate" style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 0, flex: 1 }}>
          {run.job_name || t('scheduler.deletedJob')}
        </span>
        {run.status === 'skipped' && (
          <span style={{ fontSize: 11, color: 'var(--yellow)', fontFamily: 'JetBrains Mono, monospace' }} className="truncate flex-shrink-0">
            {reason}
          </span>
        )}
        {isError && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex items-center gap-1 flex-shrink-0"
            style={{ fontSize: 11, color: 'var(--red)', background: 'transparent', border: 'none', cursor: 'pointer', padding: '2px 4px' }}
          >
            {t('scheduler.error')}
            {expanded ? <ChevronDown size={11} strokeWidth={1.5} /> : <ChevronRight size={11} strokeWidth={1.5} />}
          </button>
        )}
      </div>
      {isError && expanded && (
        <div style={{ padding: '0 10px 8px 27px' }}>
          <div
            className="copyable relative overflow-x-auto"
            style={{
              background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              borderRadius: 2, padding: '8px 10px',
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
              color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}
          >
            <span className="absolute" style={{ top: 4, right: 4 }}><CopyButton content={reason} /></span>
            {reason || '(no message)'}
          </div>
        </div>
      )}
    </div>
  )
}

const RUN_STATUS_OPTIONS = (t) => [
  { value: '', label: t('scheduler.allStatus') },
  { value: 'success', label: t('scheduler.statusSuccess') },
  { value: 'error', label: t('scheduler.statusError') },
  { value: 'skipped', label: t('scheduler.statusSkipped') },
  { value: 'running', label: t('scheduler.statusRunning') },
  { value: 'cancelled', label: t('scheduler.statusCancelled') },
]

export default function SchedulerOversight() {
  const { t } = useTranslation()
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)

  const [accounts, setAccounts] = useState(null) // [{account_id, username, phase}]
  const [counts, setCounts] = useState({})       // account_id → {active, total}
  const [selected, setSelected] = useState(null)
  const [jobs, setJobs] = useState([])
  const [runs, setRuns] = useState([])
  const [nextCursor, setNextCursor] = useState(null)
  const [statusFilter, setStatusFilter] = useState('')
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    let alive = true
    getFleet().then(async (fleet) => {
      if (!alive) return
      const entries = (fleet.entries || fleet.accounts || []).filter((e) => e.account_id)
      setAccounts(entries)
      if (entries.length && !selected) setSelected(entries[0].account_id)
      // Lazy job counts, fail-soft per account (small fleets).
      const pairs = await Promise.all(entries.map(async (e) => {
        try {
          const d = await getSchedulerJobs(e.account_id)
          const active = (d.jobs || []).filter((j) => j.status === 'active').length
          return [e.account_id, { active, total: d.total || 0 }]
        } catch { return [e.account_id, null] }
      }))
      if (alive) setCounts(Object.fromEntries(pairs.filter(([, v]) => v)))
    }).catch(() => { if (alive) setAccounts([]) })
    return () => { alive = false }
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const loadDetail = async (accountId, { status = statusFilter, before = null, append = false } = {}) => {
    setLoadingDetail(!append)
    try {
      const [jobsData, runsData] = await Promise.all([
        append ? Promise.resolve(null) : getSchedulerJobs(accountId),
        getSchedulerRuns(accountId, { status: status || null, before }),
      ])
      if (jobsData) setJobs(jobsData.jobs || [])
      setRuns((prev) => (append ? [...prev, ...(runsData.runs || [])] : (runsData.runs || [])))
      setNextCursor(runsData.next_cursor || null)
    } catch {
      if (!append) { setJobs([]); setRuns([]); setNextCursor(null) }
    }
    setLoadingDetail(false)
  }

  useEffect(() => {
    if (selected) { setStatusFilter(''); loadDetail(selected, { status: '' }) }
  }, [selected]) // eslint-disable-line react-hooks/exhaustive-deps

  const selectedEntry = useMemo(
    () => (accounts || []).find((a) => a.account_id === selected) || null,
    [accounts, selected])
  const activeCount = jobs.filter((j) => j.status === 'active').length

  const handlePauseAll = () => {
    if (!selectedEntry) return
    showConfirmDialog({
      title: t('admin.schedulerPauseAllTitle', { defaultValue: 'Pause all jobs' }),
      message: t('admin.schedulerPauseAllMessage', {
        name: selectedEntry.username || selectedEntry.account_id,
        defaultValue: `Pause every active scheduled job for ${selectedEntry.username || selectedEntry.account_id}?`,
      }),
      confirmLabel: t('scheduler.pause'),
      danger: true,
      onConfirm: async () => {
        await pauseAllSchedulerJobs(selected)
        loadDetail(selected)
        setCounts((c) => ({ ...c, [selected]: { ...(c[selected] || {}), active: 0 } }))
      },
    })
  }

  if (accounts === null) {
    return (
      <div className="flex flex-1 min-w-0" style={{ minHeight: 0 }}>
        <div className="flex-shrink-0 flex flex-col gap-2" style={{ width: 240, padding: 12, borderRight: '1px solid var(--border)' }}>
          {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 36 }} />)}
        </div>
        <div className="flex-1 flex flex-col gap-2" style={{ padding: 16 }}>
          <div className="skeleton" style={{ height: 24, width: '35%' }} />
          {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 30 }} />)}
        </div>
      </div>
    )
  }

  if (accounts.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center" style={{ color: 'var(--text-dim)' }}>
        <div className="flex flex-col items-center gap-2">
          <CalendarClock size={24} strokeWidth={1.5} />
          <span style={{ fontSize: 13 }}>{t('admin.schedulerNoAccounts', { defaultValue: 'No accounts' })}</span>
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-1 min-w-0" style={{ minHeight: 0 }}>
      {/* Left: accounts */}
      <div className="flex-shrink-0 flex flex-col overflow-hidden" style={{ width: 240, borderRight: '1px solid var(--border)' }}>
        <div className="uppercase flex-shrink-0" style={{ fontSize: 11, letterSpacing: '0.06em', fontWeight: 600, color: 'var(--text-dim)', padding: '10px 12px 6px' }}>
          {t('admin.schedulerAccounts', { defaultValue: 'Accounts' })}
        </div>
        <div className="flex-1 overflow-y-auto">
          {accounts.map((a) => {
            const isSel = a.account_id === selected
            const c = counts[a.account_id]
            return (
              <div
                key={a.account_id}
                onClick={() => setSelected(a.account_id)}
                style={{
                  borderLeft: `2px solid ${c?.active ? 'var(--status-success)' : 'var(--status-idle)'}`,
                  background: isSel ? 'var(--bg-elevated)' : 'transparent',
                  padding: '8px 12px', cursor: 'pointer', transition: 'background 150ms ease',
                }}
                onMouseEnter={(e) => { if (!isSel) e.currentTarget.style.background = 'var(--bg-surface)' }}
                onMouseLeave={(e) => { if (!isSel) e.currentTarget.style.background = isSel ? 'var(--bg-elevated)' : 'transparent' }}
              >
                <div className="flex items-center gap-2 min-w-0">
                  <span className="truncate" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: 0 }}>
                    {a.username || a.account_id}
                  </span>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: 'var(--text-dim)' }} className="flex-shrink-0">
                    {c ? c.total : '…'}
                  </span>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Right: selected account detail */}
      <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
        <div className="flex items-center gap-2 flex-shrink-0" style={{ padding: '12px 16px 8px' }}>
          <span className="truncate" style={{ fontSize: 15, fontWeight: 700, color: 'var(--text-primary)' }}>
            {selectedEntry?.username || selected}
          </span>
          <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
            — {activeCount} {t('scheduler.activeCount')} · {jobs.length - activeCount} {t('scheduler.pausedCount')}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={handlePauseAll}
            disabled={activeCount === 0}
            className="inline-flex items-center gap-1"
            style={{
              fontSize: 12, padding: '4px 10px', borderRadius: 4,
              border: '1px solid var(--border)', cursor: activeCount ? 'pointer' : 'default',
              background: 'var(--bg-surface)', color: activeCount ? 'var(--red)' : 'var(--text-dim)',
              transition: 'border-color 150ms ease',
            }}
            onMouseEnter={(e) => { if (activeCount) e.currentTarget.style.borderColor = 'var(--red)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            <Pause size={13} strokeWidth={1.5} />
            {t('admin.schedulerPauseAll', { defaultValue: 'Pause all' })}
          </button>
        </div>

        <div className="overflow-y-auto flex-shrink-0" style={{ maxHeight: '40%', padding: '0 16px' }}>
          {loadingDetail && jobs.length === 0 && (
            <div className="flex flex-col gap-2">{[0, 1].map((i) => <div key={i} className="skeleton" style={{ height: 30 }} />)}</div>
          )}
          {!loadingDetail && jobs.length === 0 && (
            <div style={{ fontSize: 12, color: 'var(--text-dim)', padding: '8px 0' }}>{t('scheduler.noJobs')}</div>
          )}
          {jobs.map((job) => (
            <div
              key={job.id}
              className="flex items-center gap-3 min-w-0"
              style={{
                borderLeft: `2px solid ${job.status === 'active' ? 'var(--status-success)' : 'var(--status-idle)'}`,
                borderBottom: '1px solid var(--border-subtle)', padding: '7px 10px',
              }}
            >
              <span className="truncate" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', flex: 1, minWidth: 0 }}>
                {job.name}
              </span>
              <span className="uppercase flex-shrink-0" style={{ fontSize: 10, letterSpacing: '0.06em', color: 'var(--cyan)' }}>
                {{ agent_run: 'AGENT', http_call: 'HTTP', user_script: 'SCRIPT' }[job.job_type] || 'AGENT'}
              </span>
              <span className="flex-shrink-0" style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: 'var(--text-secondary)' }}>
                {formatTrigger(job.trigger)}
              </span>
              <span
                className="uppercase flex-shrink-0"
                style={{
                  fontSize: 10, letterSpacing: '0.06em', fontWeight: 600,
                  color: job.status === 'active' ? 'var(--green)' : 'var(--text-dim)',
                }}
              >
                {job.status}
              </span>
            </div>
          ))}
        </div>

        <div className="flex items-center gap-2 flex-shrink-0" style={{ padding: '12px 16px 6px' }}>
          <span className="uppercase" style={{ fontSize: 11, letterSpacing: '0.06em', fontWeight: 600, color: 'var(--text-dim)' }}>
            {t('scheduler.runHistory')}
          </span>
          <span className="flex-1" />
          <Dropdown
            size="sm"
            align="right"
            value={statusFilter}
            onChange={(v) => { setStatusFilter(v); loadDetail(selected, { status: v }) }}
            options={RUN_STATUS_OPTIONS(t)}
          />
        </div>
        <div className="flex-1 overflow-y-auto min-w-0" style={{ padding: '0 16px 16px' }}>
          {runs.length === 0 && !loadingDetail && (
            <div style={{ fontSize: 12, color: 'var(--text-dim)', padding: '8px 0' }}>{t('scheduler.noRuns')}</div>
          )}
          {runs.map((run) => <AdminRunRow key={run.run_id} run={run} t={t} />)}
          {nextCursor && (
            <button
              type="button"
              onClick={() => loadDetail(selected, { before: nextCursor, append: true })}
              style={{
                width: '100%', marginTop: 8, padding: '6px 0', fontSize: 12,
                color: 'var(--text-secondary)', background: 'var(--bg-surface)',
                border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer',
                transition: 'background 150ms ease',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
              onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
            >
              {t('scheduler.loadMore')}
            </button>
          )}
        </div>
      </div>
    </div>
  )
}
