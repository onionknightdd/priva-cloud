import { useState, useEffect, useCallback } from 'react'
import { Check, Loader, Plus, X, Lock } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import Dropdown from '@shared/components/shared/Dropdown'
import { getNetworkIsolation, updateNetworkIsolation } from '@shared/api/admin'

// Sandbox ▸ Isolation.
//
// What this replaced was a greyed-out Allow/Deny control whose value was a
// client-side literal — never fetched, never saved — under a hint that stated as
// present-tense fact that Terminal could not reach data-spine, PostgreSQL, Redis
// or Runner pods. The policies making that true were not installed on any
// cluster. There was no channel between the sentence and reality, so it could
// never be wrong in a detectable way and never become right.
//
// Hence: every row here shows DESIRED (what is stored) next to APPLIED (whether
// the operator actually wrote the object). They fail separately, and the gap is
// the only visible symptom of an operator that is down or has lost its RBAC.

const STATE_COLOR = {
  ok: 'var(--green)',
  drift: 'var(--red)',
  unknown: 'var(--yellow)',
  off: 'var(--border)',
}

function boundaryState(b) {
  if (!b.desired) return 'off'
  if (b.applied === true) return 'ok'
  if (b.applied === false) return 'drift'
  return 'unknown'
}

const BOUNDARY_ROWS = [
  { key: 'runner_deny_internal', labelKey: 'admin.isoRunnerInternal', detailKey: 'admin.isoRunnerInternalDetail' },
  { key: 'terminal_deny_internal', labelKey: 'admin.isoTerminalInternal', detailKey: 'admin.isoTerminalInternalDetail' },
  { key: 'deny_tenant_peers', labelKey: 'admin.isoTenantPeers', detailKey: 'admin.isoTenantPeersDetail' },
]

const panelStyle = {
  border: '1px solid var(--border)',
  borderRadius: 4,
  background: 'var(--bg-surface)',
  overflow: 'hidden',
}

const panelHeadStyle = {
  padding: '8px 12px',
  borderBottom: '1px solid var(--border)',
  color: 'var(--text-secondary)',
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

const inputStyle = {
  padding: '6px 10px',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  color: 'var(--text-primary)',
  fontSize: 12,
  fontFamily: "'JetBrains Mono', monospace",
  outline: 'none',
  boxSizing: 'border-box',
}

function StatusBox({ tone, title, detail }) {
  return (
    <div style={{ ...panelStyle, padding: '10px 12px', borderLeft: `2px solid ${tone}` }}>
      <div className="text-xs" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{title}</div>
      {detail && (
        <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 3, fontWeight: 300 }}>
          {detail}
        </div>
      )}
    </div>
  )
}

function Field({ title, hint, children }) {
  return (
    <div className="flex items-start gap-4">
      <div style={{ width: 240, flexShrink: 0 }}>
        <div className="uppercase text-xs" style={{ color: 'var(--text-secondary)', fontWeight: 600, letterSpacing: '0.06em' }}>
          {title}
        </div>
        {hint && (
          <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4, fontWeight: 300 }}>{hint}</div>
        )}
      </div>
      <div style={{ width: 260, flexShrink: 0 }}>{children}</div>
    </div>
  )
}

function SaveRow({ dirty, saving, saved, onSave, t }) {
  return (
    <div className="flex items-center justify-end gap-3" style={{ marginTop: 12 }}>
      {saved && (
        <span className="flex items-center gap-1 text-xs" style={{ color: 'var(--green)' }}>
          <Check size={14} strokeWidth={1.5} /> {t('admin.sandboxSaved')}
        </span>
      )}
      <button
        type="button"
        className="flex items-center gap-2 px-4 py-2 text-xs font-semibold"
        disabled={!dirty || saving}
        onClick={onSave}
        style={{
          background: dirty && !saving ? 'var(--blue)' : 'var(--bg-elevated)',
          color: dirty && !saving ? 'var(--text-inverse)' : 'var(--text-dim)',
          border: 'none', borderRadius: 4,
          cursor: dirty && !saving ? 'pointer' : 'default',
          opacity: dirty && !saving ? 1 : 0.5,
          transition: 'opacity 150ms ease',
        }}
      >
        {saving && <Loader size={14} strokeWidth={1.5} style={{ animation: 'ars-spin 1s linear infinite' }} />}
        {t('admin.save')}
      </button>
    </div>
  )
}

export default function NetworkIsolation() {
  const { t } = useTranslation()
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [data, setData] = useState(null)      // server truth (settings + status)
  const [draft, setDraft] = useState(null)    // edited settings
  const [saving, setSaving] = useState(null)
  const [savedAt, setSavedAt] = useState(null)
  const [newHost, setNewHost] = useState('')
  const [newPort, setNewPort] = useState('443')

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const d = await getNetworkIsolation()
      setData(d)
      setDraft({
        runner_deny_internal: d.runner_deny_internal,
        terminal_deny_internal: d.terminal_deny_internal,
        deny_tenant_peers: d.deny_tenant_peers,
        egress_mode: d.egress_mode,
        egress_allowlist: d.egress_allowlist || [],
      })
    } catch (e) {
      setError(e?.message || t('admin.sandboxLoadFailed'))
    } finally {
      setLoading(false)
    }
  }, [t])

  useEffect(() => { load() }, [load])

  const save = useCallback(async (group, payload) => {
    setSaving(group)
    setError(null)
    try {
      const d = await updateNetworkIsolation(payload)
      setData(d)
      setDraft((p) => ({ ...p, ...payload }))
      setSavedAt(group)
      setTimeout(() => setSavedAt((g) => (g === group ? null : g)), 1500)
    } catch (e) {
      setError(e?.message || t('admin.sandboxSaveFailed'))
    } finally {
      setSaving((g) => (g === group ? null : g))
    }
  }, [t])

  if (loading) {
    return (
      <div className="flex flex-col gap-3">
        {[0, 1, 2].map((i) => (
          <div key={i} className="ars-skeleton" style={{ height: i === 2 ? 120 : 44, width: '100%' }} />
        ))}
      </div>
    )
  }
  if (!data || !draft) {
    return <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>
  }

  const st = data.status || {}
  const enf = st.enforcement || {}
  const proxy = st.proxy || {}
  const boundaries = Object.fromEntries((st.boundaries || []).map((b) => [b.key, b]))

  const enforcementTone = enf.state === 'true' ? STATE_COLOR.ok
    : enf.state === 'false' ? STATE_COLOR.drift : STATE_COLOR.unknown
  const enforcementTitle = enf.state === 'true' ? t('admin.isoEnforced')
    : enf.state === 'false' ? t('admin.isoNotEnforced') : t('admin.isoEnforcementUnknown')
  const enforcementDetail = enf.state === 'unknown' || !enf.state
    ? t('admin.isoEnforcementUnmeasured')
    : [enf.cni, enf.checked_at].filter(Boolean).join(' · ')

  // Only meaningful in allowlist mode: in every other mode nothing is pointed at
  // the proxy, so its absence is correct rather than an outage.
  const proxyTone = !proxy.required ? STATE_COLOR.off
    : proxy.present === true && proxy.ready > 0 ? STATE_COLOR.ok
      : proxy.present === null ? STATE_COLOR.unknown : STATE_COLOR.drift
  const proxyTitle = !proxy.required ? t('admin.isoProxyNotUsed')
    : proxy.present === true && proxy.ready > 0 ? `${t('admin.isoProxyHealthy')}  ${proxy.ready}/${proxy.desired}`
      : proxy.present === null ? t('admin.isoEnforcementUnknown') : t('admin.isoProxyDown')

  const boundaryDirty = BOUNDARY_ROWS.some((r) => draft[r.key] !== data[r.key])
  const allowlistDirty = draft.egress_mode !== data.egress_mode
    || JSON.stringify(draft.egress_allowlist) !== JSON.stringify(data.egress_allowlist || [])

  const addEntry = () => {
    const host = newHost.trim().toLowerCase()
    if (!host) return
    const port = parseInt(newPort, 10) || 0
    if (draft.egress_allowlist.some((e) => e.host === host && e.port === port)) return
    setDraft((p) => ({ ...p, egress_allowlist: [...p.egress_allowlist, { host, port }] }))
    setNewHost('')
    setNewPort('443')
  }

  return (
    <div className="flex flex-col gap-5">
      {error && <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>}

      <Field title={t('admin.isoEnforcementTitle')} hint={t('admin.isoEnforcementHint')}>
        <StatusBox tone={enforcementTone} title={enforcementTitle} detail={enforcementDetail} />
      </Field>

      <Field title={t('admin.isoProxyTitle')} hint={t('admin.isoProxyHint')}>
        <StatusBox tone={proxyTone} title={proxyTitle} />
      </Field>

      {/* Tenant network boundary */}
      <div style={panelStyle}>
        <div style={panelHeadStyle}>{t('admin.isoBoundaryTitle')}</div>
        {BOUNDARY_ROWS.map((row) => {
          const b = boundaries[row.key] || { desired: draft[row.key], applied: null }
          const state = boundaryState({ ...b, desired: draft[row.key] })
          return (
            <div
              key={row.key}
              className="flex items-center gap-3"
              style={{
                padding: '10px 12px',
                borderTop: '1px solid var(--border-subtle)',
                borderLeft: `2px solid ${STATE_COLOR[state]}`,
              }}
            >
              <div className="flex-1" style={{ minWidth: 0 }}>
                <div className="text-xs truncate" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
                  {t(row.labelKey)}
                </div>
                <div className="text-xs truncate" style={{ color: 'var(--text-dim)', marginTop: 2, fontWeight: 300 }}>
                  {state === 'drift' ? t('admin.isoDrift') : t(row.detailKey)}
                </div>
              </div>
              <div className="flex-shrink-0" style={{ width: 110 }}>
                <Dropdown
                  size="sm"
                  align="right"
                  value={draft[row.key] ? 'deny' : 'allow'}
                  onChange={(v) => setDraft((p) => ({ ...p, [row.key]: v === 'deny' }))}
                  options={[
                    { value: 'allow', label: t('admin.allow') },
                    { value: 'deny', label: t('admin.deny') },
                  ]}
                />
              </div>
            </div>
          )
        })}
        {/* Hand-applied control-plane boundary: shown so it is visibly in force,
            locked because it is not a tenant setting. */}
        <div
          className="flex items-center gap-3"
          style={{
            padding: '10px 12px',
            borderTop: '1px solid var(--border-subtle)',
            borderLeft: `2px solid ${STATE_COLOR[boundaryState(boundaries.postgres || { desired: true })]}`,
          }}
        >
          <div className="flex-1" style={{ minWidth: 0 }}>
            <div className="text-xs truncate" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>
              {t('admin.isoPostgres')}
            </div>
            <div className="text-xs truncate" style={{ color: 'var(--text-dim)', marginTop: 2, fontWeight: 300 }}>
              {t('admin.isoPostgresDetail')}
            </div>
          </div>
          <div className="flex items-center gap-1 flex-shrink-0 text-xs"
               style={{ width: 110, color: 'var(--text-dim)', justifyContent: 'flex-end' }}>
            <Lock size={12} strokeWidth={1.5} /> {t('admin.isoLocked')}
          </div>
        </div>
        {/* Measured, not theoretical: with the runner boundary denied, the node
            IP and ClusterIP-DNAT'd destinations stayed reachable. NetworkPolicy
            has no portable rule for traffic to the hosting node. Say so here
            rather than let the switch read as a complete boundary. */}
        <div
          className="text-xs"
          style={{
            padding: '8px 12px',
            borderTop: '1px solid var(--border-subtle)',
            color: 'var(--text-dim)',
            fontWeight: 300,
          }}
        >
          {t('admin.isoBoundaryNodeCaveat')}
        </div>
      </div>
      <SaveRow
        dirty={boundaryDirty}
        saving={saving === 'boundary'}
        saved={savedAt === 'boundary'}
        onSave={() => save('boundary', Object.fromEntries(BOUNDARY_ROWS.map((r) => [r.key, draft[r.key]])))}
        t={t}
      />

      {/* Egress */}
      <Field title={t('admin.isoEgressTitle')} hint={t('admin.isoEgressHint')}>
        <Dropdown
          size="sm"
          value={draft.egress_mode}
          onChange={(v) => setDraft((p) => ({ ...p, egress_mode: v }))}
          options={[
            { value: 'unrestricted', label: t('admin.isoModeUnrestricted') },
            { value: 'allowlist', label: t('admin.isoModeAllowlist') },
            { value: 'deny_all', label: t('admin.isoModeDenyAll') },
          ]}
        />
      </Field>

      {draft.egress_mode === 'allowlist' && (
        <div style={panelStyle}>
          <div className="flex items-center justify-between" style={panelHeadStyle}>
            <span>{t('admin.isoAllowlistTitle')}</span>
            <span style={{ fontWeight: 400 }}>
              {t('admin.isoAllowlistCount', { count: draft.egress_allowlist.length })}
            </span>
          </div>
          {draft.egress_allowlist.length === 0 && (
            <div className="text-xs" style={{ padding: '10px 12px', color: 'var(--orange)', borderLeft: '2px solid var(--orange)' }}>
              {t('admin.isoAllowlistEmpty')}
            </div>
          )}
          {draft.egress_allowlist.map((e, i) => (
            <div
              key={`${e.host}:${e.port}`}
              className="flex items-center gap-3"
              style={{ padding: '8px 12px', borderTop: '1px solid var(--border-subtle)' }}
            >
              <span className="flex-1 truncate text-xs"
                    style={{ color: 'var(--text-primary)', fontFamily: "'JetBrains Mono', monospace" }}>
                {e.host}
              </span>
              <span className="flex-shrink-0 text-xs"
                    style={{ color: 'var(--text-dim)', fontFamily: "'JetBrains Mono', monospace" }}>
                {e.port ? `:${e.port}` : t('admin.isoAnyPort')}
              </span>
              <button
                type="button"
                aria-label={t('admin.remove')}
                onClick={() => setDraft((p) => ({
                  ...p, egress_allowlist: p.egress_allowlist.filter((_, j) => j !== i),
                }))}
                className="flex items-center justify-center flex-shrink-0"
                style={{
                  width: 18, height: 18, borderRadius: '50%', border: 'none',
                  background: 'transparent', color: 'var(--text-dim)', cursor: 'pointer',
                  transition: 'color 150ms ease',
                }}
                onMouseEnter={(ev) => { ev.currentTarget.style.color = 'var(--red)' }}
                onMouseLeave={(ev) => { ev.currentTarget.style.color = 'var(--text-dim)' }}
              >
                <X size={12} strokeWidth={1.5} />
              </button>
            </div>
          ))}
          <div className="flex items-center gap-2" style={{ padding: '8px 12px', borderTop: '1px solid var(--border)' }}>
            <input
              value={newHost}
              onChange={(ev) => setNewHost(ev.target.value)}
              onKeyDown={(ev) => { if (ev.key === 'Enter') addEntry() }}
              placeholder={t('admin.isoHostPlaceholder')}
              style={{ ...inputStyle, flex: 1, minWidth: 0 }}
            />
            <input
              value={newPort}
              onChange={(ev) => setNewPort(ev.target.value)}
              onKeyDown={(ev) => { if (ev.key === 'Enter') addEntry() }}
              style={{ ...inputStyle, width: 66, flexShrink: 0 }}
            />
            <button
              type="button"
              onClick={addEntry}
              className="flex items-center justify-center flex-shrink-0"
              style={{
                width: 28, height: 28, borderRadius: 4, border: '1px solid var(--border)',
                background: 'var(--bg-elevated)', color: 'var(--text-secondary)', cursor: 'pointer',
                transition: 'color 150ms ease',
              }}
            >
              <Plus size={14} strokeWidth={1.5} />
            </button>
          </div>
        </div>
      )}

      <SaveRow
        dirty={allowlistDirty}
        saving={saving === 'egress'}
        saved={savedAt === 'egress'}
        onSave={() => save('egress', {
          egress_mode: draft.egress_mode,
          egress_allowlist: draft.egress_allowlist,
        })}
        t={t}
      />

      {st.legacy_present && st.legacy_present.length > 0 && (
        <div className="text-xs" style={{ padding: '10px 12px', ...panelStyle, borderLeft: '2px solid var(--orange)', color: 'var(--text-secondary)' }}>
          {t('admin.isoLegacyPresent', { names: st.legacy_present.join(', ') })}
        </div>
      )}
    </div>
  )
}
