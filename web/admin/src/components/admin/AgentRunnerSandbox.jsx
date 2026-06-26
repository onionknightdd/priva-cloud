import { useState, useEffect, useRef, useCallback } from 'react'
import { Clock, Cpu, ShieldCheck, Package, Check, Loader } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { SlidingTabGroup, SlidingTabIndicator } from '@shared/components/shared/Tabs'
import Dropdown from '@shared/components/shared/Dropdown'
import { getRunnerDefaults, updateRunnerDefaults, getRunnerImages } from '@shared/api/admin'

// Agent Runner Sandbox — the platform-wide GLOBAL defaults for per-account agent-runner
// pods. An account inherits each value here unless it carries a per-account override
// (set in the Accounts view). Changes apply LAZILY: the operator picks them up on a
// pod's next (re)start — never force-restarting a running runner. Two columns: a left
// "index" of config groups + a right scrollable "detail" (scroll-spy linked).

const inputStyle = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  color: 'var(--text-primary)',
  fontSize: 13,
  fontFamily: "'Noto Sans', sans-serif",
  outline: 'none',
  boxSizing: 'border-box',
}

const fieldTitleStyle = {
  color: 'var(--text-secondary)',
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

const FIELD_LABEL_WIDTH = 240
const FIELD_BOX_WIDTH = 260

// field id == the runner-defaults API key. type 'int' renders a number input + a unit
// label OUTSIDE the box; 'image' a shared Dropdown fed by cluster discovery; 'segment'
// a greyed-out (next-phase) control. CPU is millicores end-to-end (digit-only UI).
const GROUPS = [
  {
    id: 'lifecycle',
    labelKey: 'admin.sandboxLifecycle',
    icon: Clock,
    fields: [
      { id: 'idle_grace_seconds', labelKey: 'admin.sandboxIdleGrace', type: 'int', unit: 's', hintKey: 'admin.sandboxIdleGraceHint' },
      { id: 'min_alive_after_wake_seconds', labelKey: 'admin.sandboxMinAlive', type: 'int', unit: 's', hintKey: 'admin.sandboxMinAliveHint' },
    ],
  },
  {
    id: 'resources',
    labelKey: 'admin.sandboxResources',
    icon: Cpu,
    fields: [
      { id: 'cpu_millicores', labelKey: 'admin.sandboxCpuQuota', type: 'int', unit: 'm', hintKey: 'admin.sandboxCpuQuotaHint' },
      { id: 'memory_mb', labelKey: 'admin.sandboxMemoryQuota', type: 'int', unit: 'Mi', hintKey: 'admin.sandboxMemoryQuotaHint' },
      { id: 'storage_gb', labelKey: 'admin.sandboxWorkspaceQuota', type: 'int', unit: 'Gi', hintKey: 'admin.sandboxWorkspaceQuotaHint' },
    ],
  },
  {
    id: 'isolation',
    labelKey: 'admin.sandboxIsolation',
    icon: ShieldCheck,
    fields: [
      { id: 'egress', labelKey: 'admin.sandboxNetworkEgress', type: 'segment', disabled: true, options: [{ value: 'allow', labelKey: 'admin.allow' }, { value: 'deny', labelKey: 'admin.deny' }], hintKey: 'admin.sandboxNetworkEgressHint' },
    ],
  },
  {
    id: 'image',
    labelKey: 'admin.sandboxImage',
    icon: Package,
    fields: [
      { id: 'runner_image', labelKey: 'admin.sandboxAgentRunnerImage', type: 'image', hintKey: 'admin.sandboxAgentRunnerImageHint' },
    ],
  },
]

// Persistable API keys per group (egress is not persisted this phase).
const GROUP_KEYS = {
  lifecycle: ['idle_grace_seconds', 'min_alive_after_wake_seconds'],
  resources: ['cpu_millicores', 'memory_mb', 'storage_gb'],
  isolation: [],
  image: ['runner_image'],
}

const SPY_SUPPRESS_MS = 600

export default function AgentRunnerSandbox() {
  const { t } = useTranslation()
  const scrollRef = useRef(null)
  const sectionRefs = useRef({})
  const suppressSpy = useRef(false)
  const [active, setActive] = useState(GROUPS[0].id)

  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [values, setValues] = useState({})       // current edited values (by API key)
  const [baseline, setBaseline] = useState({})    // last-saved values (dirty diff)
  const [images, setImages] = useState([])
  const [saving, setSaving] = useState(null)      // group id currently saving
  const [savedAt, setSavedAt] = useState(null)    // group id that just saved (Check flash)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getRunnerDefaults()
      const v = {
        idle_grace_seconds: d.idle_grace_seconds,
        min_alive_after_wake_seconds: d.min_alive_after_wake_seconds,
        cpu_millicores: d.cpu_millicores,
        memory_mb: d.memory_mb,
        storage_gb: d.storage_gb,
        runner_image: d.runner_image,
        egress: 'allow',
      }
      setValues(v)
      setBaseline(v)
    } catch (e) {
      setError(e?.message || t('admin.sandboxLoadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => { load() }, [load])

  // Image list for the dropdown (best-effort; the current default is always included).
  useEffect(() => {
    let alive = true
    getRunnerImages()
      .then((r) => { if (alive) setImages(r?.images || []) })
      .catch(() => { /* dropdown falls back to the current value */ })
    return () => { alive = false }
  }, [])

  const setField = useCallback((fieldId, val) => {
    setValues((prev) => ({ ...prev, [fieldId]: val }))
  }, [])

  const groupDirty = useCallback((groupId) => (
    GROUP_KEYS[groupId].some((k) => String(values[k] ?? '') !== String(baseline[k] ?? ''))
  ), [values, baseline])

  const saveGroup = useCallback(async (groupId) => {
    const payload = {}
    for (const k of GROUP_KEYS[groupId]) {
      if (k === 'runner_image') {
        payload[k] = String(values[k] ?? '').trim()
      } else {
        const n = parseInt(values[k], 10)
        if (Number.isNaN(n)) continue
        payload[k] = n
      }
    }
    if (Object.keys(payload).length === 0) return
    setSaving(groupId)
    setError(null)
    try {
      const d = await updateRunnerDefaults(payload)
      // Adopt the server-canonical values (CPU may round) as the new baseline.
      const next = { ...payload, cpu_millicores: d.cpu_millicores }
      setValues((prev) => ({ ...prev, ...next }))
      setBaseline((prev) => ({ ...prev, ...next }))
      setSavedAt(groupId)
      setTimeout(() => setSavedAt((g) => (g === groupId ? null : g)), 1500)
    } catch (e) {
      setError(e?.message || t('admin.sandboxSaveFailed'))
    } finally {
      setSaving((g) => (g === groupId ? null : g))
    }
  }, [values, t])

  // Scroll-spy: the active index follows whichever section sits near the top.
  useEffect(() => {
    const root = scrollRef.current
    if (!root || typeof IntersectionObserver === 'undefined') return
    const obs = new IntersectionObserver(
      (entries) => {
        if (suppressSpy.current) return
        const visible = entries.filter((e) => e.isIntersecting)
        if (!visible.length) return
        visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        const id = visible[0].target.dataset.groupId
        if (id) setActive(id)
      },
      { root, rootMargin: '0px 0px -70% 0px', threshold: 0 },
    )
    Object.values(sectionRefs.current).forEach((el) => el && obs.observe(el))
    return () => obs.disconnect()
  }, [loading])

  const goTo = useCallback((id) => {
    setActive(id)
    const el = sectionRefs.current[id]
    const root = scrollRef.current
    if (!el || !root) return
    suppressSpy.current = true
    const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    root.scrollTo({ top, behavior: 'smooth' })
    window.setTimeout(() => { suppressSpy.current = false }, SPY_SUPPRESS_MS)
  }, [])

  const renderField = (f) => {
    const val = values[f.id]
    const hint = f.hintKey && (
      <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4, fontWeight: 300 }}>{t(f.hintKey)}</div>
    )

    let control
    if (f.type === 'segment') {
      control = (
        <div className="flex gap-2" style={{ opacity: f.disabled ? 0.5 : 1 }}>
          {f.options.map((opt) => {
            const on = (val ?? 'allow') === opt.value
            return (
              <span
                key={opt.value}
                className="flex items-center gap-2 px-3 py-2 text-sm"
                style={{
                  background: on ? 'var(--bg-elevated)' : 'var(--bg-surface)',
                  border: `1px solid ${on ? 'var(--blue)' : 'var(--border)'}`,
                  borderRadius: 4,
                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontWeight: on ? 600 : 400,
                  cursor: 'not-allowed',
                }}
              >
                {on && <Check size={14} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />}
                {t(opt.labelKey)}
              </span>
            )
          })}
        </div>
      )
    } else if (f.type === 'image') {
      const opts = images.map((tag) => ({ value: tag, label: tag }))
      if (val && !opts.some((o) => o.value === val)) opts.unshift({ value: val, label: val })
      control = (
        <Dropdown
          size="sm"
          mono
          searchable
          value={val}
          onChange={(v) => setField(f.id, v)}
          options={opts}
          placeholder={t('admin.sandboxSelectImage')}
        />
      )
    } else {
      control = (
        <div className="flex items-center gap-2">
          <input
            type="number"
            value={val ?? ''}
            onChange={(e) => setField(f.id, e.target.value)}
            style={{ ...inputStyle, flex: 1, minWidth: 0, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }}
          />
          {f.unit && (
            <span className="flex-shrink-0" style={{ color: 'var(--text-dim)', fontSize: 12, fontFamily: "'JetBrains Mono', monospace" }}>{f.unit}</span>
          )}
        </div>
      )
    }

    return (
      <div key={f.id} className="flex items-center gap-4">
        <div style={{ width: FIELD_LABEL_WIDTH, flexShrink: 0 }}>
          <div style={fieldTitleStyle}>{t(f.labelKey)}</div>
          {hint}
        </div>
        <div style={{ width: FIELD_BOX_WIDTH, flexShrink: 0 }}>
          {control}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      <style>{`
        @keyframes ars-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }
        @keyframes ars-shimmer { 0% { background-position: -400px 0; } 100% { background-position: 400px 0; } }
        .ars-skeleton { border-radius: 2px; background: linear-gradient(90deg, var(--bg-elevated) 25%, var(--bg-surface) 50%, var(--bg-elevated) 75%); background-size: 800px 100%; animation: ars-shimmer 1.4s ease infinite; }
      `}</style>
      {/* Page header */}
      <div className="flex-shrink-0" style={{ padding: '20px 24px 12px 24px' }}>
        <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>{t('admin.sandboxTitle')}</h2>
        <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
          {t('admin.sandboxDescription')}
        </p>
        {error && (
          <p className="text-xs" style={{ color: 'var(--red)', marginTop: 6 }}>{error}</p>
        )}
      </div>

      {/* Two columns: index + detail */}
      <div className="flex flex-1 min-h-0">
        {/* Index — top-aligned; active = elevated band + 2px blue left bar */}
        <div
          className="flex-shrink-0 overflow-y-auto"
          style={{ width: 216, borderRight: '1px solid var(--border)', padding: '8px 0 8px 16px' }}
        >
          <SlidingTabGroup id="sandbox-index">
            <div className="flex flex-col gap-1">
              {GROUPS.map((g) => {
                const isActive = active === g.id
                const Icon = g.icon
                return (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => goTo(g.id)}
                    className="relative flex items-center text-left"
                    style={{
                      width: '100%',
                      padding: '10px 16px',
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                      fontSize: 13,
                      fontWeight: isActive ? 600 : 400,
                      transition: 'color 150ms ease',
                    }}
                    onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.color = 'var(--text-primary)' }}
                    onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.color = 'var(--text-secondary)' }}
                  >
                    {isActive && (
                      <SlidingTabIndicator
                        variant="frame"
                        layoutId="sandbox-index-indicator"
                        style={{ border: 'none', borderLeft: '2px solid var(--blue)', borderRadius: 0 }}
                      />
                    )}
                    <span className="relative flex items-center gap-2" style={{ zIndex: 1, minWidth: 0 }}>
                      <Icon size={16} strokeWidth={1.5} className="flex-shrink-0" />
                      <span className="truncate">{t(g.labelKey)}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </SlidingTabGroup>
        </div>

        {/* Detail */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 50vh 24px' }}>
          <div style={{ maxWidth: 560, margin: '0 auto' }}>
            {loading ? (
              <div className="flex flex-col gap-6">
                {[0, 1, 2].map((i) => (
                  <div key={i} className="flex flex-col gap-3">
                    <div className="ars-skeleton" style={{ height: 16, width: 120 }} />
                    <div className="ars-skeleton" style={{ height: 36, width: '100%' }} />
                    <div className="ars-skeleton" style={{ height: 36, width: '100%' }} />
                  </div>
                ))}
              </div>
            ) : (
              GROUPS.map((g) => {
                const Icon = g.icon
                const keys = GROUP_KEYS[g.id]
                const dirty = keys.length > 0 && groupDirty(g.id)
                const isSaving = saving === g.id
                const justSaved = savedAt === g.id
                return (
                  <section
                    key={g.id}
                    data-group-id={g.id}
                    ref={(el) => { sectionRefs.current[g.id] = el }}
                    style={{ marginBottom: 32 }}
                  >
                    <div className="flex items-center gap-2" style={{ marginBottom: 14 }}>
                      <Icon size={16} strokeWidth={1.5} style={{ color: 'var(--text-secondary)' }} />
                      <h3 className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14, margin: 0 }}>{t(g.labelKey)}</h3>
                    </div>
                    <div className="flex flex-col gap-4">
                      {g.fields.map((f) => renderField(f))}
                    </div>
                    {keys.length > 0 && (
                      <div className="flex items-center justify-end gap-3" style={{ marginTop: 14 }}>
                        {justSaved && (
                          <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--green)' }}>
                            <Check size={14} strokeWidth={1.5} /> {t('admin.sandboxSaved')}
                          </span>
                        )}
                        <button
                          type="button"
                          className="flex items-center gap-2 px-4 py-2 text-xs font-semibold"
                          disabled={!dirty || isSaving}
                          style={{
                            background: dirty && !isSaving ? 'var(--blue)' : 'var(--bg-elevated)',
                            color: dirty && !isSaving ? 'var(--text-inverse)' : 'var(--text-dim)',
                            border: 'none',
                            borderRadius: 4,
                            cursor: dirty && !isSaving ? 'pointer' : 'default',
                            opacity: dirty && !isSaving ? 1 : 0.5,
                            transition: 'opacity 150ms ease',
                          }}
                          onClick={() => saveGroup(g.id)}
                        >
                          {isSaving && <Loader size={14} strokeWidth={1.5} style={{ animation: 'ars-spin 1s linear infinite' }} />}
                          {t('admin.save')}
                        </button>
                      </div>
                    )}
                  </section>
                )
              })
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
