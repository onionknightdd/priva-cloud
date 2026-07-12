import { useEffect, useMemo, useState } from 'react'
import {
  ArrowLeft, CalendarClock, Plus, Play, Pause, Pencil, Square,
  Check, X, CircleSlash, ChevronDown, ChevronRight, ExternalLink,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import Dropdown from '@shared/components/shared/Dropdown'
import CopyButton from '@shared/components/shared/CopyButton'
import useSchedulerStore, { ALL_RUNS } from '../../stores/schedulerStore'
import { openSession } from '../../session/openSession'
import { describeTrigger, formatTrigger } from './triggerPresets'
import JobDrawer from './JobDrawer'

// Design §9.1 (LOCKED): master-detail. Left = job rows (2px STATUS border,
// selection = background only) + ALL RUNS footer entry; right = detail header
// with always-visible actions + this job's runs (inline error expand).

const REFRESH_MS = 10000

function fmtDuration(ms) {
  if (ms == null) return '—'
  const s = Math.round(ms / 1000)
  if (s < 60) return `${s}s`
  const m = Math.floor(s / 60)
  if (m < 60) return `${m}m${String(s % 60).padStart(2, '0')}s`
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, '0')}m`
}

function fmtWhen(iso, t) {
  if (!iso) return '—'
  const d = new Date(iso)
  if (Number.isNaN(d.getTime())) return '—'
  const now = new Date()
  const time = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
  const sameDay = d.toDateString() === now.toDateString()
  if (sameDay) return `${t('scheduler.today', { defaultValue: 'today' })} ${time}`
  const y = new Date(now); y.setDate(now.getDate() - 1)
  if (d.toDateString() === y.toDateString()) {
    return `${t('scheduler.yesterday', { defaultValue: 'yesterday' })} ${time}`
  }
  return `${d.toLocaleDateString([], { month: 'short', day: 'numeric' })} ${time}`
}

function fmtElapsed(iso) {
  const started = new Date(iso).getTime()
  if (Number.isNaN(started)) return ''
  const s = Math.max(0, Math.round((Date.now() - started) / 1000))
  const m = Math.floor(s / 60)
  return `${m}:${String(s % 60).padStart(2, '0')}`
}

const RUN_BORDER = {
  running: 'var(--status-running)',
  success: 'var(--status-success)',
  error: 'var(--status-error)',
  cancelled: 'var(--status-error)',
  skipped: 'var(--status-pending)',
}

function jobBorder(job, liveByJob, lastByJob) {
  if (liveByJob[job.id]) return 'var(--status-running)'
  if (job.status === 'paused') return 'var(--status-idle)'
  const last = lastByJob[job.id]
  if (last?.status === 'error') return 'var(--status-error)'
  if (last) return 'var(--status-success)'
  return 'var(--status-idle)'
}

const TYPE_LABEL = { agent_run: 'AGENT', http_call: 'HTTP', user_script: 'SCRIPT' }

function TypeChip({ jobType }) {
  return (
    <span
      className="uppercase flex-shrink-0"
      style={{
        fontSize: 11, letterSpacing: '0.06em', fontWeight: 600,
        color: 'var(--cyan)', border: '1px solid var(--border)',
        borderRadius: 2, padding: '1px 6px', background: 'var(--bg-surface)',
      }}
    >
      {TYPE_LABEL[jobType] || 'AGENT'}
    </span>
  )
}

function ActionButton({ icon: Icon, label, onClick, danger = false, disabled = false }) {
  const [hover, setHover] = useState(false)
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1"
      style={{
        fontSize: 12, padding: '4px 10px', borderRadius: 4,
        border: '1px solid var(--border)', cursor: disabled ? 'default' : 'pointer',
        background: hover && !disabled ? 'var(--bg-elevated)' : 'var(--bg-surface)',
        color: disabled ? 'var(--text-dim)'
          : danger ? 'var(--red)'
            : hover ? 'var(--text-primary)' : 'var(--text-secondary)',
        opacity: disabled ? 0.6 : 1,
        transition: 'color 150ms ease, background 150ms ease, border-color 150ms ease',
        borderColor: hover && !disabled ? 'var(--border-strong)' : 'var(--border)',
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <Icon size={14} strokeWidth={1.5} />
      {label}
    </button>
  )
}

function RunGlyph({ status }) {
  const common = { size: 12, strokeWidth: 1.5 }
  if (status === 'success') return <Check {...common} style={{ color: 'var(--green)' }} />
  if (status === 'error') return <X {...common} style={{ color: 'var(--red)' }} />
  if (status === 'cancelled') return <X {...common} style={{ color: 'var(--red)' }} />
  if (status === 'skipped') return <CircleSlash {...common} style={{ color: 'var(--yellow)' }} />
  return (
    <span
      aria-hidden
      style={{
        width: 8, height: 8, borderRadius: '50%',
        background: 'var(--purple)', display: 'inline-block', flexShrink: 0,
      }}
    />
  )
}

function RunRow({ run, showJobName, t, onOpenSession, onStop }) {
  const [expanded, setExpanded] = useState(false)
  const [hover, setHover] = useState(false)
  const isError = run.status === 'error'
  const hasSession = !!run.session_id
  const reason = run.error_message || ''

  return (
    <div
      style={{
        borderLeft: `2px solid ${RUN_BORDER[run.status] || 'var(--status-idle)'}`,
        borderBottom: '1px solid var(--border-subtle)',
        background: hover ? 'var(--bg-surface)' : 'transparent',
        transition: 'background 150ms ease',
      }}
      onMouseEnter={() => setHover(true)}
      onMouseLeave={() => setHover(false)}
    >
      <div className="flex items-center gap-3 min-w-0" style={{ padding: '7px 12px' }}>
        <span className="inline-flex items-center justify-center flex-shrink-0" style={{ width: 14 }}>
          <RunGlyph status={run.status} />
        </span>
        <span
          className="flex-shrink-0"
          style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--text-secondary)', width: 64 }}
        >
          {run.status === 'running' ? fmtElapsed(run.started_at) : fmtDuration(run.duration_ms)}
        </span>
        <span className="flex-shrink-0" style={{ fontSize: 12, color: 'var(--text-secondary)', width: 110 }}>
          {fmtWhen(run.started_at, t)}
        </span>
        {showJobName && (
          <span className="truncate" style={{ fontSize: 12, color: 'var(--text-secondary)', minWidth: 0, flex: '0 1 auto' }}>
            {run.job_name || t('scheduler.deletedJob', { defaultValue: '(deleted job)' })}
          </span>
        )}
        <span className="flex-1 min-w-0" />
        {run.status === 'skipped' && (
          <span style={{ fontSize: 11, color: 'var(--yellow)', fontFamily: 'JetBrains Mono, monospace' }} className="truncate">
            {reason}
          </span>
        )}
        {run.status === 'running' && (
          <button
            type="button"
            onClick={() => onStop(run)}
            className="inline-flex items-center gap-1 flex-shrink-0"
            style={{
              fontSize: 11, color: 'var(--red)', background: 'transparent',
              border: 'none', cursor: 'pointer', padding: '2px 4px',
            }}
          >
            <Square size={11} strokeWidth={1.5} />
            {t('scheduler.stop', { defaultValue: 'Stop' })}
          </button>
        )}
        {isError && (
          <button
            type="button"
            onClick={() => setExpanded((v) => !v)}
            className="inline-flex items-center gap-1 flex-shrink-0"
            style={{
              fontSize: 11, color: 'var(--red)', background: 'transparent',
              border: 'none', cursor: 'pointer', padding: '2px 4px',
            }}
          >
            {t('scheduler.error', { defaultValue: 'error' })}
            {expanded
              ? <ChevronDown size={11} strokeWidth={1.5} />
              : <ChevronRight size={11} strokeWidth={1.5} />}
          </button>
        )}
        {hasSession && run.status !== 'running' && (
          <button
            type="button"
            onClick={() => onOpenSession(run.session_id)}
            className="inline-flex items-center gap-1 flex-shrink-0"
            style={{
              fontSize: 11, color: 'var(--blue)', background: 'transparent',
              border: 'none', cursor: 'pointer', padding: '2px 4px',
            }}
          >
            <ExternalLink size={11} strokeWidth={1.5} />
            {t('scheduler.openSession', { defaultValue: 'open session' })}
          </button>
        )}
        {hasSession && run.status === 'running' && (
          <button
            type="button"
            onClick={() => onOpenSession(run.session_id)}
            className="inline-flex items-center gap-1 flex-shrink-0"
            style={{
              fontSize: 11, color: 'var(--purple)', background: 'transparent',
              border: 'none', cursor: 'pointer', padding: '2px 4px',
            }}
          >
            <ExternalLink size={11} strokeWidth={1.5} />
            {t('scheduler.watchLive', { defaultValue: 'watch live' })}
          </button>
        )}
      </div>
      {isError && expanded && (
        <div style={{ padding: '0 12px 10px 29px' }}>
          <div
            className="copyable relative overflow-x-auto"
            style={{
              background: 'var(--bg-elevated)', border: '1px solid var(--border-subtle)',
              borderRadius: 2, padding: '8px 10px',
              fontFamily: 'JetBrains Mono, monospace', fontSize: 11,
              color: 'var(--text-primary)', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
            }}
          >
            <span className="absolute" style={{ top: 4, right: 4 }}>
              <CopyButton content={reason} />
            </span>
            {reason || '(no message)'}
          </div>
          <div style={{ fontSize: 11, color: 'var(--text-dim)', marginTop: 4, fontFamily: 'JetBrains Mono, monospace' }}>
            {t('scheduler.duration')}: {fmtDuration(run.duration_ms)} · run {run.run_id.slice(0, 8)}
            {run.num_turns != null ? ` · ${run.num_turns} ${t('scheduler.turns', { defaultValue: 'turns' })}` : ''}
          </div>
        </div>
      )}
    </div>
  )
}

function Skeleton() {
  return (
    <div className="flex flex-1 min-w-0" style={{ minHeight: 0 }}>
      <div className="flex-shrink-0 flex flex-col gap-2" style={{ width: 300, padding: 12, borderRight: '1px solid var(--border)' }}>
        {[0, 1, 2].map((i) => <div key={i} className="skeleton" style={{ height: 44 }} />)}
      </div>
      <div className="flex-1 min-w-0 flex flex-col gap-2" style={{ padding: 16 }}>
        <div className="skeleton" style={{ height: 28, width: '40%' }} />
        <div className="skeleton" style={{ height: 16, width: '55%' }} />
        <div style={{ height: 12 }} />
        {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 32 }} />)}
      </div>
    </div>
  )
}

const RUN_STATUS_OPTIONS = (t) => [
  { value: '', label: t('scheduler.allStatus', { defaultValue: 'All status' }) },
  { value: 'success', label: t('scheduler.statusSuccess', { defaultValue: 'success' }) },
  { value: 'error', label: t('scheduler.statusError', { defaultValue: 'error' }) },
  { value: 'skipped', label: t('scheduler.statusSkipped', { defaultValue: 'skipped' }) },
  { value: 'running', label: t('scheduler.statusRunning', { defaultValue: 'running' }) },
  { value: 'cancelled', label: t('scheduler.statusCancelled', { defaultValue: 'cancelled' }) },
]

export default function SchedulerView({ backTitle, onBack }) {
  const { t } = useTranslation()
  const setActiveNavTab = useUiStore((s) => s.setActiveNavTab)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)

  const jobs = useSchedulerStore((s) => s.jobs)
  const jobsLoading = useSchedulerStore((s) => s.jobsLoading)
  const jobsLoaded = useSchedulerStore((s) => s.jobsLoaded)
  const selectedId = useSchedulerStore((s) => s.selectedId)
  const runs = useSchedulerStore((s) => s.runs)
  const runsLoading = useSchedulerStore((s) => s.runsLoading)
  const runsNextCursor = useSchedulerStore((s) => s.runsNextCursor)
  const runStatusFilter = useSchedulerStore((s) => s.runStatusFilter)
  const runJobFilter = useSchedulerStore((s) => s.runJobFilter)
  const liveByJob = useSchedulerStore((s) => s.liveByJob)
  const lastByJob = useSchedulerStore((s) => s.lastByJob)
  const drawerOpen = useSchedulerStore((s) => s.drawerOpen)

  const {
    loadJobs, select, setRunStatusFilter, setRunJobFilter, loadRuns, refresh,
    openCreateDrawer, openEditDrawer, removeJob, pauseResume, runNow, stopRun,
  } = useSchedulerStore.getState()

  useEffect(() => {
    loadJobs()
    const timer = setInterval(() => {
      if (!document.hidden) refresh()
    }, REFRESH_MS)
    return () => clearInterval(timer)
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const selectedJob = useMemo(
    () => jobs.find((j) => j.id === selectedId) || null, [jobs, selectedId])
  const isAllRuns = selectedId === ALL_RUNS
  const liveRun = selectedJob ? liveByJob[selectedJob.id] : null
  const activeCount = jobs.filter((j) => j.status === 'active').length
  const pausedCount = jobs.length - activeCount

  const handleOpenSession = async (sessionId) => {
    setActiveNavTab('priva')
    await openSession({ id: sessionId, sessionId })
  }

  const handleDelete = (job) => {
    showConfirmDialog({
      title: t('scheduler.deleteJobTitle', { defaultValue: 'Delete job' }),
      message: t('scheduler.deleteJobMessage', {
        name: job.name,
        defaultValue: `Delete "${job.name}"? Its run history is kept under ALL RUNS.`,
      }),
      confirmLabel: t('scheduler.delete'),
      danger: true,
      requireText: job.name,
      onConfirm: () => removeJob(job.id),
    })
  }

  const header = (
    <div
      className="flex items-center px-3 flex-shrink-0"
      style={{ height: 40, background: 'var(--bg-base)', borderBottom: '1px solid var(--border-subtle)' }}
    >
      <div className="inline-flex items-center min-w-0" style={{ gap: 10, flex: '1 1 auto' }}>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center justify-center flex-shrink-0"
          aria-label={backTitle}
          title={backTitle}
          style={{
            width: 28, height: 28, padding: 0, background: 'transparent', border: 'none',
            borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer',
            transition: 'color 150ms ease, background 150ms ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'transparent' }}
        >
          <ArrowLeft size={16} strokeWidth={1.5} />
        </button>
        <CalendarClock size={16} strokeWidth={1.5} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
        <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 600 }}>
          {t('scheduler.title')}
        </span>
      </div>
      <ActionButton icon={Plus} label={t('scheduler.newJob')} onClick={openCreateDrawer} />
    </div>
  )

  if (!jobsLoaded && jobsLoading) {
    return (
      <div className="flex flex-col flex-1 min-w-0" style={{ minHeight: 0, background: 'var(--bg-base)' }}>
        {header}
        <Skeleton />
      </div>
    )
  }

  const empty = jobsLoaded && jobs.length === 0

  return (
    <div className="flex flex-col flex-1 min-w-0" style={{ minHeight: 0, background: 'var(--bg-base)' }}>
      {header}

      {empty ? (
        <div className="flex flex-col items-center justify-center flex-1 gap-3" style={{ color: 'var(--text-dim)' }}>
          <CalendarClock size={40} strokeWidth={1.5} />
          <div style={{ fontSize: 13, color: 'var(--text-secondary)' }}>
            {t('scheduler.emptyTitle', { defaultValue: 'No scheduled jobs yet' })}
          </div>
          <ActionButton icon={Plus} label={t('scheduler.newJob')} onClick={openCreateDrawer} />
          <div style={{ fontSize: 12 }}>
            {t('scheduler.emptyHint', { defaultValue: '…or ask your agent in chat' })}
          </div>
        </div>
      ) : (
        <div className="flex flex-1 min-w-0" style={{ minHeight: 0 }}>
          {/* Left: jobs list */}
          <div
            className="flex-shrink-0 flex flex-col overflow-hidden"
            style={{ width: 300, borderRight: '1px solid var(--border)' }}
          >
            <div
              className="uppercase flex-shrink-0"
              style={{
                fontSize: 11, letterSpacing: '0.06em', fontWeight: 600,
                color: 'var(--text-dim)', padding: '10px 12px 6px',
              }}
            >
              {t('scheduler.jobs')} — {activeCount} {t('scheduler.activeCount', { defaultValue: 'active' })}
              {pausedCount > 0 ? ` · ${pausedCount} ${t('scheduler.pausedCount', { defaultValue: 'paused' })}` : ''}
            </div>
            <div className="flex-1 overflow-y-auto min-w-0">
              {jobs.map((job) => {
                const live = liveByJob[job.id]
                const selected = selectedId === job.id
                return (
                  <div
                    key={job.id}
                    onClick={() => select(job.id)}
                    className="min-w-0"
                    style={{
                      borderLeft: `2px solid ${jobBorder(job, liveByJob, lastByJob)}`,
                      background: selected ? 'var(--bg-elevated)' : 'transparent',
                      padding: '8px 12px', cursor: 'pointer',
                      transition: 'background 150ms ease',
                    }}
                    onMouseEnter={(e) => { if (!selected) e.currentTarget.style.background = 'var(--bg-surface)' }}
                    onMouseLeave={(e) => { if (!selected) e.currentTarget.style.background = selected ? 'var(--bg-elevated)' : 'transparent' }}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className="truncate" style={{ fontSize: 13, fontWeight: 600, color: 'var(--text-primary)', minWidth: 0, flex: 1 }}>
                        {job.name}
                      </span>
                      {job.status === 'paused' && (
                        <span className="uppercase flex-shrink-0" style={{ fontSize: 10, letterSpacing: '0.06em', color: 'var(--text-dim)' }}>
                          {t('scheduler.paused', { defaultValue: 'paused' })}
                        </span>
                      )}
                    </div>
                    <div
                      className="truncate"
                      style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: live ? 'var(--purple)' : 'var(--text-dim)', marginTop: 2 }}
                    >
                      {live
                        ? `● ${t('scheduler.runningFor', { defaultValue: 'running' })} ${fmtElapsed(live.started_at)}`
                        : job.status === 'paused'
                          ? `${t('scheduler.nextRun')} —`
                          : `${t('scheduler.nextRun')} ${fmtWhen(job.next_run_time, t)}`}
                    </div>
                  </div>
                )
              })}
            </div>
            {/* ALL RUNS footer entry — also the only home of deleted jobs' runs */}
            <div
              onClick={() => select(ALL_RUNS)}
              className="flex-shrink-0 uppercase"
              style={{
                borderTop: '1px solid var(--border-subtle)',
                borderLeft: `2px solid ${isAllRuns ? 'var(--border-strong)' : 'transparent'}`,
                background: isAllRuns ? 'var(--bg-elevated)' : 'transparent',
                padding: '9px 12px', cursor: 'pointer',
                fontSize: 11, letterSpacing: '0.06em', fontWeight: 600,
                color: isAllRuns ? 'var(--text-primary)' : 'var(--text-secondary)',
                transition: 'background 150ms ease, color 150ms ease',
              }}
              onMouseEnter={(e) => { if (!isAllRuns) e.currentTarget.style.background = 'var(--bg-surface)' }}
              onMouseLeave={(e) => { if (!isAllRuns) e.currentTarget.style.background = isAllRuns ? 'var(--bg-elevated)' : 'transparent' }}
            >
              {t('scheduler.allRuns', { defaultValue: 'All runs' })}
            </div>
          </div>

          {/* Right: detail */}
          <div className="flex-1 min-w-0 flex flex-col overflow-hidden">
            {isAllRuns ? (
              <div className="flex items-center gap-3 flex-shrink-0" style={{ padding: '14px 16px 10px' }}>
                <span style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                  {t('scheduler.allRuns', { defaultValue: 'All runs' })}
                </span>
                <span className="flex-1" />
                <Dropdown
                  size="sm"
                  align="right"
                  value={runJobFilter || ''}
                  onChange={(v) => setRunJobFilter(v || null)}
                  options={[
                    { value: '', label: t('scheduler.allJobs', { defaultValue: 'All jobs' }) },
                    ...jobs.map((j) => ({ value: j.id, label: j.name })),
                  ]}
                />
                <Dropdown
                  size="sm"
                  align="right"
                  value={runStatusFilter || ''}
                  onChange={(v) => setRunStatusFilter(v || null)}
                  options={RUN_STATUS_OPTIONS(t)}
                />
              </div>
            ) : selectedJob ? (
              <div className="flex-shrink-0" style={{ padding: '14px 16px 10px', borderBottom: '1px solid var(--border-subtle)' }}>
                <div className="flex items-center gap-2 min-w-0">
                  <span className="truncate" style={{ fontSize: 16, fontWeight: 700, color: 'var(--text-primary)' }}>
                    {selectedJob.name}
                  </span>
                  <TypeChip jobType={selectedJob.job_config?.job_type || 'agent_run'} />
                  {selectedJob.status === 'paused' && (
                    <span
                      className="uppercase flex-shrink-0"
                      style={{
                        fontSize: 11, letterSpacing: '0.06em', fontWeight: 600,
                        color: 'var(--yellow)', border: '1px solid var(--border)',
                        borderRadius: 2, padding: '1px 6px',
                      }}
                    >
                      {t('scheduler.paused', { defaultValue: 'paused' })}
                    </span>
                  )}
                </div>
                <div className="flex items-center gap-2 min-w-0" style={{ marginTop: 6 }}>
                  <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: 'var(--text-primary)' }}>
                    {formatTrigger(selectedJob.trigger)}
                  </span>
                  <span style={{ fontSize: 12, color: 'var(--text-dim)' }} className="truncate">
                    · {selectedJob.timezone} (≈ {describeTrigger(selectedJob.trigger, t)})
                  </span>
                </div>
                <div style={{ fontSize: 12, color: 'var(--text-secondary)', marginTop: 4 }}>
                  <span className="uppercase" style={{ fontSize: 10, letterSpacing: '0.06em', color: 'var(--text-dim)' }}>
                    {t('scheduler.nextRun')}
                  </span>{' '}
                  {selectedJob.status === 'paused' ? '—' : fmtWhen(selectedJob.next_run_time, t)}
                  <span style={{ color: 'var(--text-dim)' }}>
                    {' '}· {t('scheduler.createdAt')} {new Date(selectedJob.created_at).toLocaleDateString()}
                  </span>
                </div>
                <div className="flex items-center gap-2" style={{ marginTop: 10 }}>
                  {liveRun ? (
                    <ActionButton icon={Square} danger label={t('scheduler.stop', { defaultValue: 'Stop' })} onClick={() => stopRun(liveRun.run_id)} />
                  ) : (
                    <ActionButton icon={Play} label={t('scheduler.triggerNow')} onClick={() => runNow(selectedJob.id)} />
                  )}
                  <ActionButton
                    icon={selectedJob.status === 'paused' ? Play : Pause}
                    label={selectedJob.status === 'paused' ? t('scheduler.resume') : t('scheduler.pause')}
                    onClick={() => pauseResume(selectedJob)}
                  />
                  <ActionButton icon={Pencil} label={t('scheduler.edit')} onClick={() => openEditDrawer(selectedJob)} />
                </div>
              </div>
            ) : null}

            {/* Runs list */}
            <div className="flex items-center gap-2 flex-shrink-0" style={{ padding: '10px 16px 6px' }}>
              <span className="uppercase" style={{ fontSize: 11, letterSpacing: '0.06em', fontWeight: 600, color: 'var(--text-dim)' }}>
                {t('scheduler.runHistory')}
              </span>
              <span className="flex-1" />
              {!isAllRuns && (
                <Dropdown
                  size="sm"
                  align="right"
                  value={runStatusFilter || ''}
                  onChange={(v) => setRunStatusFilter(v || null)}
                  options={RUN_STATUS_OPTIONS(t)}
                />
              )}
            </div>
            <div className="flex-1 overflow-y-auto min-w-0" style={{ padding: '0 16px 16px' }}>
              {runs.length === 0 && !runsLoading && (
                <div style={{ fontSize: 12, color: 'var(--text-dim)', padding: '16px 0' }}>
                  {t('scheduler.noRuns')}
                </div>
              )}
              {runs.length === 0 && runsLoading && (
                <div className="flex flex-col gap-2" style={{ paddingTop: 8 }}>
                  {[0, 1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 32 }} />)}
                </div>
              )}
              {runs.map((run) => (
                <RunRow
                  key={run.run_id}
                  run={run}
                  showJobName={isAllRuns}
                  t={t}
                  onOpenSession={handleOpenSession}
                  onStop={(r) => stopRun(r.run_id)}
                />
              ))}
              {runsNextCursor && (
                <button
                  type="button"
                  onClick={() => loadRuns({ more: true })}
                  disabled={runsLoading}
                  style={{
                    width: '100%', marginTop: 8, padding: '6px 0',
                    fontSize: 12, color: 'var(--text-secondary)',
                    background: 'var(--bg-surface)', border: '1px solid var(--border)',
                    borderRadius: 4, cursor: 'pointer',
                    transition: 'background 150ms ease',
                  }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                >
                  {runsLoading ? '…' : t('scheduler.loadMore', { defaultValue: 'Load more' })}
                </button>
              )}
            </div>
          </div>
        </div>
      )}

      {drawerOpen && <JobDrawer onDelete={handleDelete} />}
    </div>
  )
}
