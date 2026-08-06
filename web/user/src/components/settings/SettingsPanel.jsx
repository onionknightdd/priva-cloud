import { useState, useEffect, useCallback, useRef, useMemo } from 'react'
import { Key, Cpu, Zap, Check, AlertCircle, Eye, EyeOff, Plus, Trash2, Pencil, X, ChevronDown, Search, Copy, RefreshCw, Settings2, Archive, ArchiveRestore, MessageSquare, FolderBookmark } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useSettingsStore from '../../stores/settingsStore'
import useAuthStore from '@shared/stores/authStore'
import useUserDataStore from '../../stores/userDataStore'
import useSidebarStore from '../../stores/sidebarStore'
import { fetchArchivedSessions, archiveSession as apiArchiveSession } from '../../api/sessions'
import { changeMyPassword } from '@shared/api/auth'
import {
  getCliPath,
  getHistoryRetention,
  getRetryableTools,
  getSensitivePatterns,
  updateCliPath,
  updateHistoryRetention,
  updateRetryableTools,
  updateSensitivePatterns,
} from '@shared/api/admin'
import { copyTextToClipboard } from '@shared/utils/clipboard'
import { getLucideIcon, ICON_NAMES } from '../../utils/lucideIconMap'
import Toggle from '@shared/components/shared/Toggle'
import Dropdown from '@shared/components/shared/Dropdown'
import DrawIcon from '@shared/components/shared/DrawIcon'
import { createFeishuLinkCode, getFeishuConfig, getFeishuSessions, unbindFeishuOwner, updateFeishuConfig } from '../../api/channels'

function FilterableModelSelect({ models, value, onChange, label, labelStyle, inputStyle, placeholder, filterPlaceholder, noMatchesText }) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const containerRef = useRef(null)
  const filterRef = useRef(null)

  const filtered = useMemo(() => {
    if (!filter.trim()) return models
    const q = filter.toLowerCase()
    return models.filter((m) => m.id.toLowerCase().includes(q))
  }, [models, filter])

  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
        setFilter('')
      }
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  useEffect(() => {
    if (open && filterRef.current) filterRef.current.focus()
  }, [open])

  return (
    <div>
      <label style={labelStyle}>{label}</label>
      <div className="relative" ref={containerRef}>
        <div className="flex items-center" style={{ position: 'relative' }}>
          <input
            type="text"
            readOnly
            value={value || ''}
            placeholder={placeholder || 'Select model...'}
            style={{
              ...inputStyle,
              fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
              fontSize: 12,
              paddingRight: 28,
              cursor: 'text',
            }}
            onClick={() => setOpen(!open)}
          />
          <button
            type="button"
            style={{
              position: 'absolute',
              right: 0,
              top: 0,
              bottom: 0,
              width: 28,
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              color: 'var(--text-dim)',
            }}
            onClick={() => setOpen(!open)}
            tabIndex={-1}
          >
            <ChevronDown size={12} strokeWidth={1.5} />
          </button>
        </div>

        {open && (
          <div
            className="absolute left-0 right-0 flex flex-col"
            style={{
              top: '100%',
              marginTop: 2,
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              maxHeight: 200,
              zIndex: 60,
            }}
          >
            <div
              className="flex items-center gap-2 px-2 flex-shrink-0"
              style={{ borderBottom: '1px solid var(--border)' }}
            >
              <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
              <input
                ref={filterRef}
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={filterPlaceholder || 'Filter models...'}
                className="flex-1 py-2 text-xs"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: 'var(--text-primary)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 12,
                  minWidth: 0,
                }}
              />
            </div>
            <div className="overflow-y-auto" style={{ maxHeight: 160 }}>
              {filtered.length === 0 ? (
                <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>
                  {noMatchesText || 'No matches'}
                </div>
              ) : (
                filtered.map((m) => {
                  const isActive = value === m.id
                  return (
                    <button
                      key={m.id}
                      type="button"
                      className="flex items-center w-full px-3 py-2 text-xs"
                      style={{
                        background: isActive ? 'var(--bg-surface)' : 'transparent',
                        border: 'none',
                        borderLeft: isActive ? '2px solid var(--cyan)' : '2px solid transparent',
                        color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                        cursor: 'pointer',
                        fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                        fontSize: 12,
                        textAlign: 'left',
                        transition: 'background 150ms ease',
                        wordBreak: 'break-all',
                      }}
                      onClick={() => {
                        onChange(m.id)
                        setOpen(false)
                        setFilter('')
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) e.currentTarget.style.background = 'var(--bg-surface)'
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) e.currentTarget.style.background = 'transparent'
                      }}
                    >
                      {m.id}
                    </button>
                  )
                })
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

const inputStyle = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  color: 'var(--text-primary)',
  fontSize: 13,
  fontFamily: 'var(--font-ui)',
  outline: 'none',
  boxSizing: 'border-box',
}

const labelStyle = {
  display: 'block',
  marginBottom: 4,
  color: 'var(--text-secondary)',
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

function AccountTab() {
  const { t } = useTranslation()
  const user = useAuthStore((s) => s.user)
  // The workspace path is agent-runtime state — sourced from the agent-runner
  // (/api/sandbox/user/stats), not the control-panel, which doesn't own it.
  const workspace = useUserDataStore((s) => s.stats?.workspace)
  const fetchStats = useUserDataStore((s) => s.fetchStats)
  useEffect(() => { fetchStats() }, [fetchStats])

  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [showCurrent, setShowCurrent] = useState(false)
  const [showNew, setShowNew] = useState(false)
  const [showConfirm, setShowConfirm] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [saving, setSaving] = useState(false)

  const handleCancel = () => {
    setCurrentPassword('')
    setNewPassword('')
    setConfirmPassword('')
    setError('')
  }

  const handleSubmit = async () => {
    setError('')
    if (!currentPassword || !newPassword || !confirmPassword) {
      setError(t('settings.passwordRequired') || 'All fields are required')
      return
    }
    if (newPassword.length < 8) {
      setError(t('settings.passwordMin8'))
      return
    }
    if (newPassword !== confirmPassword) {
      setError(t('settings.passwordMismatch'))
      return
    }
    setSaving(true)
    try {
      await changeMyPassword(currentPassword, newPassword)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmPassword('')
      setSuccess(true)
      setTimeout(() => setSuccess(false), 1500)
    } catch (e) {
      const msg = typeof e?.message === 'string' ? e.message : 'Failed to update password'
      setError(msg)
    } finally {
      setSaving(false)
    }
  }

  const canSubmit = currentPassword && newPassword && confirmPassword && !saving

  const profileRow = (label, value) => (
    <div className="flex items-start gap-4" style={{ padding: '8px 0' }}>
      <div
        style={{
          width: 120,
          flexShrink: 0,
          color: 'var(--text-secondary)',
          fontSize: 11,
          fontWeight: 600,
          textTransform: 'uppercase',
          letterSpacing: '0.06em',
          paddingTop: 2,
        }}
      >
        {label}
      </div>
      <div
        className="flex-1 min-w-0"
        style={{
          color: 'var(--text-primary)',
          fontSize: 13,
          fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          wordBreak: 'break-word',
          overflowWrap: 'break-word',
        }}
      >
        {value || '—'}
      </div>
    </div>
  )

  const passwordField = (label, value, setter, show, setShow, hint) => (
    <div>
      <label style={labelStyle}>{label}</label>
      <div className="relative">
        <input
          type={show ? 'text' : 'password'}
          value={value}
          onChange={(e) => { setter(e.target.value); setError('') }}
          style={{ ...inputStyle, paddingRight: 36 }}
          autoComplete="new-password"
        />
        <button
          type="button"
          className="absolute flex items-center justify-center"
          style={{
            right: 8,
            top: '50%',
            transform: 'translateY(-50%)',
            background: 'transparent',
            border: 'none',
            cursor: 'pointer',
            color: 'var(--text-dim)',
            padding: 0,
          }}
          onClick={() => setShow(!show)}
          tabIndex={-1}
        >
          {show ? <EyeOff size={14} strokeWidth={1.5} /> : <Eye size={14} strokeWidth={1.5} />}
        </button>
      </div>
      {hint && (
        <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4, fontWeight: 300 }}>
          {hint}
        </div>
      )}
    </div>
  )

  return (
    <div className="flex flex-col gap-5">
      {/* Profile section */}
      <div>
        <div
          style={{
            color: 'var(--text-secondary)',
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
            marginBottom: 8,
          }}
        >
          {t('settings.profile')}
        </div>
        <div
          className="flex flex-col"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border-subtle)',
            borderRadius: 4,
            padding: '4px 16px',
          }}
        >
          {profileRow(t('settings.profileUsername'), user?.username)}
          <div style={{ height: 1, background: 'var(--border-subtle)' }} />
          {profileRow(t('settings.profileRole'), user?.role)}
          <div style={{ height: 1, background: 'var(--border-subtle)' }} />
          {profileRow(t('settings.profileWorkspace'), workspace)}
        </div>
      </div>

      {/* Divider */}
      <div style={{ borderBottom: '1px solid var(--border)' }} />

      {/* Change password section */}
      <div className="flex flex-col gap-4">
        <div
          style={{
            color: 'var(--text-secondary)',
            fontSize: 11,
            fontWeight: 600,
            textTransform: 'uppercase',
            letterSpacing: '0.06em',
          }}
        >
          {t('settings.changePassword')}
        </div>

        {passwordField(t('settings.currentPassword'), currentPassword, setCurrentPassword, showCurrent, setShowCurrent)}
        {passwordField(t('settings.newPassword'), newPassword, setNewPassword, showNew, setShowNew, t('settings.passwordMin8'))}
        {passwordField(t('settings.confirmPassword'), confirmPassword, setConfirmPassword, showConfirm, setShowConfirm)}

        {error && (
          <div
            className="flex items-center gap-2 px-3 py-2"
            style={{
              borderLeft: '2px solid var(--red)',
              background: 'var(--bg-elevated)',
              borderRadius: 2,
            }}
          >
            <AlertCircle size={12} strokeWidth={1.5} style={{ color: 'var(--red)' }} />
            <span className="text-xs" style={{ color: 'var(--red)' }}>{error}</span>
          </div>
        )}

        {success && (
          <div
            className="flex items-center gap-2 px-3 py-2"
            style={{
              borderLeft: '2px solid var(--green)',
              background: 'var(--bg-elevated)',
              borderRadius: 2,
            }}
          >
            <Check size={12} strokeWidth={1.5} style={{ color: 'var(--green)' }} />
            <span className="text-xs" style={{ color: 'var(--green)' }}>{t('settings.passwordUpdated')}</span>
          </div>
        )}

        <div className="flex items-center gap-2 justify-end">
          <button
            type="button"
            className="px-3 py-2 text-xs"
            disabled={saving}
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 4,
              color: 'var(--text-secondary)',
              cursor: saving ? 'default' : 'pointer',
            }}
            onClick={handleCancel}
          >
            {t('settings.cancel')}
          </button>
          <button
            type="button"
            className="px-3 py-2 text-xs font-semibold"
            disabled={!canSubmit}
            style={{
              background: canSubmit ? 'var(--blue)' : 'var(--bg-elevated)',
              color: canSubmit ? 'var(--text-inverse)' : 'var(--text-dim)',
              border: 'none',
              borderRadius: 4,
              cursor: canSubmit ? 'pointer' : 'default',
              opacity: canSubmit ? 1 : 0.5,
              transition: 'opacity 150ms ease',
            }}
            onClick={handleSubmit}
          >
            {saving ? t('settings.saving') : t('settings.updatePassword')}
          </button>
        </div>
      </div>
    </div>
  )
}

function ApiKeyTab() {
  const { t } = useTranslation()
  const apiKey = useSettingsStore((s) => s.apiKey)
  const apiKeyLoading = useSettingsStore((s) => s.apiKeyLoading)
  const fetchApiKey = useSettingsStore((s) => s.fetchApiKey)
  const generateApiKey = useSettingsStore((s) => s.generateApiKey)
  const revokeApiKey = useSettingsStore((s) => s.revokeApiKey)

  const [showKey, setShowKey] = useState(false)
  const [copied, setCopied] = useState(false)
  const [confirmAction, setConfirmAction] = useState(null) // 'regenerate' | 'revoke' | null

  useEffect(() => {
    fetchApiKey()
  }, [fetchApiKey])

  const handleCopy = () => {
    if (apiKey?.api_key) {
      copyTextToClipboard(apiKey.api_key)
      setCopied(true)
      setTimeout(() => setCopied(false), 800)
    }
  }

  const handleGenerate = async () => {
    await generateApiKey()
    setConfirmAction(null)
    setShowKey(true)
  }

  const handleRevoke = async () => {
    await revokeApiKey()
    setConfirmAction(null)
    setShowKey(false)
  }

  const hasKey = apiKey?.has_key

  return (
    <div className="flex flex-col gap-5">
      <p className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
        {t('settings.apiKeyDesc')} <span style={{ fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace" }}>{t('settings.authHeader')}</span>{t('settings.header')}
      </p>

      {hasKey && apiKey?.api_key && (
        <div>
          <label style={labelStyle}>{t('settings.yourApiKey')}</label>
          <div className="flex items-center gap-2">
            <div className="relative flex-1">
              <input
                type={showKey ? 'text' : 'password'}
                readOnly
                value={apiKey.api_key}
                style={{
                  ...inputStyle,
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 12,
                  paddingRight: 64,
                }}
              />
              <div
                className="absolute flex items-center gap-1"
                style={{
                  right: 8,
                  top: '50%',
                  transform: 'translateY(-50%)',
                }}
              >
                <button
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-dim)',
                    padding: 2,
                    transition: 'color 150ms ease',
                  }}
                  onClick={() => setShowKey(!showKey)}
                  type="button"
                >
                  {showKey ? <EyeOff size={14} strokeWidth={1.5} /> : <Eye size={14} strokeWidth={1.5} />}
                </button>
                <button
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: copied ? 'var(--green)' : 'var(--text-dim)',
                    padding: 2,
                    transition: 'color 150ms ease',
                  }}
                  onClick={handleCopy}
                  type="button"
                >
                  {copied ? <DrawIcon name="check" size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}
                </button>
              </div>
            </div>
          </div>
        </div>
      )}

      {!hasKey && !apiKeyLoading && (
        <div
          className="flex items-center gap-2 px-3 py-2"
          style={{
            borderLeft: '2px solid var(--border)',
            background: 'var(--bg-elevated)',
            borderRadius: 2,
          }}
        >
          <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('settings.noApiKey')}</span>
        </div>
      )}

      <div className="flex items-center gap-2">
        {hasKey ? (
          <>
            <button
              className="flex items-center gap-2 px-4 py-2 text-xs font-semibold"
              disabled={apiKeyLoading}
              style={{
                background: 'var(--bg-elevated)',
                color: 'var(--text-primary)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                cursor: 'pointer',
                transition: 'border-color 150ms ease',
              }}
              onClick={() => setConfirmAction('regenerate')}
            >
              <RefreshCw size={12} strokeWidth={1.5} />
              {t('settings.regenerate')}
            </button>
            <button
              className="flex items-center gap-2 px-4 py-2 text-xs font-semibold"
              disabled={apiKeyLoading}
              style={{
                background: 'transparent',
                color: 'var(--red)',
                border: '1px solid var(--red)',
                borderRadius: 4,
                cursor: 'pointer',
                transition: 'opacity 150ms ease',
              }}
              onClick={() => setConfirmAction('revoke')}
            >
              <Trash2 size={12} strokeWidth={1.5} />
              {t('settings.revoke')}
            </button>
          </>
        ) : (
          <button
            className="flex items-center gap-2 px-4 py-2 text-xs font-semibold"
            disabled={apiKeyLoading}
            style={{
              background: 'var(--blue)',
              color: 'var(--text-inverse)',
              border: 'none',
              borderRadius: 4,
              cursor: 'pointer',
            }}
            onClick={handleGenerate}
          >
            <Key size={12} strokeWidth={1.5} />
            {t('settings.generateKey')}
          </button>
        )}
      </div>

      {/* Confirmation Dialog */}
      {confirmAction && (
        <div
          className="fixed inset-0 flex items-center justify-center"
          style={{
            background: 'var(--bg-overlay)',
            backdropFilter: 'blur(4px)',
            zIndex: 100,
          }}
          onClick={() => setConfirmAction(null)}
        >
          <div
            className="flex flex-col gap-4 p-6"
            style={{
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              width: 400,
              maxWidth: '90vw',
              animation: 'scale-in 200ms cubic-bezier(0.16, 1, 0.3, 1)',
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h3 className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14, margin: 0 }}>
              {confirmAction === 'regenerate' ? t('settings.regenerateTitle') : t('settings.revokeTitle')}
            </h3>
            <p className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0 }}>
              {confirmAction === 'regenerate' ? t('settings.regenerateMsg') : t('settings.revokeMsg')}
            </p>
            <div className="flex items-center gap-2 justify-end">
              <button
                className="px-3 py-2 text-xs"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border)',
                  borderRadius: 4,
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                }}
                onClick={() => setConfirmAction(null)}
              >
                {t('settings.cancel')}
              </button>
              <button
                className="px-3 py-2 text-xs font-semibold"
                style={{
                  background: confirmAction === 'revoke' ? 'var(--red)' : 'var(--blue)',
                  color: 'var(--text-inverse)',
                  border: 'none',
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
                onClick={confirmAction === 'regenerate' ? handleGenerate : handleRevoke}
              >
                {confirmAction === 'regenerate' ? t('settings.regenerate') : t('settings.revoke')}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}

function ModelsTab() {
  const { t } = useTranslation()
  const env = useSettingsStore((s) => s.env)
  const models = useSettingsStore((s) => s.models)
  const fetchEnv = useSettingsStore((s) => s.fetchEnv)
  const fetchModels = useSettingsStore((s) => s.fetchModels)
  const saveEnv = useSettingsStore((s) => s.saveEnv)
  const fetchVisionModel = useSettingsStore((s) => s.fetchVisionModel)
  const saveVisionModel = useSettingsStore((s) => s.saveVisionModel)

  const [baseUrl, setBaseUrl] = useState('')
  const [authToken, setAuthToken] = useState('')
  const [showToken, setShowToken] = useState(false)
  const [connStatus, setConnStatus] = useState(null) // null | 'loading' | 'success' | 'error'
  const [connMsg, setConnMsg] = useState('')
  const [connDirty, setConnDirty] = useState(false)
  const [connSaving, setConnSaving] = useState(false)

  const [defaultModel, setDefaultModel] = useState('')
  const [opusModel, setOpusModel] = useState('')
  const [sonnetModel, setSonnetModel] = useState('')
  const [haikuModel, setHaikuModel] = useState('')
  const [saving, setSaving] = useState(false)
  const [dirty, setDirty] = useState(false)

  const [visionModel, setVisionModel] = useState('')
  const [visionDirty, setVisionDirty] = useState(false)
  const [visionSaving, setVisionSaving] = useState(false)

  useEffect(() => {
    fetchEnv().then((data) => {
      if (data?.env) {
        setBaseUrl(data.env.ANTHROPIC_BASE_URL || '')
        setAuthToken(data.env.ANTHROPIC_AUTH_TOKEN || '')
        setDefaultModel(data.env.ANTHROPIC_MODEL || '')
        setOpusModel(data.env.ANTHROPIC_DEFAULT_OPUS_MODEL || '')
        setSonnetModel(data.env.ANTHROPIC_DEFAULT_SONNET_MODEL || '')
        setHaikuModel(data.env.ANTHROPIC_DEFAULT_HAIKU_MODEL || '')
      }
    })
    fetchVisionModel().then((vm) => {
      if (vm) setVisionModel(vm)
    })
    if (models.length === 0) fetchModels()
  }, [fetchEnv, fetchModels, fetchVisionModel, models.length])

  const testConnection = useCallback(async () => {
    if (!baseUrl.trim() || !authToken.trim()) return
    setConnStatus('loading')
    setConnMsg('')

    try {
      await saveEnv({
        ANTHROPIC_BASE_URL: baseUrl.trim(),
        ANTHROPIC_AUTH_TOKEN: authToken.trim(),
      })
    } catch {
      setConnStatus('error')
      setConnMsg(t('settings.failedToSave'))
      return
    }

    const result = await fetchModels()
    if (result.length > 0) {
      setConnStatus('success')
      setConnMsg(t('settings.connectedModels', { count: result.length }))
      setConnDirty(false)
    } else {
      setConnStatus('error')
      setConnMsg(useSettingsStore.getState().modelsError || t('settings.connectionFailed'))
    }
  }, [baseUrl, authToken, saveEnv, fetchModels])

  const handleConnBlur = () => {
    if (baseUrl.trim() && authToken.trim() && connDirty) {
      testConnection()
    }
  }

  const handleConnSave = async () => {
    setConnSaving(true)
    try {
      await saveEnv({
        ANTHROPIC_BASE_URL: baseUrl.trim(),
        ANTHROPIC_AUTH_TOKEN: authToken.trim(),
      })
      setConnDirty(false)
      await testConnection()
    } catch {
      // handled
    } finally {
      setConnSaving(false)
    }
  }

  const handleSave = async () => {
    setSaving(true)
    try {
      await saveEnv({
        ANTHROPIC_MODEL: defaultModel,
        ANTHROPIC_DEFAULT_OPUS_MODEL: opusModel,
        ANTHROPIC_DEFAULT_SONNET_MODEL: sonnetModel,
        ANTHROPIC_DEFAULT_HAIKU_MODEL: haikuModel,
      })
      setDirty(false)
    } catch {
      // handled
    } finally {
      setSaving(false)
    }
  }

  const modelFields = [
    { label: t('settings.defaultModel'), value: defaultModel, setter: setDefaultModel },
    { label: t('settings.opusModel'), value: opusModel, setter: setOpusModel },
    { label: t('settings.sonnetModel'), value: sonnetModel, setter: setSonnetModel },
    { label: t('settings.haikuModel'), value: haikuModel, setter: setHaikuModel },
  ]

  return (
    <div className="flex flex-col gap-5">
      {/* API Connection */}
      <div>
        <label style={labelStyle}>{t('settings.baseUrl')}</label>
        <input
          type="text"
          value={baseUrl}
          onChange={(e) => { setBaseUrl(e.target.value); setConnDirty(true) }}
          onBlur={handleConnBlur}
          placeholder="http://your-api-server:port/"
          style={inputStyle}
        />
      </div>
      <div>
        <label style={labelStyle}>{t('settings.authToken')}</label>
        <div className="relative">
          <input
            type={showToken ? 'text' : 'password'}
            value={authToken}
            onChange={(e) => { setAuthToken(e.target.value); setConnDirty(true) }}
            onBlur={handleConnBlur}
            placeholder="sk-..."
            style={{ ...inputStyle, paddingRight: 36 }}
          />
          <button
            className="absolute flex items-center justify-center"
            style={{
              right: 8,
              top: '50%',
              transform: 'translateY(-50%)',
              background: 'transparent',
              border: 'none',
              cursor: 'pointer',
              color: 'var(--text-dim)',
              padding: 0,
            }}
            onClick={() => setShowToken(!showToken)}
            type="button"
          >
            {showToken ? <EyeOff size={14} strokeWidth={1.5} /> : <Eye size={14} strokeWidth={1.5} />}
          </button>
        </div>
      </div>

      {connStatus && (
        <div
          className="flex items-center gap-2 px-3 py-2"
          style={{
            borderLeft: `2px solid ${connStatus === 'success' ? 'var(--green)' : connStatus === 'error' ? 'var(--red)' : 'var(--yellow)'}`,
            background: 'var(--bg-elevated)',
            borderRadius: 2,
          }}
        >
          {connStatus === 'loading' && <span className="text-xs" style={{ color: 'var(--yellow)' }}>{t('settings.connecting')}</span>}
          {connStatus === 'success' && (
            <>
              <Check size={12} strokeWidth={1.5} style={{ color: 'var(--green)' }} />
              <span className="text-xs" style={{ color: 'var(--green)' }}>{connMsg}</span>
            </>
          )}
          {connStatus === 'error' && (
            <>
              <AlertCircle size={12} strokeWidth={1.5} style={{ color: 'var(--red)' }} />
              <span className="text-xs" style={{ color: 'var(--red)' }}>{connMsg}</span>
            </>
          )}
        </div>
      )}

      <div className="flex justify-end">
        <button
          className="px-4 py-2 text-xs font-semibold"
          disabled={connSaving || !connDirty}
          style={{
            background: connDirty ? 'var(--blue)' : 'var(--bg-elevated)',
            color: connDirty ? 'var(--text-inverse)' : 'var(--text-dim)',
            border: 'none',
            borderRadius: 4,
            cursor: connDirty ? 'pointer' : 'default',
            opacity: connDirty ? 1 : 0.5,
            transition: 'opacity 150ms ease',
          }}
          onClick={handleConnSave}
        >
          {connSaving ? t('settings.saving') : t('settings.save')}
        </button>
      </div>

      {/* Divider */}
      <div style={{ borderBottom: '1px solid var(--border)' }} />

      {/* Model Selectors */}
      {modelFields.map(({ label, value, setter }) => (
        <FilterableModelSelect
          key={label}
          label={label}
          models={models}
          value={value}
          onChange={(v) => { setter(v); setDirty(true) }}
          labelStyle={labelStyle}
          inputStyle={inputStyle}
          placeholder={t('settings.selectModel')}
          filterPlaceholder={t('settings.filterModels')}
          noMatchesText={t('settings.noMatches')}
        />
      ))}
      <div className="flex justify-end">
        <button
          className="px-4 py-2 text-xs font-semibold"
          disabled={saving || !dirty}
          style={{
            background: dirty ? 'var(--blue)' : 'var(--bg-elevated)',
            color: dirty ? 'var(--text-inverse)' : 'var(--text-dim)',
            border: 'none',
            borderRadius: 4,
            cursor: dirty ? 'pointer' : 'default',
            opacity: dirty ? 1 : 0.5,
          }}
          onClick={handleSave}
        >
          {saving ? t('settings.saving') : t('settings.save')}
        </button>
      </div>

      {/* Divider */}
      <div style={{ borderBottom: '1px solid var(--border)' }} />

      {/* Vision Model */}
      <div className="flex flex-col gap-2">
        <span className="text-xs font-semibold uppercase" style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
          {t('settings.visionModel')}
        </span>
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
          {t('settings.visionModelDesc')}
        </span>
        <FilterableModelSelect
          label={t('settings.visionModel')}
          models={models}
          value={visionModel}
          onChange={(v) => { setVisionModel(v); setVisionDirty(true) }}
          labelStyle={{ ...labelStyle, display: 'none' }}
          inputStyle={inputStyle}
          placeholder={t('settings.visionModelPlaceholder')}
          filterPlaceholder={t('settings.filterModels')}
          noMatchesText={t('settings.noMatches')}
        />
        <div className="flex items-center gap-2 justify-end">
          {visionModel && (
            <button
              className="px-3 py-2 text-xs"
              style={{
                background: 'transparent',
                color: 'var(--text-secondary)',
                border: '1px solid var(--border)',
                borderRadius: 4,
                cursor: 'pointer',
              }}
              onClick={() => { setVisionModel(''); setVisionDirty(true) }}
            >
              {t('settings.clear')}
            </button>
          )}
          <button
            className="px-4 py-2 text-xs font-semibold"
            disabled={visionSaving || !visionDirty}
            style={{
              background: visionDirty ? 'var(--blue)' : 'var(--bg-elevated)',
              color: visionDirty ? 'var(--text-inverse)' : 'var(--text-dim)',
              border: 'none',
              borderRadius: 4,
              cursor: visionDirty ? 'pointer' : 'default',
              opacity: visionDirty ? 1 : 0.5,
            }}
            onClick={async () => {
              setVisionSaving(true)
              try {
                await saveVisionModel(visionModel || null)
                setVisionDirty(false)
              } finally {
                setVisionSaving(false)
              }
            }}
          >
            {visionSaving ? t('settings.saving') : t('settings.save')}
          </button>
        </div>
      </div>
    </div>
  )
}

function IconPicker({ value, onChange, labelStyle }) {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const containerRef = useRef(null)
  const filterRef = useRef(null)

  const filtered = useMemo(() => {
    if (!filter.trim()) return ICON_NAMES
    const q = filter.toLowerCase()
    return ICON_NAMES.filter((name) => name.includes(q))
  }, [filter])

  useEffect(() => {
    const handler = (e) => {
      if (containerRef.current && !containerRef.current.contains(e.target)) {
        setOpen(false)
        setFilter('')
      }
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  useEffect(() => {
    if (open && filterRef.current) filterRef.current.focus()
  }, [open])

  const SelectedIcon = value ? getLucideIcon(value) : null

  return (
    <div>
      <label style={labelStyle}>Icon</label>
      <div className="relative" ref={containerRef}>
        <button
          type="button"
          className="flex items-center gap-2 w-full"
          style={{
            padding: '8px 12px',
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            color: value ? 'var(--text-primary)' : 'var(--text-dim)',
            fontSize: 12,
            cursor: 'pointer',
            textAlign: 'left',
            boxSizing: 'border-box',
          }}
          onClick={() => setOpen(!open)}
        >
          {SelectedIcon ? (
            <>
              <SelectedIcon size={14} strokeWidth={1.5} />
              <span style={{ fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace" }}>{value}</span>
            </>
          ) : (
            <span>Select icon...</span>
          )}
          <ChevronDown
            size={12}
            strokeWidth={1.5}
            style={{ marginLeft: 'auto', color: 'var(--text-dim)' }}
          />
        </button>

        {open && (
          <div
            className="absolute left-0 right-0 flex flex-col"
            style={{
              top: '100%',
              marginTop: 2,
              background: 'var(--bg-elevated)',
              border: '1px solid var(--border)',
              borderRadius: 4,
              maxHeight: 260,
              zIndex: 60,
            }}
          >
            <div
              className="flex items-center gap-2 px-2 flex-shrink-0"
              style={{ borderBottom: '1px solid var(--border)' }}
            >
              <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
              <input
                ref={filterRef}
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder={t('settings.searchIcons')}
                className="flex-1 py-2 text-xs"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: 'var(--text-primary)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 12,
                  minWidth: 0,
                }}
              />
              {value && (
                <button
                  type="button"
                  className="flex items-center justify-center"
                  style={{
                    background: 'transparent',
                    border: 'none',
                    cursor: 'pointer',
                    color: 'var(--text-dim)',
                    padding: 2,
                  }}
                  onClick={() => { onChange(''); setOpen(false); setFilter('') }}
                  title={t('settings.clearIcon')}
                >
                  <X size={12} strokeWidth={1.5} />
                </button>
              )}
            </div>
            <div
              className="overflow-y-auto px-2 py-2"
              style={{ maxHeight: 220, display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: 2 }}
            >
              {filtered.length === 0 ? (
                <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)', gridColumn: '1 / -1' }}>
                  {t('settings.noMatches')}
                </div>
              ) : (
                filtered.map((name) => {
                  const IconComp = getLucideIcon(name)
                  if (!IconComp) return null
                  const isActive = value === name
                  return (
                    <button
                      key={name}
                      type="button"
                      className="flex items-center justify-center"
                      title={name}
                      style={{
                        width: '100%',
                        aspectRatio: '1',
                        background: isActive ? 'var(--bg-surface)' : 'transparent',
                        border: isActive ? '1px solid var(--cyan)' : '1px solid transparent',
                        borderRadius: 4,
                        color: isActive ? 'var(--cyan)' : 'var(--text-secondary)',
                        cursor: 'pointer',
                        transition: 'background 150ms ease, color 150ms ease',
                      }}
                      onClick={() => {
                        onChange(name)
                        setOpen(false)
                        setFilter('')
                      }}
                      onMouseEnter={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.background = 'var(--bg-surface)'
                          e.currentTarget.style.color = 'var(--text-primary)'
                        }
                      }}
                      onMouseLeave={(e) => {
                        if (!isActive) {
                          e.currentTarget.style.background = 'transparent'
                          e.currentTarget.style.color = 'var(--text-secondary)'
                        }
                      }}
                    >
                      <IconComp size={16} strokeWidth={1.5} />
                    </button>
                  )
                })
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

function QuickActionsTab() {
  const { t } = useTranslation()
  const quickActions = useSettingsStore((s) => s.quickActions)
  const fetchQuickActions = useSettingsStore((s) => s.fetchQuickActions)
  const saveQuickActions = useSettingsStore((s) => s.saveQuickActions)

  const [items, setItems] = useState([])
  const [editingIndex, setEditingIndex] = useState(null)
  const [newName, setNewName] = useState('')
  const [newPrompt, setNewPrompt] = useState('')
  const [newIcon, setNewIcon] = useState('')
  const [showAddForm, setShowAddForm] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    fetchQuickActions()
  }, [fetchQuickActions])

  useEffect(() => {
    setItems(quickActions.map((qa) => ({ ...qa })))
  }, [quickActions])

  const handleSave = async (updatedItems) => {
    setSaving(true)
    try {
      await saveQuickActions(updatedItems || items)
    } catch {
      // handled
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (index) => {
    const updated = items.filter((_, i) => i !== index)
    setItems(updated)
    await handleSave(updated)
  }

  const handleAdd = async () => {
    if (!newName.trim() || !newPrompt.trim()) return
    const updated = [...items, { name: newName.trim(), prompt: newPrompt.trim(), icon: newIcon.trim() || null }]
    setItems(updated)
    setNewName('')
    setNewPrompt('')
    setNewIcon('')
    setShowAddForm(false)
    await handleSave(updated)
  }

  const handleEditSave = async (index, name, prompt, icon) => {
    const updated = [...items]
    updated[index] = { name, prompt, icon: icon || null }
    setItems(updated)
    setEditingIndex(null)
    await handleSave(updated)
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5, margin: 0 }}>
        {t('settings.quickActionsDesc')}
      </p>
      {items.map((item, i) => (
        <div key={i}>
          {editingIndex === i ? (
            <EditForm
              item={item}
              onSave={(name, prompt, icon) => handleEditSave(i, name, prompt, icon)}
              onCancel={() => setEditingIndex(null)}
            />
          ) : (
            <div
              className="flex items-center gap-3 px-3 py-2"
              style={{
                background: 'var(--bg-elevated)',
                borderRadius: 4,
                borderLeft: '2px solid var(--border)',
              }}
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-semibold" style={{ color: 'var(--text-primary)' }}>
                    {item.name}
                  </span>
                  {item.icon && (() => {
                    const IconComp = getLucideIcon(item.icon, null)
                    return IconComp
                      ? <IconComp size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)' }} />
                      : <span className="text-xs" style={{ color: 'var(--text-dim)', fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace" }}>{item.icon}</span>
                  })()}
                </div>
                <p className="text-xs truncate" style={{ color: 'var(--text-secondary)', margin: '2px 0 0' }}>
                  {item.prompt}
                </p>
              </div>
              <button
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4 }}
                onClick={() => setEditingIndex(i)}
              >
                <Pencil size={12} strokeWidth={1.5} />
              </button>
              <button
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4 }}
                onClick={() => handleDelete(i)}
              >
                <Trash2 size={12} strokeWidth={1.5} />
              </button>
            </div>
          )}
        </div>
      ))}

      {showAddForm ? (
        <div className="flex flex-col gap-3 px-3 py-3" style={{ background: 'var(--bg-elevated)', borderRadius: 4 }}>
          <div>
            <label style={labelStyle}>{t('settings.name')}</label>
            <input value={newName} onChange={(e) => setNewName(e.target.value)} style={inputStyle} placeholder="e.g. Summarize" autoFocus />
          </div>
          <div>
            <label style={labelStyle}>{t('settings.prompt')}</label>
            <textarea
              value={newPrompt}
              onChange={(e) => setNewPrompt(e.target.value)}
              style={{ ...inputStyle, minHeight: 60, resize: 'vertical' }}
              placeholder="e.g. Summarize this code"
            />
          </div>
          <IconPicker value={newIcon} onChange={setNewIcon} labelStyle={labelStyle} />
          <div className="flex items-center gap-2 justify-end">
            <button
              className="px-3 py-1 text-xs"
              style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}
              onClick={() => { setShowAddForm(false); setNewName(''); setNewPrompt(''); setNewIcon('') }}
            >
              {t('settings.cancel')}
            </button>
            <button
              className="px-3 py-1 text-xs font-semibold"
              disabled={!newName.trim() || !newPrompt.trim()}
              style={{
                background: newName.trim() && newPrompt.trim() ? 'var(--blue)' : 'var(--bg-surface)',
                color: newName.trim() && newPrompt.trim() ? 'var(--text-inverse)' : 'var(--text-dim)',
                border: 'none',
                borderRadius: 4,
                cursor: newName.trim() && newPrompt.trim() ? 'pointer' : 'default',
              }}
              onClick={handleAdd}
            >
              {t('settings.add')}
            </button>
          </div>
        </div>
      ) : (
        <button
          className="flex items-center gap-2 px-3 py-2 text-xs"
          style={{
            background: 'transparent',
            border: '1px dashed var(--border)',
            borderRadius: 4,
            color: 'var(--text-dim)',
            cursor: 'pointer',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onClick={() => setShowAddForm(true)}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.borderColor = 'var(--border-strong)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-dim)'
            e.currentTarget.style.borderColor = 'var(--border)'
          }}
        >
          <Plus size={14} strokeWidth={1.5} />
          {t('settings.addQuickAction')}
        </button>
      )}
    </div>
  )
}

function EditForm({ item, onSave, onCancel }) {
  const { t } = useTranslation()
  const [name, setName] = useState(item.name)
  const [prompt, setPrompt] = useState(item.prompt)
  const [icon, setIcon] = useState(item.icon || '')

  return (
    <div className="flex flex-col gap-3 px-3 py-3" style={{ background: 'var(--bg-elevated)', borderRadius: 4, borderLeft: '2px solid var(--blue)' }}>
      <div>
        <label style={labelStyle}>{t('settings.name')}</label>
        <input value={name} onChange={(e) => setName(e.target.value)} style={inputStyle} autoFocus />
      </div>
      <div>
        <label style={labelStyle}>{t('settings.prompt')}</label>
        <textarea value={prompt} onChange={(e) => setPrompt(e.target.value)} style={{ ...inputStyle, minHeight: 60, resize: 'vertical' }} />
      </div>
      <IconPicker value={icon} onChange={setIcon} labelStyle={labelStyle} />
      <div className="flex items-center gap-2 justify-end">
        <button
          className="px-3 py-1 text-xs"
          style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}
          onClick={onCancel}
        >
          {t('settings.cancel')}
        </button>
        <button
          className="px-3 py-1 text-xs font-semibold"
          disabled={!name.trim() || !prompt.trim()}
          style={{
            background: name.trim() && prompt.trim() ? 'var(--blue)' : 'var(--bg-surface)',
            color: name.trim() && prompt.trim() ? 'var(--text-inverse)' : 'var(--text-dim)',
            border: 'none',
            borderRadius: 4,
            cursor: name.trim() && prompt.trim() ? 'pointer' : 'default',
          }}
          onClick={() => onSave(name.trim(), prompt.trim(), icon.trim())}
        >
          {t('settings.save')}
        </button>
      </div>
    </div>
  )
}

function SettingRow({ label, desc, checked, onChange, disabled = false }) {
  return (
    <div className="flex items-start justify-between gap-4">
      <div
        className="flex flex-col min-w-0"
        style={{ opacity: disabled ? 0.55 : 1, transition: 'opacity 150ms ease' }}
      >
        <span
          className="text-xs font-semibold uppercase"
          style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em' }}
        >
          {label}
        </span>
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)', lineHeight: 1.5, marginTop: 4 }}>
          {desc}
        </span>
      </div>
      <div className="flex-shrink-0" style={{ paddingTop: 2 }}>
        <Toggle checked={checked} onChange={onChange} disabled={disabled} ariaLabel={label} />
      </div>
    </div>
  )
}

function AdvancedTab() {
  const { t } = useTranslation()
  const transport = useSettingsStore((s) => s.transport)
  const setTransport = useSettingsStore((s) => s.setTransport)
  const developerMode = useSettingsStore((s) => s.developerMode)
  const setDeveloperMode = useSettingsStore((s) => s.setDeveloperMode)
  const debugMode = useSettingsStore((s) => s.debugMode)
  const setDebugMode = useSettingsStore((s) => s.setDebugMode)
  const recapEnabled = useSettingsStore((s) => s.recapEnabled)
  const saveRecapEnabled = useSettingsStore((s) => s.saveRecapEnabled)
  const fetchRecapEnabled = useSettingsStore((s) => s.fetchRecapEnabled)

  // Unlike the localStorage switches above, this one's truth lives on the pod.
  useEffect(() => { fetchRecapEnabled() }, [fetchRecapEnabled])

  const options = [
    { value: 'ws', label: t('settings.transportWs') },
    { value: 'sse', label: t('settings.transportSse') },
  ]

  return (
    <div className="flex flex-col gap-5">
      {/* Streaming transport */}
      <div className="flex flex-col gap-4">
        <label style={{ ...labelStyle, marginBottom: 0 }}>{t('settings.transportMode')}</label>
        <div className="flex gap-2">
          {options.map((opt) => {
            const isActive = transport === opt.value
            return (
              <button
                key={opt.value}
                className="flex items-center gap-2 px-3 py-2 text-sm"
                style={{
                  background: isActive ? 'var(--bg-elevated)' : 'var(--bg-surface)',
                  border: isActive ? '1px solid var(--blue)' : '1px solid var(--border)',
                  borderRadius: 4,
                  color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                  cursor: 'pointer',
                  fontWeight: isActive ? 600 : 400,
                  transition: 'background 150ms ease, border-color 150ms ease, color 150ms ease',
                }}
                onClick={() => setTransport(opt.value)}
              >
                {isActive && <Check size={14} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />}
                {opt.label}
              </button>
            )
          })}
        </div>
        <p className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
          {t('settings.transportNote')}
        </p>
      </div>

      {/* Divider */}
      <div style={{ borderBottom: '1px solid var(--border)' }} />

      {/* Session recap — server-side, so a failed write reverts the switch */}
      <SettingRow
        label={t('settings.sessionRecap')}
        desc={t('settings.sessionRecapDesc')}
        checked={recapEnabled}
        onChange={(next) => { saveRecapEnabled(next).catch(() => {}) }}
      />

      {/* Divider */}
      <div style={{ borderBottom: '1px solid var(--border)' }} />

      {/* Developer Mode — master gate for the dev switches below */}
      <SettingRow
        label={t('settings.developerMode')}
        desc={t('settings.developerModeDesc')}
        checked={developerMode}
        onChange={setDeveloperMode}
      />

      {/* Dev switches — always shown, disabled until Developer Mode is on */}
      <div
        className="flex flex-col gap-4"
        style={{ paddingLeft: 16, marginLeft: 2, borderLeft: '1px solid var(--border-subtle)' }}
      >
        <SettingRow
          label={t('settings.debugLogging')}
          desc={t('settings.debugLoggingDesc')}
          checked={debugMode}
          onChange={setDebugMode}
          disabled={!developerMode}
        />
      </div>
    </div>
  )
}

// Last path segment for compact cwd display (full path stays in the title attr).
function lastSeg(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

function archivedTitle(s) {
  return s.custom_title || s.first_prompt || s.summary || s.session_id
}

function ArchivedTab() {
  const { t } = useTranslation()
  const [sessions, setSessions] = useState(null) // null = loading
  const [error, setError] = useState(null)
  const [busyId, setBusyId] = useState(null)

  useEffect(() => {
    let alive = true
    fetchArchivedSessions()
      .then((data) => { if (alive) setSessions(data.sessions || []) })
      .catch((err) => { if (alive) { setError(String(err?.message || err)); setSessions([]) } })
    return () => { alive = false }
  }, [])

  const handleUnarchive = async (s) => {
    setBusyId(s.session_id)
    try {
      await apiArchiveSession(s.session_id, false)
      setSessions((prev) => prev.filter((x) => x.session_id !== s.session_id))
      // Re-sync the sidebar so the session (and its workdir) reappears.
      useSidebarStore.getState().fetchSessions()
    } catch (err) {
      setError(String(err?.message || err))
    } finally {
      setBusyId(null)
    }
  }

  // Group archived sessions by their workdir (most-recent activity first),
  // mirroring the sidebar's cwd grouping.
  const groups = useMemo(() => {
    if (!sessions) return []
    const byCwd = new Map()
    for (const s of sessions) {
      const arr = byCwd.get(s.cwd) || []
      arr.push(s)
      byCwd.set(s.cwd, arr)
    }
    const out = []
    for (const [cwd, list] of byCwd) {
      out.push({ cwd, sessions: list, lastActivity: list[0]?.last_modified || 0 })
    }
    out.sort((a, b) => (b.lastActivity || 0) - (a.lastActivity || 0))
    return out
  }, [sessions])

  if (sessions === null) {
    return (
      <div className="flex flex-col gap-2">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton" style={{ height: 48, width: '100%' }} />
        ))}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      <p className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
        {t('settings.archivedHint')}
      </p>
      {error && (
        <div className="flex items-center gap-2 text-xs" style={{ color: 'var(--red)' }}>
          <AlertCircle size={14} strokeWidth={1.5} />
          {error}
        </div>
      )}
      {sessions.length === 0 ? (
        <div
          className="flex flex-col items-center justify-center gap-2 text-sm"
          style={{ color: 'var(--text-dim)', padding: '40px 0' }}
        >
          <Archive size={24} strokeWidth={1.5} />
          {t('settings.archivedEmpty')}
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          {groups.map((g) => (
            <div key={g.cwd} className="flex flex-col gap-1">
              {/* Workdir header */}
              <div className="flex items-center gap-2 px-1 py-1 min-w-0" style={{ color: 'var(--text-secondary)' }}>
                <FolderBookmark size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                <span className="truncate" style={{ fontSize: 12, fontWeight: 600, minWidth: 0 }} title={g.cwd}>
                  {lastSeg(g.cwd)}
                </span>
                <span style={{ fontSize: 11, fontWeight: 600, color: 'var(--text-dim)', flexShrink: 0 }}>{g.sessions.length}</span>
              </div>
              {/* Sessions in this workdir */}
              {g.sessions.map((s) => (
                <div
                  key={s.session_id}
                  className="flex items-center gap-3 px-3 py-2 min-w-0"
                  style={{
                    marginLeft: 8,
                    background: 'var(--bg-surface)',
                    border: '1px solid var(--border)',
                    borderRadius: 4,
                  }}
                >
                  <MessageSquare size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
                  <span className="truncate flex-1" style={{ fontSize: 13, color: 'var(--text-primary)', minWidth: 0 }}>
                    {archivedTitle(s)}
                  </span>
                  <button
                    className="flex items-center gap-2 px-2 py-1 flex-shrink-0"
                    style={{
                      background: 'transparent',
                      border: '1px solid var(--border)',
                      borderRadius: 4,
                      color: 'var(--text-secondary)',
                      cursor: busyId === s.session_id ? 'default' : 'pointer',
                      fontSize: 12,
                      transition: 'border-color 150ms ease, color 150ms ease',
                    }}
                    disabled={busyId === s.session_id}
                    onClick={() => handleUnarchive(s)}
                    onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.color = 'var(--text-primary)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
                  >
                    <ArchiveRestore size={14} strokeWidth={1.5} />
                    {t('settings.unarchive')}
                  </button>
                </div>
              ))}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

const FEISHU_MONO = "'JetBrains Mono', 'Source Han Mono SC', monospace"
const feishuSecBtn = {
  padding: '6px 12px', borderRadius: 4, fontSize: 12, background: 'transparent',
  border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer',
}

function feishuStatusView(cfg, t) {
  if (!cfg) return { color: 'var(--border)', label: '—', desc: '' }
  if (cfg.admin_disabled) return { color: 'var(--border)', label: t('settings.feishu.stDisabledAdmin'), desc: '' }
  if (!cfg.app_id || !cfg.app_secret_set) {
    return { color: 'var(--border)', label: t('settings.feishu.stNotConfigured'), desc: t('settings.feishu.stNotConfiguredDesc') }
  }
  if (!cfg.user_enabled) return { color: 'var(--border)', label: t('settings.feishu.stOff'), desc: '' }
  const MAP = {
    connected: { color: 'var(--green)', label: t('settings.feishu.stConnected') },
    connecting: { color: 'var(--yellow)', label: t('settings.feishu.stConnecting') },
    auth_failed: { color: 'var(--red)', label: t('settings.feishu.stAuthFailed') },
    error: { color: 'var(--red)', label: t('settings.feishu.stError') },
    conflict: { color: 'var(--yellow)', label: t('settings.feishu.stConflict') },
    disabled: { color: 'var(--yellow)', label: t('settings.feishu.stPendingConnector') },
  }
  const conn = cfg.connection || {}
  const v = MAP[conn.conn_status] || MAP.disabled
  const parts = []
  if (conn.last_error_code) parts.push(String(conn.last_error_code))
  if (conn.last_connected_at) parts.push(new Date(conn.last_connected_at).toLocaleTimeString())
  return { color: v.color, label: v.label, desc: parts.join(' · ') }
}

// Feishu bot — the user is the sole editor of their own app_id / app_secret; the
// secret is write-only (only app_secret_set comes back). admin_disabled is a hard
// gate the user cannot lift (rendered read-only when set).
function ChannelsTab() {
  const { t } = useTranslation()
  const [cfg, setCfg] = useState(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [appIdDraft, setAppIdDraft] = useState('')
  const [secretMode, setSecretMode] = useState(false)
  const [secretDraft, setSecretDraft] = useState('')
  const [showSecret, setShowSecret] = useState(false)
  const [pendingClear, setPendingClear] = useState(false)
  const [linkCode, setLinkCode] = useState(null)      // { code, expires_at } | null
  const [codeCopied, setCodeCopied] = useState(false)
  const [nowTick, setNowTick] = useState(() => Date.now())
  const [pendingUnbind, setPendingUnbind] = useState(false)
  const [sessions, setSessions] = useState([])        // per-chat bindings (session list)
  const [copiedSession, setCopiedSession] = useState(null)
  const initedRef = useRef(false)
  const mountedRef = useRef(true)

  const refresh = useCallback(async ({ spin = false } = {}) => {
    if (spin) setRefreshing(true)
    try {
      const data = await getFeishuConfig()
      if (!mountedRef.current) return
      setCfg(data); setError(null)
      if (!initedRef.current) { setAppIdDraft(data.app_id || ''); initedRef.current = true }
    } catch (e) {
      if (mountedRef.current) setError(e.message)  // fail-soft: keep last good cfg
    } finally {
      if (mountedRef.current) { setLoading(false); setRefreshing(false) }
    }
    // Session list rides the same cadence; fail-soft (keeps the last good list).
    try {
      const s = await getFeishuSessions()
      if (mountedRef.current) setSessions(s.sessions || [])
    } catch { /* ignore */ }
  }, [])

  useEffect(() => {
    mountedRef.current = true
    refresh()
    const id = setInterval(() => refresh(), 8000)
    return () => { mountedRef.current = false; clearInterval(id) }
  }, [refresh])

  const save = async (patch) => {
    setBusy(true)
    try {
      const data = await updateFeishuConfig(patch)
      if (mountedRef.current) { setCfg(data); setError(null); setAppIdDraft(data.app_id || '') }
      return true
    } catch (e) {
      if (mountedRef.current) setError(e.message)
      return false
    } finally {
      if (mountedRef.current) setBusy(false)
    }
  }

  const adminDisabled = !!cfg?.admin_disabled
  const appIdDirty = appIdDraft.trim() !== (cfg?.app_id || '')
  const status = feishuStatusView(cfg, t)

  // Owner link-code: 1s tick drives the expiry countdown; the 8s cfg poll flips
  // owner_bound once the user DMs the code, at which point the code block clears.
  useEffect(() => {
    if (!linkCode) return undefined
    const id = setInterval(() => setNowTick(Date.now()), 1000)
    return () => clearInterval(id)
  }, [linkCode])
  useEffect(() => {
    if (cfg?.owner_bound && linkCode) setLinkCode(null)
  }, [cfg?.owner_bound])  // eslint-disable-line react-hooks/exhaustive-deps
  const codeRemainMs = linkCode ? Date.parse(linkCode.expires_at) - nowTick : 0
  const codeRemain = codeRemainMs > 0
    ? `${String(Math.floor(codeRemainMs / 60000)).padStart(2, '0')}:${String(Math.floor((codeRemainMs % 60000) / 1000)).padStart(2, '0')}`
    : null

  const mintCode = async () => {
    setBusy(true)
    try {
      const data = await createFeishuLinkCode()
      if (mountedRef.current) { setLinkCode(data); setError(null) }
    } catch (e) {
      if (mountedRef.current) setError(e.message)
    } finally {
      if (mountedRef.current) setBusy(false)
    }
  }

  const doUnbind = async () => {
    setBusy(true)
    try {
      const data = await unbindFeishuOwner()
      if (mountedRef.current) { setCfg(data); setError(null); setPendingUnbind(false) }
    } catch (e) {
      if (mountedRef.current) setError(e.message)
    } finally {
      if (mountedRef.current) setBusy(false)
    }
  }

  const copyLinkCommand = () => {
    navigator.clipboard.writeText(`/link ${linkCode.code}`)
    setCodeCopied(true)
    setTimeout(() => setCodeCopied(false), 800)
  }

  const saveSecret = async () => {
    if (!secretDraft) return
    const ok = await save({ app_secret: secretDraft })
    if (ok && mountedRef.current) { setSecretMode(false); setSecretDraft(''); setShowSecret(false) }
  }

  if (loading) {
    return (
      <div className="flex flex-col gap-4">
        <div className="skeleton" style={{ height: 48, width: '100%', borderRadius: 4 }} />
        <div className="skeleton" style={{ height: 36, width: '60%', borderRadius: 4 }} />
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-5">
      <p className="text-xs" style={{ color: 'var(--text-secondary)', lineHeight: 1.5 }}>
        {t('settings.feishu.desc')}
      </p>

      {adminDisabled && (
        <div className="px-3 py-2 text-xs" style={{ borderLeft: '2px solid var(--red)', background: 'var(--bg-elevated)', borderRadius: 4, color: 'var(--red)' }}>
          {t('settings.feishu.adminDisabledBanner')}
        </div>
      )}

      {/* Connection status — 2px left border carries the status color */}
      <div className="flex items-center gap-2 px-3 py-2 overflow-hidden" style={{ background: 'var(--bg-surface)', borderRadius: 4, borderLeft: `2px solid ${status.color}` }}>
        <div className="flex-1 min-w-0">
          <div className="text-sm truncate" style={{ color: 'var(--text-primary)' }}>{status.label}</div>
          {status.desc && <div className="text-xs truncate" style={{ color: 'var(--text-dim)', fontFamily: FEISHU_MONO }}>{status.desc}</div>}
        </div>
        <button type="button" onClick={() => refresh({ spin: true })} title={t('settings.feishu.refresh')}
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }}>
          <RefreshCw size={14} strokeWidth={1.5} style={refreshing ? { animation: 'spin 1s linear infinite' } : undefined} />
        </button>
      </div>

      {/* Enabled (user's own toggle) */}
      <div className="flex items-center justify-between gap-3">
        <div className="flex flex-col gap-1">
          <label style={labelStyle}>{t('settings.feishu.enabled')}</label>
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.feishu.enabledHint')}</span>
        </div>
        <Toggle size="sm" checked={!!cfg?.user_enabled} disabled={busy || adminDisabled} onChange={(next) => save({ user_enabled: next })} />
      </div>

      {/* App ID */}
      <div>
        <label style={labelStyle}>{t('settings.feishu.appId')}</label>
        <div className="flex items-center gap-2">
          <input style={{ ...inputStyle, fontFamily: FEISHU_MONO, fontSize: 12 }} value={appIdDraft}
            onChange={(e) => setAppIdDraft(e.target.value)} placeholder="cli_..." />
          <button type="button" disabled={!appIdDirty || busy} onClick={() => save({ app_id: appIdDraft.trim() })}
            style={{ padding: '8px 12px', borderRadius: 4, fontSize: 13, flexShrink: 0, background: 'transparent',
              border: `1px solid ${appIdDirty ? 'var(--border-strong)' : 'var(--border)'}`,
              color: appIdDirty ? 'var(--text-primary)' : 'var(--text-dim)',
              cursor: (!appIdDirty || busy) ? 'not-allowed' : 'pointer' }}>
            {t('settings.feishu.save')}
          </button>
        </div>
      </div>

      {/* App Secret — write-only three states */}
      <div>
        <label style={labelStyle}>{t('settings.feishu.appSecret')}</label>
        {secretMode ? (
          <div className="flex flex-col gap-2">
            <div className="relative">
              <input type={showSecret ? 'text' : 'password'} autoFocus value={secretDraft}
                onChange={(e) => setSecretDraft(e.target.value)} placeholder={t('settings.feishu.secretPlaceholder')}
                style={{ ...inputStyle, fontFamily: FEISHU_MONO, fontSize: 12, paddingRight: 40 }} />
              <button type="button" onClick={() => setShowSecret(!showSecret)}
                style={{ position: 'absolute', right: 8, top: '50%', transform: 'translateY(-50%)', background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }}>
                {showSecret ? <EyeOff size={14} strokeWidth={1.5} /> : <Eye size={14} strokeWidth={1.5} />}
              </button>
            </div>
            <div className="flex items-center gap-2">
              <button type="button" disabled={!secretDraft || busy} onClick={saveSecret}
                style={{ padding: '6px 12px', borderRadius: 4, fontSize: 12, border: 'none', background: 'var(--blue)', color: 'var(--text-inverse)', cursor: (!secretDraft || busy) ? 'not-allowed' : 'pointer', opacity: (!secretDraft || busy) ? 0.5 : 1 }}>
                {t('settings.feishu.saveSecret')}
              </button>
              <button type="button" onClick={() => { setSecretMode(false); setSecretDraft(''); setShowSecret(false) }}
                style={{ padding: '6px 12px', borderRadius: 4, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                {t('confirm.cancel')}
              </button>
              <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.feishu.writeOnlyHint')}</span>
            </div>
          </div>
        ) : cfg?.app_secret_set ? (
          <div className="flex items-center gap-3">
            <span className="text-xs px-2 py-1" style={{ color: 'var(--green)', borderLeft: '2px solid var(--green)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
              {t('settings.feishu.secretConfigured')}
            </span>
            <button type="button" onClick={() => setSecretMode(true)} style={feishuSecBtn}>{t('settings.feishu.rotate')}</button>
            <button type="button" onClick={() => setPendingClear(true)} style={{ ...feishuSecBtn, borderColor: 'var(--red)', color: 'var(--red)' }}>{t('settings.feishu.clear')}</button>
          </div>
        ) : (
          <div className="flex items-center gap-3">
            <span className="text-xs px-2 py-1" style={{ color: 'var(--text-dim)', borderLeft: '2px solid var(--border)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
              {t('settings.feishu.secretNotSet')}
            </span>
            <button type="button" onClick={() => setSecretMode(true)} style={feishuSecBtn}>{t('settings.feishu.setSecret')}</button>
          </div>
        )}
        {pendingClear && (
          <div className="flex flex-col gap-2 px-3 py-2" style={{ marginTop: 8, borderLeft: '2px solid var(--red)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
            <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('settings.feishu.clearConfirm')}</span>
            <div className="flex gap-2">
              <button type="button" onClick={() => setPendingClear(false)}
                style={{ padding: '4px 12px', borderRadius: 4, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                {t('confirm.cancel')}
              </button>
              <button type="button" disabled={busy} onClick={async () => { const ok = await save({ app_secret: '__clear__' }); if (ok && mountedRef.current) setPendingClear(false) }}
                style={{ padding: '4px 12px', borderRadius: 4, fontSize: 12, border: 'none', background: 'var(--red)', color: 'var(--text-inverse)', cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>
                {t('settings.feishu.clear')}
              </button>
            </div>
          </div>
        )}
      </div>

      {/* Domain */}
      <div>
        <label style={labelStyle}>{t('settings.feishu.domain')}</label>
        <Dropdown size="sm" value={cfg?.domain || 'feishu'} onChange={(v) => save({ domain: v })}
          options={[{ value: 'feishu', label: 'Feishu (飞书)' }, { value: 'lark', label: 'Lark' }]} />
      </div>

      {/* Owner binding (link-code) */}
      <div>
        <label style={labelStyle}>{t('settings.feishu.owner')}</label>
        {cfg?.owner_bound ? (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-xs px-2 py-1" style={{ color: 'var(--green)', borderLeft: '2px solid var(--green)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
                {t('settings.feishu.ownerBound')}
              </span>
              <span className="text-xs" style={{ color: 'var(--text-secondary)', fontFamily: FEISHU_MONO }}>
                {cfg.owner_union_id_masked}
              </span>
              {cfg.owner_bound_at && (
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>
                  {new Date(cfg.owner_bound_at).toLocaleString()}
                </span>
              )}
              <button type="button" onClick={() => setPendingUnbind(true)}
                style={{ ...feishuSecBtn, borderColor: 'var(--red)', color: 'var(--red)' }}>
                {t('settings.feishu.unbind')}
              </button>
            </div>
            <div className="flex items-center gap-3">
              <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.feishu.accessMode')}</span>
              <Dropdown size="sm" value={cfg?.single_chat_access_mode || 'owner_only'}
                onChange={(v) => save({ single_chat_access_mode: v })}
                options={[
                  { value: 'owner_only', label: t('settings.feishu.modeOwnerOnly') },
                  { value: 'all', label: t('settings.feishu.modeAll') },
                ]} />
            </div>
            {pendingUnbind && (
              <div className="flex flex-col gap-2 px-3 py-2" style={{ borderLeft: '2px solid var(--red)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
                <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>{t('settings.feishu.unbindConfirm')}</span>
                <div className="flex gap-2">
                  <button type="button" onClick={() => setPendingUnbind(false)}
                    style={{ padding: '4px 12px', borderRadius: 4, fontSize: 12, background: 'transparent', border: '1px solid var(--border)', color: 'var(--text-secondary)', cursor: 'pointer' }}>
                    {t('confirm.cancel')}
                  </button>
                  <button type="button" disabled={busy} onClick={doUnbind}
                    style={{ padding: '4px 12px', borderRadius: 4, fontSize: 12, border: 'none', background: 'var(--red)', color: 'var(--text-inverse)', cursor: busy ? 'not-allowed' : 'pointer', opacity: busy ? 0.5 : 1 }}>
                    {t('settings.feishu.unbind')}
                  </button>
                </div>
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex items-center gap-3">
              <span className="text-xs px-2 py-1" style={{ color: 'var(--text-dim)', borderLeft: '2px solid var(--border)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
                {t('settings.feishu.ownerUnbound')}
              </span>
              <button type="button" disabled={busy} onClick={mintCode} style={feishuSecBtn}>
                {t('settings.feishu.genCode')}
              </button>
            </div>
            <span className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
              {t('settings.feishu.ownerHint')}
            </span>
            {linkCode && (
              <div className="flex flex-col gap-1 px-3 py-2" style={{ borderLeft: '2px solid var(--cyan)', background: 'var(--bg-elevated)', borderRadius: 4 }}>
                <div className="flex items-center gap-3">
                  <span className="text-sm" style={{ color: 'var(--text-primary)', fontFamily: FEISHU_MONO }}>
                    /link {linkCode.code}
                  </span>
                  <button type="button" onClick={copyLinkCommand}
                    style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 2, color: codeCopied ? 'var(--green)' : 'var(--text-dim)', transition: 'color 150ms ease' }}>
                    {codeCopied ? <Check size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}
                  </button>
                  <span className="text-xs" style={{ color: codeRemain ? 'var(--text-dim)' : 'var(--red)' }}>
                    {codeRemain ? t('settings.feishu.codeExpires', { time: codeRemain }) : t('settings.feishu.codeExpired')}
                  </span>
                </div>
                <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.feishu.codeHint')}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Group-chat participation (user opt-in; admin holds a platform-wide kill switch) */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-3">
          <div className="flex flex-col gap-1">
            <label style={labelStyle}>{t('settings.feishu.groupChat')}</label>
            <span className="text-xs" style={{ color: 'var(--text-dim)', lineHeight: 1.5 }}>
              {t('settings.feishu.groupChatHint')}
            </span>
          </div>
          <Toggle size="sm" checked={!!cfg?.group_chat_enabled}
            disabled={busy || adminDisabled || !!cfg?.group_chat_globally_disabled}
            onChange={(next) => save({ group_chat_enabled: next })} />
        </div>
        {cfg?.group_chat_globally_disabled && (
          <div className="px-3 py-2 text-xs" style={{ borderLeft: '2px solid var(--yellow)', background: 'var(--bg-elevated)', borderRadius: 4, color: 'var(--yellow)' }}>
            {t('settings.feishu.groupChatGloballyDisabled')}
          </div>
        )}
      </div>

      {/* Session list — every chat the bot has been talked to in (per-chat bindings).
          Reset chats (/new) are greyed; names are stamped by the connector. */}
      <div className="flex flex-col gap-2">
        <label style={labelStyle}>{t('settings.feishu.sessions')}</label>
        {sessions.length === 0 ? (
          <span className="text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.feishu.sessionsEmpty')}</span>
        ) : (
          <div className="flex flex-col" style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4 }}>
            {sessions.map((s, i) => {
              const active = !!s.session_id
              const isGroup = s.chat_type === 'group'
              const label = s.chat_name || (s.chat_id ? `${s.chat_id.slice(0, 10)}…` : '—')
              return (
                <div key={s.chat_id || i} className="flex items-center gap-3 px-3 py-2 min-w-0"
                  style={{ borderTop: i === 0 ? 'none' : '1px solid var(--border-subtle)', opacity: active ? 1 : 0.55 }}>
                  <span className="text-xs px-2 py-1 flex-shrink-0 whitespace-nowrap" style={{
                    borderLeft: `2px solid ${isGroup ? 'var(--purple)' : 'var(--cyan)'}`,
                    background: 'var(--bg-elevated)', borderRadius: 4,
                    color: isGroup ? 'var(--purple)' : 'var(--cyan)',
                  }}>
                    {isGroup ? t('settings.feishu.typeGroup') : t('settings.feishu.typeP2p')}
                  </span>
                  <span className="text-sm truncate flex-1 min-w-0" style={{ color: 'var(--text-primary)' }} title={s.chat_id}>
                    {label}
                  </span>
                  {active ? (
                    <span className="flex items-center gap-1 flex-shrink-0 copyable">
                      <span className="text-xs" style={{ color: 'var(--text-secondary)', fontFamily: FEISHU_MONO }} title={s.session_id}>
                        {s.session_id.slice(0, 8)}…{s.session_id.slice(-4)}
                      </span>
                      <button type="button" onClick={() => {
                        navigator.clipboard.writeText(s.session_id)
                        setCopiedSession(s.chat_id)
                        setTimeout(() => setCopiedSession(null), 800)
                      }} style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 2,
                        color: copiedSession === s.chat_id ? 'var(--green)' : 'var(--text-dim)', transition: 'color 150ms ease' }}>
                        {copiedSession === s.chat_id ? <Check size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}
                      </button>
                    </span>
                  ) : (
                    <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
                      {t('settings.feishu.sessionReset')}
                    </span>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </div>

      {error && <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>}
    </div>
  )
}

/**
 * SettingsPanel renders the tab content only when given an `activeTabOverride` prop.
 * Used by SettingsOverlay to render the appropriate settings tab.
 */
export default function SettingsPanel({ activeTabOverride }) {
  const activeTab = activeTabOverride || 'api'

  return (
    <>
      {activeTab === 'account' && <AccountTab />}
      {activeTab === 'api' && <ApiKeyTab />}
      {activeTab === 'models' && <ModelsTab />}
      {activeTab === 'channels' && <ChannelsTab />}
      {activeTab === 'quickactions' && <QuickActionsTab />}
      {activeTab === 'advanced' && <AdvancedTab />}
      {activeTab === 'archived' && <ArchivedTab />}
    </>
  )
}
