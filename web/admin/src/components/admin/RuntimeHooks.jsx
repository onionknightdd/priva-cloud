import { Suspense, useState, useEffect, useLayoutEffect, useCallback, useMemo, useRef } from 'react'
import {
  ChevronRight, ChevronDown, Plus, Loader, Check, X, AlertTriangle, Trash2,
  ShieldOff, ShieldCheck, Pencil,
} from 'lucide-react'
import { animate, svg, utils } from 'animejs'
import { useTranslation } from 'react-i18next'
import Dropdown from '@shared/components/shared/Dropdown'
import useOverlayTransition from '@shared/motion/useOverlayTransition'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { EASE_OUT } from '@shared/motion/tokens'
import { useResizable } from '@shared/hooks/useResizable'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'
import {
  listHookPolicies, createHookPolicy, updateHookPolicy, deleteHookPolicy,
  validateHookPolicy, getHookPolicySeed,
} from '@shared/api/admin'

const ScriptEditor = lazyWithChunkReload(() => import('@user/components/shared/ScriptEditor'))

// Agent Runner Sandbox › Runtime — admin-stored hooks grouped by event. Rows
// carry a staged "draft" (Enable/Enforce flips + drawer Done) committed via
// per-group [Save (n)]; custom-row Delete is immediate after a typed-name
// confirm. Predefined (seeded) rows can be edited & disabled but not deleted.
// Enforce-on shows a confirm dialog at flip time. Script bodies never leave the
// admin surface (the user catalog shows description only).

const HOOK_TYPES = ['command', 'http', 'mcp_tool']
const INTERPRETERS = ['bash', 'python3']

const badgeStyle = {
  fontSize: 9, fontWeight: 600, letterSpacing: '0.06em', textTransform: 'uppercase',
  padding: '0 4px', borderRadius: 2,
}

const inputStyle = {
  width: '100%', padding: '7px 10px', background: 'var(--bg-elevated)',
  border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-primary)',
  fontSize: 13, fontFamily: "'Noto Sans', sans-serif", outline: 'none', boxSizing: 'border-box',
}

const monoInputStyle = { ...inputStyle, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 }

const labelStyle = {
  color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600,
  textTransform: 'uppercase', letterSpacing: '0.06em', marginBottom: 6, display: 'block',
}

function EnforceButton({ on, onClick, label, disabled }) {
  const Icon = on ? ShieldCheck : ShieldOff
  const iconRef = useRef(null)
  const previousOnRef = useRef(on)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    const changed = previousOnRef.current !== on
    previousOnRef.current = on
    if (!changed || reducedMotion) return undefined

    const paths = iconRef.current?.querySelectorAll('path')
    if (!paths?.length) return undefined
    const drawables = svg.createDrawable(paths)
    utils.set(drawables, { draw: '0 0' })
    const animation = animate(drawables, { draw: '0 1', duration: 150, ease: EASE_OUT })
    return () => animation.cancel()
  }, [on, reducedMotion])

  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      title={label}
      aria-label={label}
      className="rh-action-button flex items-center justify-center flex-shrink-0"
      style={{
        width: 30, height: 30,
        background: 'transparent', border: 'none',
        borderRadius: 4, color: on ? 'var(--blue)' : 'var(--text-secondary)',
        cursor: disabled ? 'not-allowed' : 'pointer', opacity: disabled ? 0.5 : 1,
        transition: 'color 150ms ease, background 150ms ease',
      }}
    >
      <span ref={iconRef} className="flex items-center justify-center">
        <Icon size={14} strokeWidth={1.5} />
      </span>
    </button>
  )
}

function ConfirmDialog({ title, body, confirmLabel, danger, onConfirm, onCancel, children }) {
  const { t } = useTranslation()
  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open: true, variant: 'modal' })
  if (!mounted) return null
  return (
    <>
      <div ref={backdropRef} className="fixed inset-0"
        style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 300 }}
        onClick={onCancel} />
      <div ref={panelRef} className="fixed" style={{
        top: '50%', left: '50%', transform: 'translate(-50%, -50%)', width: 420,
        background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4,
        zIndex: 301, padding: 20,
      }}>
        <div className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14, marginBottom: 8 }}>
          {title}
        </div>
        <div className="text-sm" style={{ color: 'var(--text-secondary)', marginBottom: 16 }}>{body}</div>
        {children}
        <div className="flex items-center justify-end gap-3" style={{ marginTop: 16 }}>
          <button type="button" onClick={onCancel} className="px-3 py-2 text-xs"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}>
            {t('admin.hookCancel')}
          </button>
          <button type="button" onClick={onConfirm} className="px-4 py-2 text-xs font-semibold"
            style={{ background: danger ? 'var(--red)' : 'var(--blue)', border: 'none', borderRadius: 4, color: 'var(--text-inverse)', cursor: 'pointer' }}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </>
  )
}

// --- Drawer editor (480px right slide-in) ------------------------------------

const EMPTY_DRAFT = {
  id: '', hook_type: 'command', name: '', description: '', events: [], matcher: '',
  timeout_seconds: '', interpreter: 'bash', script_body: '', url: '', headers_json: '',
  allowed_env_vars: [], enforced: false, default_on: false,
}

const HOOK_DRAWER_WIDTH = 480
const HOOK_DRAWER_MIN_WIDTH = 360
const HOOK_DRAWER_MAX_WIDTH = 760

function HookDrawer({ open, mode, policy, presetEvent, supportedEvents, onClose, onCommit, onDelete }) {
  const { t } = useTranslation()
  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'drawer' })
  const [draft, setDraft] = useState(EMPTY_DRAFT)
  const [validation, setValidation] = useState(null) // {valid, errors:[{field,message,line}]}
  const [validating, setValidating] = useState(false)
  const [envInput, setEnvInput] = useState('')
  const [seed, setSeed] = useState(null)
  const [showDiff, setShowDiff] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [drawerWidth, setDrawerWidth] = useState(HOOK_DRAWER_WIDTH)
  const { dragging, onMouseDown } = useResizable({
    initial: drawerWidth,
    min: HOOK_DRAWER_MIN_WIDTH,
    max: HOOK_DRAWER_MAX_WIDTH,
    direction: 'left',
    onResize: setDrawerWidth,
  })

  useEffect(() => {
    if (!open) return
    if (mode === 'create') {
      setDraft({ ...EMPTY_DRAFT, events: presetEvent ? [presetEvent] : [] })
    } else if (policy) {
      setDraft({
        id: policy.id, hook_type: policy.hook_type, name: policy.name || '',
        description: policy.description || '', events: [...(policy.events || [])],
        matcher: policy.matcher || '', timeout_seconds: String(policy.timeout_seconds ?? ''),
        interpreter: policy.interpreter || 'bash', script_body: policy.script_body || '',
        url: policy.url || '', headers_json: policy.headers_json || '',
        allowed_env_vars: [...(policy.allowed_env_vars || [])],
        enforced: policy.enforced, default_on: policy.default_on,
      })
    }
    setValidation(null)
    setShowDiff(false)
    setDeleting(false)
  }, [open, mode, policy, presetEvent])

  useEffect(() => {
    if (open && mode === 'edit' && policy?.seed_state === 'outdated') {
      getHookPolicySeed(policy.id).then(setSeed).catch(() => setSeed(null))
    } else {
      setSeed(null)
    }
  }, [open, mode, policy])

  useEffect(() => {
    if (open) setDrawerWidth(HOOK_DRAWER_WIDTH)
  }, [open])

  if (!mounted) return null

  const set = (patch) => setDraft((d) => ({ ...d, ...patch }))
  const isCommand = draft.hook_type === 'command'
  const isHttp = draft.hook_type === 'http'
  const isMcp = draft.hook_type === 'mcp_tool'
  const predefined = mode === 'edit' && policy?.predefined
  const fieldError = (name) => validation?.errors?.find((e) => e.field === name)

  const toggleEvent = (ev) => set({
    events: draft.events.includes(ev) ? draft.events.filter((e) => e !== ev) : [...draft.events, ev],
  })

  const runValidate = async () => {
    setValidating(true)
    try {
      const res = await validateHookPolicy({
        id: mode === 'create' ? draft.id : undefined,
        hook_type: draft.hook_type, name: draft.name, description: draft.description,
        events: draft.events, matcher: draft.matcher,
        timeout_seconds: draft.timeout_seconds ? Number(draft.timeout_seconds) : undefined,
        interpreter: draft.interpreter, script_body: draft.script_body,
        url: draft.url, headers_json: draft.headers_json, allowed_env_vars: draft.allowed_env_vars,
      })
      setValidation(res)
      return res.valid
    } finally {
      setValidating(false)
    }
  }

  const handleDone = async () => {
    if (!(await runValidate())) return
    onCommit(draft, mode)
  }

  const addEnv = () => {
    const v = envInput.trim()
    if (v && !draft.allowed_env_vars.includes(v)) set({ allowed_env_vars: [...draft.allowed_env_vars, v] })
    setEnvInput('')
  }

  return (
    <>
      <div ref={backdropRef} className="fixed inset-0"
        style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 200, pointerEvents: open ? 'auto' : 'none' }}
        onClick={onClose} />
      <div ref={panelRef} className="fixed top-0 right-0 bottom-0 flex flex-col"
        style={{ width: drawerWidth, background: 'var(--bg-surface)', borderLeft: '1px solid var(--border)', zIndex: 201 }}>
        {mode === 'edit' && (
          <div
            onMouseDown={onMouseDown}
            style={{
              position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
              cursor: 'col-resize', background: dragging ? 'var(--blue)' : 'transparent',
              transition: 'background 100ms ease', zIndex: 10,
            }}
            onMouseEnter={(e) => { if (!dragging) e.currentTarget.style.background = 'var(--blue)' }}
            onMouseLeave={(e) => { if (!dragging) e.currentTarget.style.background = 'transparent' }}
          />
        )}
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 min-w-0">
            <span className="font-semibold truncate" style={{ color: 'var(--text-primary)', fontSize: 14 }}>
              {mode === 'create' ? t('admin.hookDrawerNewTitle') : draft.name || draft.id}
            </span>
            {predefined && <span style={{ ...badgeStyle, color: 'var(--cyan)', border: '1px solid var(--cyan)' }}>{t('admin.hookPredefined')}</span>}
          </div>
          <button onClick={onClose} style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)' }}>
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto" style={{ padding: '16px 20px' }}>
          {seed && (
            <div className="flex items-center justify-between px-3 py-2" style={{
              background: 'var(--bg-elevated)', border: '1px solid var(--orange)', borderRadius: 4, marginBottom: 16,
            }}>
              <span className="flex items-center gap-2 text-xs" style={{ color: 'var(--orange)' }}>
                <AlertTriangle size={14} strokeWidth={1.5} />
                {t('admin.hookSeedOutdated')} · {t('admin.hookSeedShipped', { v: seed.seed_version })}
              </span>
              <button onClick={() => setShowDiff((s) => !s)} className="text-xs"
                style={{ background: 'transparent', border: 'none', color: 'var(--blue)', cursor: 'pointer' }}>
                {t('admin.hookViewDiff')}
              </button>
            </div>
          )}
          {showDiff && seed && (
            <pre style={{ ...monoInputStyle, whiteSpace: 'pre-wrap', maxHeight: 160, overflow: 'auto', marginBottom: 16 }}>
              {seed.script_body}
            </pre>
          )}

          {mode === 'create' && (
            <Field label={t('admin.hookFieldId')} error={fieldError('id')}>
              <input style={monoInputStyle} value={draft.id} placeholder="my-hook"
                onChange={(e) => set({ id: e.target.value })} />
            </Field>
          )}
          <Field label={t('admin.hookFieldName')} error={fieldError('name')}>
            <input style={inputStyle} value={draft.name} onChange={(e) => set({ name: e.target.value })} />
          </Field>
          <Field label={t('admin.hookFieldDescription')} error={fieldError('description')}>
            <textarea style={{ ...inputStyle, minHeight: 52, resize: 'vertical' }} value={draft.description}
              onChange={(e) => set({ description: e.target.value })} />
          </Field>

          <Field label={t('admin.hookFieldType')} error={fieldError('hook_type')}>
            <Dropdown size="sm" value={draft.hook_type} onChange={(v) => set({ hook_type: v })}
              options={HOOK_TYPES.map((v) => ({ value: v, label: v, disabled: v === 'mcp_tool' }))} />
            {isMcp && <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>{t('admin.hookMcpSoon')}</div>}
          </Field>

          <Field label={t('admin.hookFieldEvents')} error={fieldError('events')}>
            <div className="flex flex-wrap gap-2">
              {supportedEvents.map((ev) => {
                const on = draft.events.includes(ev)
                return (
                  <button key={ev} type="button" onClick={() => toggleEvent(ev)} className="px-2 py-1 text-xs"
                    style={{
                      background: on ? 'var(--bg-elevated)' : 'var(--bg-surface)',
                      border: `1px solid ${on ? 'var(--cyan)' : 'var(--border)'}`, borderRadius: 4,
                      color: on ? 'var(--text-primary)' : 'var(--text-secondary)', cursor: 'pointer',
                    }}>
                    {ev}
                  </button>
                )
              })}
            </div>
          </Field>

          <div className="flex gap-3">
            <Field label={t('admin.hookFieldMatcher')} error={fieldError('matcher')} style={{ flex: 1 }}>
              <input style={monoInputStyle} value={draft.matcher} placeholder="Bash|Write"
                onChange={(e) => set({ matcher: e.target.value })} />
            </Field>
            <Field label={t('admin.hookFieldTimeout')} error={fieldError('timeout_seconds')} style={{ width: 96 }}>
              <input type="number" style={monoInputStyle} value={draft.timeout_seconds}
                onChange={(e) => set({ timeout_seconds: e.target.value })} />
            </Field>
          </div>

          {isCommand && (
            <>
              <Field label={t('admin.hookFieldInterpreter')} error={fieldError('interpreter')}>
                <Dropdown size="sm" value={draft.interpreter} onChange={(v) => set({ interpreter: v })}
                  options={INTERPRETERS.map((v) => ({ value: v, label: v }))} />
              </Field>
              <Field label={t('admin.hookFieldScript')} error={fieldError('script_body')}>
                <Suspense fallback={<div className="skeleton" style={{ height: 200, borderRadius: 2 }} />}>
                  <ScriptEditor
                    value={draft.script_body}
                    onChange={(script_body) => set({ script_body })}
                    language={draft.interpreter === 'python3' ? 'python' : 'shell'}
                    minHeight={200}
                    maxHeight={360}
                  />
                </Suspense>
              </Field>
            </>
          )}

          {isHttp && (
            <>
              <Field label={t('admin.hookFieldUrl')} error={fieldError('url')}>
                <input style={monoInputStyle} value={draft.url} placeholder="https://…"
                  onChange={(e) => set({ url: e.target.value })} />
              </Field>
              <Field label={t('admin.hookFieldHeaders')} error={fieldError('headers_json')}>
                <textarea spellCheck={false} value={draft.headers_json} placeholder='{"Authorization": "Bearer $TOKEN"}'
                  onChange={(e) => set({ headers_json: e.target.value })}
                  style={{ ...monoInputStyle, minHeight: 60, resize: 'vertical' }} />
              </Field>
              {draft.events.includes('PreToolUse') && (
                <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--orange)', marginBottom: 12 }}>
                  <AlertTriangle size={12} strokeWidth={1.5} /> {t('admin.hookHttpLatencyWarn')}
                </div>
              )}
            </>
          )}

          <Field label={t('admin.hookFieldEnvPassthrough')} error={fieldError('allowed_env_vars')}>
            <div className="flex flex-wrap items-center gap-2">
              {draft.allowed_env_vars.map((v) => (
                <span key={v} className="flex items-center gap-1 px-2 py-1 text-xs" style={{
                  background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4,
                  color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace",
                }}>
                  {v}
                  <button onClick={() => set({ allowed_env_vars: draft.allowed_env_vars.filter((x) => x !== v) })}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 0 }}>
                    <X size={12} strokeWidth={1.5} />
                  </button>
                </span>
              ))}
              <input style={{ ...monoInputStyle, width: 120 }} value={envInput} placeholder="VAR_NAME"
                onChange={(e) => setEnvInput(e.target.value)}
                onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); addEnv() } }} />
            </div>
          </Field>

          {validation && !validation.valid && (
            <div className="text-xs" style={{ color: 'var(--red)', marginTop: 8 }}>
              {validation.errors.map((e, i) => (
                <div key={i}>{e.field}{e.line ? ` (line ${e.line})` : ''}: {e.message}</div>
              ))}
            </div>
          )}
          {validation?.valid && (
            <div className="flex items-center gap-1 text-xs" style={{ color: 'var(--green)', marginTop: 8 }}>
              <Check size={12} strokeWidth={1.5} /> {t('admin.hookValidateOk')}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-between px-5 py-3 flex-shrink-0" style={{ borderTop: '1px solid var(--border)' }}>
          <div>
            {mode === 'edit' && !predefined && (
              <button onClick={() => setDeleting(true)} className="flex items-center gap-1 px-3 py-2 text-xs"
                style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--red)', cursor: 'pointer' }}>
                <Trash2 size={12} strokeWidth={1.5} /> {t('admin.hookDelete')}
              </button>
            )}
          </div>
          <div className="flex items-center gap-3">
            <button onClick={runValidate} disabled={validating} className="px-3 py-2 text-xs"
              style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}>
              {validating ? <Loader size={12} strokeWidth={1.5} style={{ animation: 'rh-spin 1s linear infinite' }} /> : t('admin.hookValidate')}
            </button>
            <button onClick={handleDone} disabled={isMcp} className="px-4 py-2 text-xs font-semibold"
              style={{ background: isMcp ? 'var(--bg-elevated)' : 'var(--blue)', border: 'none', borderRadius: 4, color: isMcp ? 'var(--text-dim)' : 'var(--text-inverse)', cursor: isMcp ? 'not-allowed' : 'pointer' }}>
              {t('admin.hookDone')}
            </button>
          </div>
        </div>
      </div>

      {deleting && (
        <DeleteConfirm id={draft.id} onCancel={() => setDeleting(false)}
          onConfirm={() => { setDeleting(false); onDelete(draft.id) }} />
      )}
    </>
  )
}

function DeleteConfirm({ id, onConfirm, onCancel }) {
  const { t } = useTranslation()
  const [typed, setTyped] = useState('')
  return (
    <ConfirmDialog title={t('admin.hookDeleteConfirmTitle')} body={t('admin.hookDeleteConfirmBody')}
      confirmLabel={t('admin.hookDelete')} danger
      onConfirm={() => { if (typed === id) onConfirm() }} onCancel={onCancel}>
      <input autoFocus style={monoInputStyle} value={typed} placeholder={t('admin.hookDeleteTypeId', { id })}
        onChange={(e) => setTyped(e.target.value)} />
    </ConfirmDialog>
  )
}

function Field({ label, error, style, children }) {
  return (
    <div style={{ marginBottom: 14, ...style }}>
      <label style={labelStyle}>{label}</label>
      {children}
      {error && <div className="text-xs" style={{ color: 'var(--red)', marginTop: 4 }}>{error.message}</div>}
    </div>
  )
}

// --- Main section ------------------------------------------------------------

export default function RuntimeHooks() {
  const { t } = useTranslation()
  const [policies, setPolicies] = useState([])
  const [events, setEvents] = useState([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [collapsed, setCollapsed] = useState(() => new Set())
  const [drafts, setDrafts] = useState({})          // { id: partialPatch }
  const [savingGroup, setSavingGroup] = useState(null)
  const [drawer, setDrawer] = useState(null)        // { mode, policy?, presetEvent? }
  const [enforceConfirm, setEnforceConfirm] = useState(null) // policy id pending enforce-on
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await listHookPolicies()
      if (!mountedRef.current) return
      setPolicies(res.items || [])
      setEvents(res.supported_events || [])
      // Collapse empty event groups by default.
      const byEvent = groupByEvent(res.items || [], res.supported_events || [])
      setCollapsed(new Set((res.supported_events || []).filter((ev) => byEvent[ev].length === 0)))
    } catch (e) {
      if (mountedRef.current) setError(e?.message || t('admin.hookLoadFailed'))
    } finally {
      if (mountedRef.current) setLoading(false)
    }
  }, [t])

  useEffect(() => { load() }, [load])

  // Effective row = server policy merged with its staged draft.
  const effective = useCallback((p) => ({ ...p, ...(drafts[p.id] || {}) }), [drafts])

  const byEvent = useMemo(() => groupByEvent(policies, events), [policies, events])

  const stageDraft = (id, patch) => setDrafts((d) => ({ ...d, [id]: { ...(d[id] || {}), ...patch } }))

  // rev-6: enforcement is PER EVENT (enforced_events ⊆ events) — the shield in
  // one event group toggles only that event, so a hook spanning two events can
  // fire on just one. The server derives enabled/enforced from the list.
  const enforcedOn = useCallback((p, ev) => (effective(p).enforced_events || []).includes(ev), [effective])

  const toggleEnforce = (p, ev) => {
    const ee = effective(p).enforced_events || []
    if (ee.includes(ev)) {
      stageDraft(p.id, { enforced_events: ee.filter((e) => e !== ev) })
    } else {
      setEnforceConfirm({ id: p.id, ev })     // enforce-ON confirms at flip time
    }
  }

  const confirmEnforce = () => {
    const { id, ev } = enforceConfirm
    const p = policies.find((x) => x.id === id)
    if (p) {
      const ee = effective(p).enforced_events || []
      stageDraft(id, { enforced_events: ee.includes(ev) ? ee : [...ee, ev] })
    }
    setEnforceConfirm(null)
  }

  // A draft dirties a group only when it changes THIS event's enforcement (or
  // touches shared definition fields) — a Pre-only toggle no longer lights up
  // the PostToolUse row of the same hook.
  const dirtyForEvent = useCallback((p, ev) => {
    const d = drafts[p.id]
    if (!d) return false
    if (Object.keys(d).some((k) => k !== 'enforced_events' && k !== '__create')) return true
    if (d.__create) return true
    if (!('enforced_events' in d)) return false
    const base = p.enforced_events || []
    return base.includes(ev) !== d.enforced_events.includes(ev)
  }, [drafts])

  const dirtyInGroup = (ev) => byEvent[ev].filter((p) => dirtyForEvent(p, ev)).length

  const saveGroup = async (ev) => {
    const dirty = byEvent[ev].filter((p) => dirtyForEvent(p, ev))
    if (!dirty.length) return
    setSavingGroup(ev)
    setError(null)
    try {
      for (const p of dirty) {
        const patch = drafts[p.id]
        if (patch.__create) {
          const { __create, ...body } = patch
          await createHookPolicy(body)
        } else {
          const { __create, ...body } = patch
          await updateHookPolicy(p.id, body)
        }
      }
      // Clear the committed drafts, then reload canonical state.
      setDrafts((d) => {
        const next = { ...d }
        dirty.forEach((p) => delete next[p.id])
        return next
      })
      await load()
    } catch (e) {
      if (mountedRef.current) setError(e?.message || t('admin.hookSaveFailed'))
    } finally {
      if (mountedRef.current) setSavingGroup((g) => (g === ev ? null : g))
    }
  }

  const openCreate = (ev) => setDrawer({ mode: 'create', presetEvent: ev })
  const openEdit = (p) => setDrawer({ mode: 'edit', policy: effective(p) })

  const commitDrawer = (draft, mode) => {
    const body = {
      hook_type: draft.hook_type, name: draft.name, description: draft.description,
      events: draft.events, matcher: draft.matcher,
      timeout_seconds: draft.timeout_seconds ? Number(draft.timeout_seconds) : undefined,
      interpreter: draft.interpreter, script_body: draft.script_body,
      url: draft.url, headers_json: draft.headers_json, allowed_env_vars: draft.allowed_env_vars,
      default_on: draft.default_on,
    }
    if (mode === 'create') {
      // Optimistic local row so it renders in its group(s) until Save commits it.
      // New rows are unarmed (enforced_events = []) — enforce per event after review.
      const id = draft.id
      const optimistic = {
        id, ...body, enabled: false, enforced: false, enforced_events: [],
        predefined: false, seed_version: 0, seed_state: null,
      }
      setPolicies((ps) => ps.some((p) => p.id === id) ? ps : [...ps, optimistic])
      stageDraft(id, { __create: true, id, ...body })
    } else {
      // Definition-only patch: per-event enforcement rides the row shields, and
      // the server clamps enforced_events to the (possibly edited) event set.
      stageDraft(draft.id, { ...body })
    }
    setDrawer(null)
  }

  const handleDelete = async (id) => {
    setError(null)
    try {
      await deleteHookPolicy(id)
      setDrawer(null)
      setDrafts((d) => { const n = { ...d }; delete n[id]; return n })
      await load()
    } catch (e) {
      if (mountedRef.current) setError(e?.message || t('admin.hookDeleteFailed'))
    }
  }

  const toggleCollapse = (ev) => setCollapsed((c) => {
    const n = new Set(c)
    n.has(ev) ? n.delete(ev) : n.add(ev)
    return n
  })

  return (
    <div>
      <style>{`
        @keyframes rh-spin { from { transform: rotate(0); } to { transform: rotate(360deg); } }
        .rh-event-trigger:hover { background: var(--bg-elevated) !important; }
        .rh-action-button:not(:disabled):hover { background: var(--bg-elevated) !important; }
      `}</style>
      <div className="flex items-center justify-between" style={{ marginBottom: 6 }}>
        <div className="flex items-center gap-2">
          <h4 className="font-semibold" style={{ color: 'var(--text-secondary)', fontSize: 13, margin: 0 }}>
            {t('admin.hookRuntimeTitle')}
          </h4>
        </div>
        <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('admin.hookEffectNote')}</span>
      </div>
      <p className="text-xs" style={{ color: 'var(--text-dim)', marginBottom: 16, maxWidth: 640 }}>
        {t('admin.hookRuntimeDesc')}
      </p>
      {error && <p className="text-xs" style={{ color: 'var(--red)', marginBottom: 12 }}>{error}</p>}

      {loading ? (
        <div className="flex flex-col gap-3">
          {[0, 1, 2].map((i) => <div key={i} className="ars-skeleton" style={{ height: 40, borderRadius: 4 }} />)}
        </div>
      ) : (
        <div className="flex flex-col gap-1">
          {events.map((ev) => {
            const rows = byEvent[ev]
            const isCollapsed = collapsed.has(ev)
            const activated = rows.filter((p) => enforcedOn(p, ev)).length
            const dirtyN = dirtyInGroup(ev)
            const Chevron = isCollapsed ? ChevronRight : ChevronDown
            return (
              <div key={ev}>
                <button type="button" onClick={() => toggleCollapse(ev)}
                  className="rh-event-trigger flex items-center justify-between w-full py-2"
                  style={{
                    paddingLeft: 10, paddingRight: 12,
                    background: isCollapsed ? 'transparent' : 'var(--bg-elevated)',
                    border: 'none', borderLeft: `2px solid ${isCollapsed ? 'transparent' : 'var(--blue)'}`,
                    borderRadius: 0, cursor: 'pointer', transition: 'background 150ms ease',
                  }}>
                  <span className="flex items-center gap-2" style={{ color: 'var(--text-primary)', fontSize: 13, fontWeight: 600 }}>
                    <Chevron size={14} strokeWidth={1.5} />
                    {ev}
                  </span>
                  <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                    {t('admin.hookActivated', { n: activated, total: rows.length })}
                  </span>
                </button>

                {!isCollapsed && (
                  <div style={{ borderTop: '1px solid var(--border-subtle)' }}>
                    {rows.length === 0 ? (
                      <div className="px-3 py-3 text-xs" style={{ color: 'var(--text-dim)' }}>{t('admin.hookNoHooks')}</div>
                    ) : rows.map((p) => {
                      const eff = effective(p)
                      const dirty = dirtyForEvent(p, ev)
                      return (
                        <div key={p.id} className="flex items-center gap-3"
                          style={{
                            paddingTop: 8, paddingLeft: 28, paddingRight: 12,
                            background: dirty ? 'var(--bg-elevated)' : 'transparent',
                          }}>
                          <div className="flex flex-col min-w-0" style={{ flex: 1, paddingBottom: 8, borderBottom: '1px solid var(--border-subtle)' }}>
                            <div className="flex items-center gap-2 min-w-0">
                              <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 12 }}>{eff.name || p.id}</span>
                              {p.predefined && <span style={{ ...badgeStyle, color: 'var(--cyan)', border: '1px solid var(--cyan)', flexShrink: 0 }}>{t('admin.hookPredefined')}</span>}
                              {p.seed_state === 'outdated' && <AlertTriangle size={12} strokeWidth={1.5} style={{ color: 'var(--orange)', flexShrink: 0 }} />}
                            </div>
                            <span className="truncate" style={{ color: 'var(--text-dim)', fontSize: 11, fontFamily: "'JetBrains Mono', monospace" }}>
                              {eff.hook_type.toUpperCase()}·{eff.matcher || 'all'}
                              {(eff.events || []).length > 1 ? ` · ${t('admin.hookMultiEvent', { n: eff.events.length })}` : ''}
                            </span>
                          </div>
                          <EnforceButton on={enforcedOn(p, ev)} label={t('admin.hookEnforce')} onClick={() => toggleEnforce(p, ev)} />
                          <button type="button" onClick={() => openEdit(p)} title={t('admin.hookEdit')} aria-label={t('admin.hookEdit')}
                            className="rh-action-button flex items-center justify-center flex-shrink-0"
                            style={{ width: 30, height: 30, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer', transition: 'color 150ms ease, background 150ms ease' }}>
                            <Pencil size={14} strokeWidth={1.5} />
                          </button>
                        </div>
                      )
                    })}

                    <div className="flex items-center justify-between px-3 py-2">
                      <button type="button" onClick={() => openCreate(ev)} className="flex items-center gap-1 text-xs"
                        style={{ background: 'transparent', border: 'none', color: 'var(--blue)', cursor: 'pointer' }}>
                        <Plus size={14} strokeWidth={1.5} /> {t('admin.hookAddHook')}
                      </button>
                      <button type="button" onClick={() => saveGroup(ev)} disabled={dirtyN === 0 || savingGroup === ev}
                        className="flex items-center gap-2 px-3 py-1 text-xs font-semibold"
                        style={{
                          background: dirtyN > 0 && savingGroup !== ev ? 'var(--blue)' : 'var(--bg-elevated)',
                          color: dirtyN > 0 && savingGroup !== ev ? 'var(--text-inverse)' : 'var(--text-dim)',
                          border: 'none', borderRadius: 4, cursor: dirtyN > 0 ? 'pointer' : 'default',
                          opacity: dirtyN > 0 ? 1 : 0.5, transition: 'opacity 150ms ease',
                        }}>
                        {savingGroup === ev && <Loader size={12} strokeWidth={1.5} style={{ animation: 'rh-spin 1s linear infinite' }} />}
                        {t('admin.hookSaveCount', { n: dirtyN })}
                      </button>
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      <HookDrawer
        open={!!drawer}
        mode={drawer?.mode || 'edit'}
        policy={drawer?.policy}
        presetEvent={drawer?.presetEvent}
        supportedEvents={events}
        onClose={() => setDrawer(null)}
        onCommit={commitDrawer}
        onDelete={handleDelete}
      />

      {enforceConfirm && (
        <ConfirmDialog
          title={t('admin.hookEnforceConfirmTitle')}
          body={t('admin.hookEnforceConfirmBody')}
          confirmLabel={t('admin.hookConfirm')}
          onConfirm={confirmEnforce}
          onCancel={() => setEnforceConfirm(null)}
        />
      )}
    </div>
  )
}

// Group policies by event; a multi-event hook appears under each of its events.
function groupByEvent(policies, events) {
  const map = {}
  events.forEach((ev) => { map[ev] = [] })
  policies.forEach((p) => {
    (p.events || []).forEach((ev) => {
      if (!map[ev]) map[ev] = []
      map[ev].push(p)
    })
  })
  return map
}
