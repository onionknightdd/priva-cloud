import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronRight, Trash2, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import Dropdown from '@shared/components/shared/Dropdown'
import { useOverlayTransition } from '@shared/motion/useOverlayTransition'
import useSettingsStore from '../../stores/settingsStore'
import useSchedulerStore from '../../stores/schedulerStore'
import { validateTrigger } from '../../api/scheduler'
import {
  PRESETS, WEEKDAYS, presetToTrigger, triggerToPreset, describeTrigger, parseTime,
} from './triggerPresets'

// Create/edit drawer (design §9.2): 480px right slide; trigger editor =
// presets + custom-cron escape with an always-live preview line; type
// sections per job type; edit-only [Delete job] behind typed-name confirm.

const FIELD_LABEL = {
  fontSize: 11, letterSpacing: '0.06em', fontWeight: 600,
  color: 'var(--text-dim)', textTransform: 'uppercase',
  display: 'block', marginBottom: 4,
}

const INPUT_STYLE = {
  width: '100%', boxSizing: 'border-box',
  background: 'var(--bg-surface)', border: '1px solid var(--border)',
  borderRadius: 4, color: 'var(--text-primary)', fontSize: 13,
  padding: '6px 8px', outline: 'none',
  transition: 'border-color 150ms ease',
}

const MONO_INPUT = { ...INPUT_STYLE, fontFamily: 'JetBrains Mono, monospace', fontSize: 12 }

function Field({ label, children }) {
  return (
    <div style={{ marginBottom: 14 }}>
      <span style={FIELD_LABEL}>{label}</span>
      {children}
    </div>
  )
}

function TextInput({ value, onChange, placeholder, mono = false, invalid = false, onBlur, type = 'text' }) {
  return (
    <input
      type={type}
      value={value}
      placeholder={placeholder}
      onChange={(e) => onChange(e.target.value)}
      onBlur={onBlur}
      onFocus={(e) => { if (!invalid) e.currentTarget.style.borderColor = 'var(--border-strong)' }}
      style={{
        ...(mono ? MONO_INPUT : INPUT_STYLE),
        borderColor: invalid ? 'var(--red)' : 'var(--border)',
      }}
    />
  )
}

function NumberInput({ value, onChange, min = 1, width = 90 }) {
  return (
    <input
      type="number"
      min={min}
      value={value}
      onChange={(e) => onChange(e.target.value)}
      style={{ ...MONO_INPUT, width }}
    />
  )
}

function HeaderRows({ headers, setHeaders, t }) {
  const rows = Object.entries(headers)
  const update = (idx, key, val) => {
    const next = rows.map((r, i) => (i === idx ? [key, val] : r))
    setHeaders(Object.fromEntries(next.filter(([k]) => k !== '')))
  }
  return (
    <div className="flex flex-col gap-2">
      {rows.map(([k, v], i) => (
        <div key={i} className="flex items-center gap-2">
          <input value={k} placeholder="Header" onChange={(e) => update(i, e.target.value, v)} style={{ ...MONO_INPUT, flex: 1 }} />
          <input value={v} placeholder="Value" onChange={(e) => update(i, k, e.target.value)} style={{ ...MONO_INPUT, flex: 2 }} />
          <button
            type="button"
            onClick={() => setHeaders(Object.fromEntries(rows.filter((_, idx) => idx !== i)))}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', padding: 2 }}
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>
      ))}
      <button
        type="button"
        onClick={() => setHeaders({ ...headers, '': '' })}
        style={{
          alignSelf: 'flex-start', fontSize: 12, color: 'var(--text-secondary)',
          background: 'transparent', border: '1px dashed var(--border)',
          borderRadius: 4, padding: '3px 8px', cursor: 'pointer',
        }}
      >
        + {t('scheduler.addHeader', { defaultValue: 'Add header' })}
      </button>
    </div>
  )
}

export default function JobDrawer({ onDelete }) {
  const { t } = useTranslation()
  const editingJob = useSchedulerStore((s) => s.editingJob)
  const { closeDrawer, saveJob } = useSchedulerStore.getState()
  const models = useSettingsStore((s) => s.models)
  const fetchModels = useSettingsStore((s) => s.fetchModels)

  const isEdit = !!editingJob
  const [closing, setClosing] = useState(false)
  const { mounted, panelRef, backdropRef } = useOverlayTransition({
    open: !closing, variant: 'drawer',
  })

  // `useOverlayTransition` retains this component until the drawer exit
  // animation ends. Clear the scheduler state only once it has unmounted so a
  // later click on “New task” mounts a fresh drawer instead of reusing this
  // closed instance.
  useEffect(() => {
    if (closing && !mounted) closeDrawer()
  }, [closing, closeDrawer, mounted])

  // --- form state -----------------------------------------------------------
  const initialCfg = editingJob?.job_config || null
  const [name, setName] = useState(editingJob?.name || '')
  const [jobType, setJobType] = useState(initialCfg?.job_type || 'agent_run')
  const initialPreset = triggerToPreset(editingJob?.trigger)
  const [preset, setPreset] = useState(initialPreset.preset)
  const [time, setTime] = useState(initialPreset.time)
  const [weekday, setWeekday] = useState(initialPreset.weekday)
  const [nValue, setNValue] = useState(String(initialPreset.n || 1))
  const [cronExpr, setCronExpr] = useState(initialPreset.expr || editingJob?.trigger?.expr || '0 9 * * mon-fri')
  const [tz, setTz] = useState(editingJob?.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC')
  // agent
  const [prompt, setPrompt] = useState(initialCfg?.prompt ?? editingJob?.prompt ?? '')
  const [model, setModel] = useState(initialCfg?.model || editingJob?.model || '')
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [timeoutSec, setTimeoutSec] = useState(String(initialCfg?.timeout_seconds ?? (jobType === 'http_call' ? 30 : jobType === 'user_script' ? 300 : 1800)))
  const [maxTurns, setMaxTurns] = useState(String(initialCfg?.max_turns ?? 50))
  // http
  const [method, setMethod] = useState(initialCfg?.method || 'GET')
  const [url, setUrl] = useState(initialCfg?.url || '')
  const [headers, setHeaders] = useState(initialCfg?.headers || {})
  const [body, setBody] = useState(initialCfg?.body || '')
  // script
  const [language, setLanguage] = useState(initialCfg?.language || 'python')
  const [source, setSource] = useState(initialCfg?.source || 'inline')
  const [script, setScript] = useState(initialCfg?.script || '')
  const [filePath, setFilePath] = useState(initialCfg?.file_path || '')

  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState(null)

  // --- live trigger preview (server-validated, §9.2) --------------------------
  const trigger = useMemo(() => {
    if (preset === 'custom') return { type: 'cron', expr: cronExpr }
    return presetToTrigger(preset, { time, weekday, n: Number(nValue) || 1 })
  }, [preset, time, weekday, nValue, cronExpr])

  const [preview, setPreview] = useState({ valid: true, next: null, error: null })
  const previewTimer = useRef(null)
  useEffect(() => {
    if (!trigger) return
    if (preset !== 'custom' && !parseTime(time) && ['daily', 'weekdays', 'weekly'].includes(preset)) {
      setPreview({ valid: false, next: null, error: t('scheduler.badTime', { defaultValue: 'time must be HH:MM' }) })
      return
    }
    clearTimeout(previewTimer.current)
    previewTimer.current = setTimeout(async () => {
      try {
        const res = await validateTrigger(trigger, tz, editingJob?.created_at ?? null)
        setPreview({ valid: res.valid, next: res.next_run_time, error: res.error })
      } catch {
        setPreview({ valid: true, next: null, error: null }) // preview is best-effort
      }
    }, 300)
    return () => clearTimeout(previewTimer.current)
  }, [trigger, tz]) // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { if (jobType === 'agent_run') fetchModels?.() }, [jobType, fetchModels])

  const tzOptions = useMemo(() => {
    let zones = []
    try { zones = Intl.supportedValuesOf('timeZone') } catch { zones = ['UTC', 'Asia/Shanghai'] }
    if (!zones.includes(tz)) zones = [tz, ...zones]
    return zones.map((z) => ({ value: z, label: z }))
  }, [tz])

  const modelOptions = useMemo(() => ([
    { value: '', label: t('scheduler.defaultModel') },
    ...models.map((m) => ({ value: m.id, label: m.id })),
  ]), [models, t])

  const buildJobConfig = () => {
    if (jobType === 'agent_run') {
      return {
        job_type: 'agent_run', prompt, model: model || null,
        timeout_seconds: Math.max(60, Number(timeoutSec) || 1800),
        max_turns: Math.max(1, Number(maxTurns) || 50),
      }
    }
    if (jobType === 'http_call') {
      return {
        job_type: 'http_call', method, url,
        headers: Object.fromEntries(Object.entries(headers).filter(([k]) => k)),
        body: body || null,
        timeout_seconds: Math.max(1, Number(timeoutSec) || 30),
      }
    }
    return {
      job_type: 'user_script', language, source,
      script: source === 'inline' ? script : null,
      file_path: source === 'file' ? filePath : null,
      timeout_seconds: Math.max(1, Number(timeoutSec) || 300),
    }
  }

  const canSave = name.trim() && preview.valid && !saving && (
    (jobType === 'agent_run' && prompt.trim())
    || (jobType === 'http_call' && url.trim())
    || (jobType === 'user_script' && (source === 'inline' ? script.trim() : filePath.trim()))
  )

  const handleSave = async () => {
    setSaving(true)
    setSaveError(null)
    try {
      await saveJob({
        name: name.trim(),
        trigger,
        timezone: tz,
        job_config: buildJobConfig(),
        model: jobType === 'agent_run' ? (model || null) : null,
        prompt: jobType === 'agent_run' ? prompt : '',
      })
    } catch (err) {
      setSaveError(err?.message || String(err))
      setSaving(false)
    }
  }

  if (!mounted) return null

  const presetOptions = PRESETS.map((p) => ({
    value: p,
    label: t(`scheduler.preset_${p}`, {
      defaultValue: {
        daily: 'Every day at…', weekdays: 'Weekdays at…', weekly: 'Every week on…',
        everyNHours: 'Every N hours', everyNMinutes: 'Every N minutes', custom: 'Custom cron',
      }[p],
    }),
  }))

  return (
    <div className="fixed inset-0" style={{ zIndex: 60 }}>
      <div
        ref={backdropRef}
        onClick={() => setClosing(true)}
        className="absolute inset-0"
        style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(2px)' }}
      />
      <div
        ref={panelRef}
        className="absolute flex flex-col"
        style={{
          top: 0, right: 0, bottom: 0, width: 480, maxWidth: '100vw',
          background: 'var(--bg-base)', borderLeft: '1px solid var(--border)',
        }}
      >
        {/* Header */}
        <div
          className="flex items-center flex-shrink-0 px-4"
          style={{ height: 48, borderBottom: '1px solid var(--border-subtle)' }}
        >
          <span style={{ fontSize: 14, fontWeight: 700, color: 'var(--text-primary)' }}>
            {isEdit ? t('scheduler.editJob') : t('scheduler.createJob')}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={() => setClosing(true)}
            style={{ background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', padding: 4 }}
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto min-w-0" style={{ padding: 16 }}>
          <Field label={t('scheduler.jobType')}>
            {isEdit ? (
              <div style={{ ...INPUT_STYLE, color: 'var(--text-secondary)', cursor: 'default' }}>
                {{ agent_run: 'Agent', http_call: 'HTTP call', user_script: 'Script' }[jobType]}
              </div>
            ) : (
              <Dropdown
                value={jobType}
                onChange={(v) => {
                  setJobType(v)
                  setTimeoutSec(String(v === 'http_call' ? 30 : v === 'user_script' ? 300 : 1800))
                }}
                options={[
                  { value: 'agent_run', label: 'Agent' },
                  { value: 'http_call', label: 'HTTP call' },
                  { value: 'user_script', label: 'Script' },
                ]}
              />
            )}
          </Field>

          <Field label={t('scheduler.name')}>
            <TextInput value={name} onChange={setName} placeholder={t('scheduler.namePlaceholder')} />
          </Field>

          <Field label={t('scheduler.trigger')}>
            <div className="flex flex-col gap-2">
              <Dropdown value={preset} onChange={setPreset} options={presetOptions} />
              {['daily', 'weekdays', 'weekly'].includes(preset) && (
                <div className="flex items-center gap-2">
                  {preset === 'weekly' && (
                    <Dropdown
                      size="sm"
                      value={weekday}
                      onChange={setWeekday}
                      options={WEEKDAYS.map((d) => ({ value: d, label: t(`scheduler.days_${d}`, { defaultValue: d }) }))}
                    />
                  )}
                  <input
                    value={time}
                    onChange={(e) => setTime(e.target.value)}
                    placeholder="09:00"
                    style={{
                      ...MONO_INPUT, width: 90,
                      borderColor: parseTime(time) ? 'var(--border)' : 'var(--red)',
                    }}
                  />
                </div>
              )}
              {['everyNHours', 'everyNMinutes'].includes(preset) && (
                <div className="flex items-center gap-2">
                  <NumberInput value={nValue} onChange={setNValue} />
                  <span style={{ fontSize: 12, color: 'var(--text-secondary)' }}>
                    {preset === 'everyNHours' ? t('scheduler.hours') : t('scheduler.minutes')}
                  </span>
                </div>
              )}
              {preset === 'custom' && (
                <TextInput
                  mono
                  value={cronExpr}
                  onChange={setCronExpr}
                  placeholder="0 9 * * mon-fri"
                  invalid={!preview.valid}
                />
              )}
              {/* Always-live preview line */}
              <div style={{ fontSize: 12, color: preview.valid ? 'var(--text-dim)' : 'var(--red)', fontFamily: 'JetBrains Mono, monospace' }}>
                {preview.valid
                  ? `≈ ${describeTrigger(trigger, t)}${preview.next ? ` · ${t('scheduler.nextRun').toLowerCase()} ${new Date(preview.next).toLocaleString([], { weekday: 'short', hour: '2-digit', minute: '2-digit', month: 'short', day: 'numeric' })}` : ''}`
                  : preview.error || t('scheduler.invalidTrigger', { defaultValue: 'invalid trigger' })}
              </div>
            </div>
          </Field>

          <Field label={t('scheduler.timezone')}>
            <Dropdown searchable value={tz} onChange={setTz} options={tzOptions} />
          </Field>

          {jobType === 'agent_run' && (
            <>
              <Field label={t('scheduler.prompt')}>
                <textarea
                  value={prompt}
                  onChange={(e) => setPrompt(e.target.value)}
                  placeholder={t('scheduler.promptPlaceholder')}
                  rows={5}
                  style={{ ...INPUT_STYLE, resize: 'vertical', minHeight: 90 }}
                />
              </Field>
              <Field label={t('scheduler.model')}>
                <Dropdown searchable value={model} onChange={setModel} options={modelOptions} />
              </Field>
              <button
                type="button"
                onClick={() => setAdvancedOpen((v) => !v)}
                className="inline-flex items-center gap-1"
                style={{
                  background: 'transparent', border: 'none', cursor: 'pointer',
                  fontSize: 12, color: 'var(--text-secondary)', padding: 0, marginBottom: 10,
                }}
              >
                {advancedOpen ? <ChevronDown size={12} strokeWidth={1.5} /> : <ChevronRight size={12} strokeWidth={1.5} />}
                {t('scheduler.advanced', { defaultValue: 'Advanced' })}
              </button>
              {advancedOpen && (
                <div className="flex items-center gap-4" style={{ marginBottom: 14 }}>
                  <div>
                    <span style={FIELD_LABEL}>{t('scheduler.timeoutSeconds', { defaultValue: 'Timeout (s)' })}</span>
                    <NumberInput value={timeoutSec} onChange={setTimeoutSec} min={60} />
                  </div>
                  <div>
                    <span style={FIELD_LABEL}>{t('scheduler.maxTurns', { defaultValue: 'Max turns' })}</span>
                    <NumberInput value={maxTurns} onChange={setMaxTurns} />
                  </div>
                </div>
              )}
            </>
          )}

          {jobType === 'http_call' && (
            <>
              <Field label={t('scheduler.method', { defaultValue: 'Method' })}>
                <Dropdown
                  size="sm"
                  value={method}
                  onChange={setMethod}
                  options={['GET', 'POST', 'PUT', 'DELETE'].map((m) => ({ value: m, label: m }))}
                  mono
                />
              </Field>
              <Field label="URL">
                <TextInput mono value={url} onChange={setUrl} placeholder="https://example.com/health" />
              </Field>
              <Field label={t('scheduler.headers', { defaultValue: 'Headers' })}>
                <HeaderRows headers={headers} setHeaders={setHeaders} t={t} />
              </Field>
              <Field label={t('scheduler.body', { defaultValue: 'Body' })}>
                <textarea
                  value={body}
                  onChange={(e) => setBody(e.target.value)}
                  rows={3}
                  style={{ ...MONO_INPUT, resize: 'vertical' }}
                />
              </Field>
              <Field label={t('scheduler.timeoutSeconds', { defaultValue: 'Timeout (s)' })}>
                <NumberInput value={timeoutSec} onChange={setTimeoutSec} />
              </Field>
            </>
          )}

          {jobType === 'user_script' && (
            <>
              <Field label={t('scheduler.language', { defaultValue: 'Language' })}>
                <Dropdown
                  size="sm"
                  value={language}
                  onChange={setLanguage}
                  options={[{ value: 'python', label: 'python' }, { value: 'shell', label: 'shell' }]}
                  mono
                />
              </Field>
              <Field label={t('scheduler.source', { defaultValue: 'Source' })}>
                <Dropdown
                  size="sm"
                  value={source}
                  onChange={setSource}
                  options={[
                    { value: 'inline', label: t('scheduler.inline', { defaultValue: 'inline' }) },
                    { value: 'file', label: t('scheduler.file', { defaultValue: 'workspace file' }) },
                  ]}
                />
              </Field>
              {source === 'inline' ? (
                <Field label={t('scheduler.script', { defaultValue: 'Script' })}>
                  <textarea
                    value={script}
                    onChange={(e) => setScript(e.target.value)}
                    rows={7}
                    spellCheck={false}
                    style={{ ...MONO_INPUT, resize: 'vertical', minHeight: 120 }}
                  />
                </Field>
              ) : (
                <Field label={t('scheduler.filePath', { defaultValue: 'File path (relative to workspace)' })}>
                  <TextInput mono value={filePath} onChange={setFilePath} placeholder="scripts/backup.py" />
                </Field>
              )}
              <Field label={t('scheduler.timeoutSeconds', { defaultValue: 'Timeout (s)' })}>
                <NumberInput value={timeoutSec} onChange={setTimeoutSec} />
              </Field>
            </>
          )}

          {isEdit && (
            <button
              type="button"
              onClick={() => onDelete(editingJob)}
              className="inline-flex items-center gap-1"
              style={{
                marginTop: 8, fontSize: 12, color: 'var(--red)',
                background: 'transparent', border: '1px solid var(--border)',
                borderRadius: 4, padding: '4px 10px', cursor: 'pointer',
                transition: 'border-color 150ms ease',
              }}
              onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--red)' }}
              onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
            >
              <Trash2 size={14} strokeWidth={1.5} />
              {t('scheduler.deleteJob', { defaultValue: 'Delete job' })}
            </button>
          )}

          {saveError && (
            <div style={{ marginTop: 12, fontSize: 12, color: 'var(--red)', wordBreak: 'break-word' }}>
              {saveError}
            </div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex items-center justify-end gap-2 flex-shrink-0 px-4"
          style={{ height: 56, borderTop: '1px solid var(--border-subtle)' }}
        >
          <button
            type="button"
            onClick={() => setClosing(true)}
            style={{
              fontSize: 12, padding: '6px 14px', borderRadius: 4,
              border: '1px solid var(--border)', background: 'var(--bg-surface)',
              color: 'var(--text-secondary)', cursor: 'pointer',
              transition: 'background 150ms ease',
            }}
          >
            {t('scheduler.cancel')}
          </button>
          <button
            type="button"
            disabled={!canSave}
            onClick={handleSave}
            style={{
              fontSize: 12, padding: '6px 14px', borderRadius: 4, border: '1px solid transparent',
              background: canSave ? 'var(--blue)' : 'var(--bg-elevated)',
              color: canSave ? 'var(--text-inverse)' : 'var(--text-dim)',
              cursor: canSave ? 'pointer' : 'default', fontWeight: 600,
              transition: 'background 150ms ease',
            }}
          >
            {saving ? '…' : isEdit ? t('scheduler.done', { defaultValue: 'Done' }) : t('scheduler.createJob')}
          </button>
        </div>
      </div>
    </div>
  )
}
