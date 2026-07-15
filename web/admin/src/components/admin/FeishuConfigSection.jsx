import { useState, useEffect, useRef, useCallback } from 'react'
import { MessageSquare, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import * as adminApi from '@shared/api/admin'
import CopyButton from '@shared/components/shared/CopyButton'
import Toggle from '@shared/components/shared/Toggle'

const MONO = "'JetBrains Mono', 'Source Han Mono SC', monospace"

// conn_status → { color (2px left border), labelKey } — the design-system status colors.
function statusView(cfg, t) {
  if (!cfg) return { color: 'var(--border)', label: '—', desc: '' }
  if (cfg.admin_disabled) {
    return { color: 'var(--border)', label: t('admin.feishu.stDisabledAdmin'), desc: t('admin.feishu.stDisabledAdminDesc') }
  }
  if (!cfg.app_id || !cfg.app_secret_set) {
    return { color: 'var(--border)', label: t('admin.feishu.stNotConfigured'), desc: t('admin.feishu.stNotConfiguredDesc') }
  }
  const MAP = {
    connected: { color: 'var(--green)', label: t('admin.feishu.stConnected') },
    connecting: { color: 'var(--yellow)', label: t('admin.feishu.stConnecting') },
    auth_failed: { color: 'var(--red)', label: t('admin.feishu.stAuthFailed') },
    error: { color: 'var(--red)', label: t('admin.feishu.stError') },
    conflict: { color: 'var(--yellow)', label: t('admin.feishu.stConflict') },
    // effective but the connector has not (yet) reported a live connection
    disabled: { color: 'var(--yellow)', label: t('admin.feishu.stPendingConnector') },
  }
  const conn = cfg.connection || {}
  const v = MAP[conn.conn_status] || MAP.disabled
  const parts = [cfg.app_id]
  if (conn.last_error_code) parts.push(String(conn.last_error_code))
  if (conn.last_connected_at) parts.push(new Date(conn.last_connected_at).toLocaleTimeString())
  return { color: v.color, label: v.label, desc: parts.filter(Boolean).join(' · ') }
}

export default function FeishuConfigSection({ username }) {
  const { t } = useTranslation()
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [pendingDisable, setPendingDisable] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const mountedRef = useRef(true)

  const refresh = useCallback(async ({ spin = false } = {}) => {
    if (spin) setRefreshing(true)
    try {
      const data = await adminApi.getUserFeishuConfig(username)
      if (mountedRef.current) { setCfg(data); setError(null) }
    } catch (e) {
      if (mountedRef.current) setError(e.message)  // fail-soft: keep last good cfg
    } finally {
      if (mountedRef.current) { setLoading(false); setRefreshing(false) }
    }
  }, [username])

  useEffect(() => {
    mountedRef.current = true
    setLoading(true)
    refresh()
    const id = setInterval(() => refresh(), 8000)  // ~8s poll; the connection line is the live part
    return () => { mountedRef.current = false; clearInterval(id) }
  }, [refresh])

  const applyDisabled = async (value) => {
    setBusy(true)
    try {
      const data = await adminApi.updateUserFeishuConfig(username, { admin_disabled: value })
      if (mountedRef.current) setCfg(data)
    } catch (e) {
      if (mountedRef.current) setError(e.message)
    } finally {
      if (mountedRef.current) { setBusy(false); setPendingDisable(false) }
    }
  }

  const sv = statusView(cfg, t)
  const adminDisabled = !!cfg?.admin_disabled

  return (
    <div className="flex flex-col gap-2">
      <label className="text-xs uppercase flex items-center gap-2" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
        <MessageSquare size={12} strokeWidth={1.5} />
        {t('admin.feishu.title')}
      </label>

      {loading ? (
        <div className="skeleton" style={{ height: 46, width: '100%', borderRadius: 4 }} />
      ) : (
        <>
          {/* Connection status — 2px left border carries the status color */}
          <div
            className="flex items-center gap-2 px-3 py-2 overflow-hidden"
            style={{ background: 'var(--bg-elevated)', borderRadius: 4, borderLeft: `2px solid ${sv.color}` }}
          >
            <div className="flex-1 min-w-0">
              <div className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{sv.label}</div>
              {sv.desc && (
                <div className="text-xs truncate" style={{ color: 'var(--text-dim)', fontFamily: MONO }}>{sv.desc}</div>
              )}
            </div>
            <button
              type="button"
              onClick={() => refresh({ spin: true })}
              title={t('admin.feishu.refresh')}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2, transition: 'color 150ms ease' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
            >
              <RefreshCw size={14} strokeWidth={1.5} style={refreshing ? { animation: 'spin 1s linear infinite' } : undefined} />
            </button>
          </div>

          {/* app_id — read-only + copy (admin never edits credentials) */}
          {cfg?.app_id && (
            <div className="flex items-center gap-3">
              <span className="text-xs uppercase flex-shrink-0" style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em', width: 72 }}>
                {t('admin.feishu.appId')}
              </span>
              <div
                className="relative flex items-center px-2 py-1 text-xs flex-1 min-w-0"
                style={{ background: 'var(--bg-elevated)', borderRadius: 4, fontFamily: MONO, color: 'var(--text-secondary)', paddingRight: 28 }}
              >
                <span className="truncate">{cfg.app_id}</span>
                <CopyButton content={cfg.app_id} />
              </div>
            </div>
          )}

          {/* app_secret — presence only (write-only; admin never sees or edits it) */}
          <div className="flex items-center gap-3">
            <span className="text-xs uppercase flex-shrink-0" style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em', width: 72 }}>
              {t('admin.feishu.appSecret')}
            </span>
            <span
              className="text-xs px-2 py-1"
              style={{
                color: cfg?.app_secret_set ? 'var(--green)' : 'var(--text-dim)',
                borderLeft: `2px solid ${cfg?.app_secret_set ? 'var(--green)' : 'var(--border)'}`,
                background: 'var(--bg-elevated)', borderRadius: 4,
              }}
            >
              {cfg?.app_secret_set ? t('admin.feishu.secretConfigured') : t('admin.feishu.secretNotSet')}
            </span>
          </div>

          {/* Kill-switch — the ONLY admin-writable control. Disabling asks to confirm. */}
          <div className="flex items-center justify-between gap-3 pt-1">
            <span className="text-xs uppercase" style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
              {t('admin.feishu.adminDisable')}
            </span>
            <Toggle
              size="sm"
              checked={adminDisabled}
              disabled={busy || pendingDisable}
              onLabel={t('admin.feishu.on')}
              offLabel={t('admin.feishu.off')}
              onChange={(next) => {
                if (next) setPendingDisable(true)   // disabling → confirm
                else applyDisabled(false)           // re-enabling → immediate
              }}
            />
          </div>

          {pendingDisable && (
            <div
              className="flex flex-col gap-2 px-3 py-2"
              style={{ background: 'var(--bg-elevated)', borderRadius: 4, borderLeft: '2px solid var(--red)' }}
            >
              <span className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                {t('admin.feishu.disableConfirm', { username })}
              </span>
              <div className="flex gap-2">
                <button
                  type="button"
                  className="px-3 py-1 text-xs"
                  style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}
                  onClick={() => setPendingDisable(false)}
                >
                  {t('confirm.cancel')}
                </button>
                <button
                  type="button"
                  className="px-3 py-1 text-xs"
                  style={{ background: 'var(--red)', border: 'none', borderRadius: 4, color: 'var(--text-inverse)', cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}
                  disabled={busy}
                  onClick={() => applyDisabled(true)}
                >
                  {t('admin.feishu.disable')}
                </button>
              </div>
            </div>
          )}

          {error && <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>}
        </>
      )}
    </div>
  )
}
