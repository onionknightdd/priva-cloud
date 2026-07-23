import { useState, useEffect, useCallback } from 'react'
import { MessageSquare, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import Toggle from '@shared/components/shared/Toggle'
import { getChannelPlatformConfig, updateChannelPlatformConfig } from '@shared/api/admin'

// Configurations ▸ Channels — platform-wide channel settings (feat_feishu_DM.md §5.1).
// Today: the global group-chat kill switch. It composes with each user's own opt-in
// (effective = user opt-in AND NOT this switch); flipping it re-arms every affected
// connector worker via the desired_digest recompute on the data-spine side.

export default function ChannelSettings() {
  const { t } = useTranslation()
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState(null)
  const [pendingDisable, setPendingDisable] = useState(false)

  const load = useCallback(async ({ spin = false } = {}) => {
    if (spin) setRefreshing(true)
    try {
      setCfg(await getChannelPlatformConfig())
      setError(null)
    } catch (e) {
      setError(e?.message || 'load failed')
    } finally {
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  const apply = async (disabled) => {
    setBusy(true)
    try {
      setCfg(await updateChannelPlatformConfig({ group_chat_disabled: disabled }))
      setError(null)
      setPendingDisable(false)
    } catch (e) {
      setError(e?.message || 'save failed')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex-1 flex flex-col" style={{ minWidth: 0, minHeight: 0, overflowY: 'auto' }}>
      {/* Header */}
      <div className="flex-shrink-0" style={{ padding: '20px var(--admin-section-x) 12px var(--admin-section-x)' }}>
        <div className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1" style={{ minWidth: 0 }}>
            <h2 className="text-lg font-bold" style={{ color: 'var(--text-primary)' }}>
              {t('admin.channels.title')}
            </h2>
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
              {t('admin.channels.desc')}
            </span>
          </div>
          <button
            type="button"
            onClick={() => load({ spin: true })}
            title={t('admin.channels.refresh')}
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4 }}
          >
            <RefreshCw size={14} strokeWidth={1.5} style={refreshing ? { animation: 'fleet-spin 1s linear infinite' } : undefined} />
          </button>
        </div>
      </div>

      <div className="flex flex-col gap-4" style={{ padding: '4px var(--admin-section-x) 24px var(--admin-section-x)', maxWidth: 720 }}>
        {loading ? (
          <div className="flex flex-col gap-3">
            <div className="skeleton" style={{ height: 88, width: '100%', borderRadius: 4 }} />
          </div>
        ) : (
          <div
            className="flex flex-col gap-3 px-4 py-3"
            style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4 }}
          >
            {/* Section: Feishu */}
            <div className="flex items-center gap-2">
              <MessageSquare size={16} strokeWidth={1.5} style={{ color: 'var(--text-secondary)' }} />
              <span className="text-xs font-semibold uppercase" style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
                {t('admin.channels.feishu')}
              </span>
            </div>

            {/* Global group-chat kill switch */}
            <div className="flex items-center justify-between gap-3">
              <div className="flex flex-col gap-1" style={{ minWidth: 0 }}>
                <span className="text-sm" style={{ color: 'var(--text-primary)' }}>
                  {t('admin.channels.groupChatDisable')}
                </span>
                <span className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
                  {t('admin.channels.groupChatDisableHint')}
                </span>
              </div>
              <Toggle
                size="sm"
                checked={!!cfg?.group_chat_disabled}
                disabled={busy || pendingDisable}
                onLabel={t('admin.channels.on')}
                offLabel={t('admin.channels.off')}
                onChange={(next) => {
                  if (next) setPendingDisable(true)  // platform-wide disable → confirm first
                  else apply(false)                  // re-enabling → immediate
                }}
              />
            </div>

            {pendingDisable && (
              <div
                className="flex flex-col gap-2 px-3 py-2"
                style={{ background: 'var(--bg-elevated)', borderRadius: 4, borderLeft: '2px solid var(--red)' }}
              >
                <span className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
                  {t('admin.channels.groupChatDisableConfirm')}
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
                    onClick={() => apply(true)}
                  >
                    {t('admin.channels.groupChatDisableAction')}
                  </button>
                </div>
              </div>
            )}

            {cfg?.updated_at && (
              <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                {t('admin.channels.updatedMeta', {
                  by: cfg.updated_by || '—',
                  at: new Date(cfg.updated_at).toLocaleString(),
                })}
              </span>
            )}

            {error && <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>}
          </div>
        )}
      </div>
    </div>
  )
}
