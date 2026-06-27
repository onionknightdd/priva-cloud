import { useState, useEffect, useRef } from 'react'
import { X, Key, ShieldOff } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import useAdminStore from '../../stores/adminStore'
import * as adminApi from '@shared/api/admin'
import CopyButton from '@shared/components/shared/CopyButton'
import Dropdown from '@shared/components/shared/Dropdown'

export default function UserEditDrawer() {
  const { t } = useTranslation()
  const selectedUser = useAdminStore((s) => s.selectedUser)
  const closeUserDrawer = useAdminStore((s) => s.closeUserDrawer)
  const users = useAdminStore((s) => s.users)
  const fetchUsers = useAdminStore((s) => s.fetchUsers)
  const drawerWidth = useAdminStore((s) => s.drawerWidth)
  const setDrawerWidth = useAdminStore((s) => s.setDrawerWidth)

  const { dragging, onMouseDown } = useResizable({
    initial: drawerWidth,
    min: 320,
    max: Math.round(window.innerWidth * 0.6),
    direction: 'left',
    onResize: setDrawerWidth,
  })

  const user = users.find((u) => u.username === selectedUser)

  const [role, setRole] = useState(user?.role || 'user')
  const [password, setPassword] = useState('')
  const [runnerType, setRunnerType] = useState(user?.agent_runner_type || 'auto_scale')
  const [cpuCores, setCpuCores] = useState(String(user?.cpu_cores ?? 1))
  const [memoryMb, setMemoryMb] = useState(String(user?.memory_mb ?? 2048))
  const [volumeGb, setVolumeGb] = useState(String(user?.volume_gb ?? 1))
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState(null)
  // Guards setState after the drawer unmounts mid-request.
  const mountedRef = useRef(true)
  useEffect(() => () => { mountedRef.current = false }, [])

  useEffect(() => {
    if (user) {
      setRole(user.role)
      setPassword('')
      setRunnerType(user.agent_runner_type || 'auto_scale')
      setCpuCores(String(user.cpu_cores ?? 1))
      setMemoryMb(String(user.memory_mb ?? 2048))
      setVolumeGb(String(user.volume_gb ?? 1))
      setError(null)
    }
  }, [user?.username])

  if (!user) return null

  const handleSave = async () => {
    setSaving(true)
    setError(null)
    try {
      const data = {}
      if (role !== user.role) data.role = role
      if (password) data.password = password
      data.agent_runner_type = runnerType
      data.cpu_cores = Number(cpuCores)
      data.memory_mb = Number(memoryMb)
      data.volume_gb = Number(volumeGb)
      await adminApi.updateUser(user.username, data)
      await fetchUsers()
      closeUserDrawer()
    } catch (e) {
      if (mountedRef.current) setError(e.message)
    } finally {
      if (mountedRef.current) setSaving(false)
    }
  }

  const handleGenerateKey = async () => {
    try {
      await adminApi.updateUser(user.username, { api_key: '__generate__' })
      await fetchUsers()
    } catch (e) {
      setError(e.message)
    }
  }

  const handleRevokeKey = async () => {
    try {
      await adminApi.updateUser(user.username, { api_key: '__revoke__' })
      await fetchUsers()
    } catch (e) {
      setError(e.message)
    }
  }

  const formatDate = (dateStr) => {
    if (!dateStr) return '—'
    return new Date(dateStr).toLocaleString()
  }

  const inputStyle = {
    background: 'var(--bg-elevated)',
    border: '1px solid var(--border)',
    borderRadius: '4px',
    color: 'var(--text-primary)',
    outline: 'none',
    width: '100%',
  }

  return (
    <>
      {/* Overlay */}
      <div
        className="fixed inset-0"
        style={{
          background: 'var(--bg-overlay)',
          backdropFilter: 'blur(4px)',
          zIndex: 200,
        }}
        onClick={closeUserDrawer}
      />

      {/* Drawer */}
      <div
        className="fixed top-0 right-0 bottom-0 flex flex-col"
        style={{
          width: drawerWidth,
          background: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border)',
          zIndex: 201,
          animation: dragging ? 'none' : 'slide-in-right 220ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        {/* Resize handle — left edge */}
        <div
          onMouseDown={onMouseDown}
          style={{
            position: 'absolute',
            left: 0,
            top: 0,
            bottom: 0,
            width: 4,
            cursor: 'col-resize',
            background: dragging ? 'var(--blue)' : 'transparent',
            transition: 'background 100ms ease',
            zIndex: 10,
          }}
          onMouseEnter={(e) => {
            if (!dragging) e.currentTarget.style.background = 'var(--blue)'
          }}
          onMouseLeave={(e) => {
            if (!dragging) e.currentTarget.style.background = 'transparent'
          }}
        />
        {/* Header */}
        <div
          className="flex items-center justify-between px-5 py-4"
          style={{ borderBottom: '1px solid var(--border)' }}
        >
          <span className="font-semibold text-md" style={{ color: 'var(--text-primary)' }}>
            {t('admin.editUser')}
          </span>
          <button
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', padding: 4, color: 'var(--text-dim)', transition: 'color 150ms ease' }}
            onClick={closeUserDrawer}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-5 py-4 flex flex-col gap-5">
          {/* Username (read-only) */}
          <div className="flex flex-col gap-1">
            <label className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
              {t('admin.username')}
            </label>
            <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)' }}>
              {user.username}
            </span>
          </div>

          {/* Role */}
          <div className="flex flex-col gap-1">
            <label className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
              {t('admin.role')}
            </label>
            <Dropdown
              size="sm"
              value={role}
              onChange={setRole}
              options={[
                { value: 'user', label: 'User' },
                { value: 'admin', label: 'Admin' },
              ]}
            />
          </div>

          {/* Runner Type */}
          <div className="flex flex-col gap-2">
            <label className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
              {t('admin.runnerType')}
            </label>
            <div className="flex gap-2">
              {[
                { value: 'auto_scale', label: t('admin.runnerAutoScale') },
                { value: 'persistent', label: t('admin.runnerPersistent') },
              ].map(({ value, label }) => {
                const selected = runnerType === value
                return (
                  <button
                    key={value}
                    type="button"
                    className="flex items-center gap-2 px-3 py-2 flex-1 min-w-0"
                    style={{
                      background: selected ? 'var(--bg-elevated)' : 'transparent',
                      border: '1px solid var(--border)',
                      borderLeft: `2px solid ${selected ? 'var(--blue)' : 'var(--border)'}`,
                      borderRadius: 4,
                      cursor: 'pointer',
                      transition: 'background 150ms ease, border-color 150ms ease',
                    }}
                    onClick={() => setRunnerType(value)}
                  >
                    <span
                      className="flex items-center justify-center flex-shrink-0"
                      style={{
                        width: 14,
                        height: 14,
                        borderRadius: '50%',
                        border: `1px solid ${selected ? 'var(--blue)' : 'var(--border-strong)'}`,
                      }}
                    >
                      {selected && (
                        <span style={{ width: 7, height: 7, borderRadius: '50%', background: 'var(--blue)' }} />
                      )}
                    </span>
                    <span
                      className="text-sm truncate"
                      style={{
                        color: 'var(--text-primary)',
                        fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                      }}
                    >
                      {label}
                    </span>
                  </button>
                )
              })}
            </div>
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              &gt; {runnerType === 'persistent' ? t('admin.runnerPersistentHint') : t('admin.runnerAutoScaleHint')}
            </span>
          </div>

          {/* Resource Spec */}
          <div className="flex flex-col gap-2">
            <label className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
              {t('admin.resourceSpec')}
            </label>
            {[
              { label: t('admin.cpu'), value: cpuCores, setter: setCpuCores, unit: t('admin.cpuUnit'), min: 0.1, step: 0.1 },
              { label: t('admin.memory'), value: memoryMb, setter: setMemoryMb, unit: t('admin.memoryUnit'), min: 256, step: 256 },
              { label: t('admin.volume'), value: volumeGb, setter: setVolumeGb, unit: t('admin.volumeUnit'), min: 1, step: 1 },
            ].map(({ label, value, setter, unit, min, step }) => (
              <div key={label} className="flex items-center gap-3">
                <span
                  className="text-xs uppercase flex-shrink-0"
                  style={{ color: 'var(--text-secondary)', letterSpacing: '0.06em', width: 60 }}
                >
                  {label}
                </span>
                <input
                  className="px-2 py-1 text-sm"
                  type="number"
                  min={min}
                  step={step}
                  style={{
                    ...inputStyle,
                    width: 100,
                    fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  }}
                  value={value}
                  onChange={(e) => setter(e.target.value)}
                />
                <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
                  {unit}
                </span>
              </div>
            ))}
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              &gt; {t('admin.saveRestartsPod')}
            </span>
          </div>

          {/* Reset Password */}
          <div className="flex flex-col gap-1">
            <label
              className="text-xs uppercase"
              style={{ color: 'var(--text-dim)', letterSpacing: '0.06em', fontWeight: 600 }}
            >
              {t('admin.resetPassword')}
            </label>
            <input
              className="px-2 py-1 text-sm"
              type="password"
              style={inputStyle}
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder={t('admin.passwordKeepHint')}
            />
          </div>

          {/* API Key */}
          <div className="flex flex-col gap-2">
            <label className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
              {t('admin.apiKey')}
            </label>
            {user.api_key ? (
              <div className="flex flex-col gap-2">
                <div
                  className="relative flex items-center px-2 py-2 text-xs"
                  style={{
                    background: 'var(--bg-elevated)',
                    borderRadius: '4px',
                    fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                    color: 'var(--text-secondary)',
                    wordBreak: 'break-all',
                    paddingRight: 32,
                  }}
                >
                  {user.api_key}
                  <CopyButton content={user.api_key} />
                </div>
                <button
                  className="flex items-center gap-1 px-3 py-1 text-xs"
                  style={{
                    background: 'transparent',
                    border: '1px solid var(--red)',
                    borderRadius: '4px',
                    color: 'var(--red)',
                    cursor: 'pointer',
                    alignSelf: 'flex-start',
                    transition: 'opacity 150ms ease',
                  }}
                  onClick={handleRevokeKey}
                  onMouseEnter={(e) => { e.currentTarget.style.opacity = '0.8' }}
                  onMouseLeave={(e) => { e.currentTarget.style.opacity = '1' }}
                >
                  <ShieldOff size={12} strokeWidth={1.5} />
                  {t('admin.revokeKey')}
                </button>
              </div>
            ) : (
              <button
                className="flex items-center gap-1 px-3 py-1 text-xs"
                style={{
                  background: 'transparent',
                  border: '1px solid var(--border)',
                  borderRadius: '4px',
                  color: 'var(--text-secondary)',
                  cursor: 'pointer',
                  alignSelf: 'flex-start',
                  transition: 'border-color 150ms ease',
                }}
                onClick={handleGenerateKey}
                onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
                onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
              >
                <Key size={12} strokeWidth={1.5} />
                {t('admin.generateKey')}
              </button>
            )}
          </div>

          {/* Timestamps */}
          <div className="flex flex-col gap-1" style={{ marginTop: 'auto' }}>
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              {t('admin.createdAt')}: {formatDate(user.created_at)}
            </span>
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              {t('admin.updatedAt')}: {formatDate(user.updated_at)}
            </span>
          </div>

          {error && (
            <div className="text-xs" style={{ color: 'var(--red)' }}>{error}</div>
          )}
        </div>

        {/* Footer */}
        <div
          className="flex justify-end gap-2 px-5 py-3"
          style={{ borderTop: '1px solid var(--border)' }}
        >
          <button
            className="px-3 py-1 text-sm"
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: '4px',
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              transition: 'border-color 150ms ease',
            }}
            onClick={closeUserDrawer}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            {t('confirm.cancel')}
          </button>
          <button
            className="px-3 py-1 text-sm"
            style={{
              background: 'var(--blue)',
              border: 'none',
              borderRadius: '4px',
              color: 'var(--text-inverse)',
              cursor: saving ? 'not-allowed' : 'pointer',
              opacity: saving ? 0.4 : 1,
              transition: 'opacity 150ms ease',
            }}
            disabled={saving}
            onClick={handleSave}
          >
            {t('admin.saveChanges')}
          </button>
        </div>

        <style>{`
          @keyframes slide-in-right {
            from { transform: translateX(100%); }
            to { transform: translateX(0); }
          }
        `}</style>
      </div>
    </>
  )
}
