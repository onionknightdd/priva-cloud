import { useState, useEffect, useRef } from 'react'
import { X, Key, ShieldOff, Ban, Power, Trash2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import useOverlayTransition from '@shared/motion/useOverlayTransition'
import useAdminStore from '../../stores/adminStore'
import useAuthStore from '@shared/stores/authStore'
import useUiStore from '@shared/stores/uiStore'
import * as adminApi from '@shared/api/admin'
import CopyButton from '@shared/components/shared/CopyButton'
import Dropdown from '@shared/components/shared/Dropdown'
import FeishuConfigSection from './FeishuConfigSection'

// Purge-dialog framing of a fleet entry. FleetView calls an awake runner "online"
// in neutral green; here the same fact is what the operator is about to kill, so
// the palette escalates instead of reassuring.
function purgeRunnerState(acct) {
  if (!acct) return { labelKey: 'admin.stateIdle', color: 'var(--text-dim)' }
  if (acct.phase === 'Waking') return { labelKey: 'admin.stateWaking', color: 'var(--yellow)' }
  if (acct.awake) {
    if ((acct.active_runs || 0) > 0) return { labelKey: 'admin.stateRunning', color: 'var(--red)' }
    return { labelKey: 'admin.stateOnline', color: 'var(--yellow)' }
  }
  return { labelKey: 'admin.stateIdle', color: 'var(--text-dim)' }
}

// Live facts to put in front of the operator before freezing or destroying an
// account. Without a fleet snapshot the runner state is unknown, not idle — the
// two must not look alike in a dialog that force-kills whatever is running.
function AccountStateLines({ acct, hasFleet, feishu }) {
  const { t } = useTranslation()
  const state = purgeRunnerState(acct)
  const runs = acct?.active_runs
  return (
    <div className="flex flex-col gap-1">
      <span className="text-xs uppercase" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
        {t('admin.currentState')}
      </span>
      <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
        Runner: {hasFleet ? (
          <>
            <span style={{ color: state.color }}>{t(state.labelKey)}</span>
            {acct?.awake && (runs == null
              ? ` · ${t('admin.runsUnknown')}`
              : runs > 0 ? ` · ${t('admin.activeRuns', { count: runs })}` : '')}
          </>
        ) : (
          <span style={{ color: 'var(--text-dim)' }}>{t('admin.stateUnknown')}</span>
        )}
      </span>
      {feishu && (
        <span className="text-xs" style={{ color: 'var(--text-secondary)' }}>
          {t('admin.feishu.title')}: {feishu.app_id && feishu.app_secret_set
            ? t('admin.feishuBound')
            : t('admin.feishuUnbound')}
        </span>
      )}
    </div>
  )
}

function DangerRow({ title, desc, actionLabel, icon, color, locked, onClick }) {
  return (
    <div className="flex items-center gap-3 px-3 py-3">
      <div className="flex flex-col gap-1 flex-1 min-w-0">
        <span className="text-sm font-semibold" style={{ color: 'var(--text-primary)', wordBreak: 'break-word' }}>
          {title}
        </span>
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)', wordBreak: 'break-word' }}>
          {desc}
        </span>
      </div>
      <button
        type="button"
        className="flex items-center gap-1 px-3 py-1 text-xs flex-shrink-0"
        style={{
          background: 'transparent',
          border: `1px solid ${color}`,
          borderRadius: 4,
          color,
          cursor: locked ? 'not-allowed' : 'pointer',
          opacity: locked ? 0.4 : 1,
          transition: 'opacity 150ms ease',
        }}
        disabled={locked}
        onClick={onClick}
        onMouseEnter={(e) => { if (!locked) e.currentTarget.style.opacity = '0.8' }}
        onMouseLeave={(e) => { if (!locked) e.currentTarget.style.opacity = '1' }}
      >
        {icon}
        {actionLabel}
      </button>
    </div>
  )
}

// Disable (reversible) and purge (not). Both dialogs are built at click time, so
// the fleet snapshot and the Feishu binding have to be in hand before then.
function DangerZone({ user, isSelf, onError }) {
  const { t } = useTranslation()
  const refreshUsers = useAdminStore((s) => s.refreshUsers)
  const closeUserDrawer = useAdminStore((s) => s.closeUserDrawer)
  const fleet = useAdminStore((s) => s.fleet)
  const fetchFleet = useAdminStore((s) => s.fetchFleet)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const [feishu, setFeishu] = useState(null)

  const username = user.username
  const status = user.status || 'active'
  const disabled = status === 'disabled'
  const purging = status === 'purged'

  useEffect(() => { fetchFleet() }, [username, fetchFleet])

  useEffect(() => {
    let cancelled = false
    setFeishu(null)
    adminApi.getUserFeishuConfig(username)
      .then((cfg) => { if (!cancelled) setFeishu(cfg) })
      .catch(() => {})  // fail-soft: the dialog just leaves the Feishu line out
    return () => { cancelled = true }
  }, [username])

  // Fleet entries are keyed by account_id; username is the only join key the
  // admin user objects carry, and it is nullable on the fleet side.
  const acct = fleet?.accounts?.find((a) => a.username === username)
  const locked = isSelf || purging
  const lockReason = isSelf ? t('admin.dangerSelfHint') : purging ? t('admin.dangerPurgingHint') : null

  const handleToggle = () => {
    showConfirmDialog({
      title: disabled
        ? t('admin.enableUserTitle', { name: username })
        : t('admin.disableUserTitle', { name: username }),
      message: disabled ? t('admin.enableUserMessage') : (
        <div className="flex flex-col gap-2" style={{ wordBreak: 'break-word' }}>
          <span>{t('admin.disableUserMessage')}</span>
          <AccountStateLines acct={acct} hasFleet={!!fleet} feishu={feishu} />
        </div>
      ),
      confirmLabel: disabled ? t('admin.enable') : t('admin.disable'),
      danger: !disabled,
      onConfirm: async () => {
        try {
          if (disabled) await adminApi.enableUser(username)
          else await adminApi.disableUser(username)
          await refreshUsers()
        } catch (e) {
          onError(e.message)
        }
      },
    })
  }

  const handlePurge = () => {
    showConfirmDialog({
      title: t('admin.deleteUserTitle', { name: username }),
      message: (
        <div className="flex flex-col gap-2" style={{ wordBreak: 'break-word' }}>
          <span>{t('admin.deleteUserMessage')}</span>
          <AccountStateLines acct={acct} hasFleet={!!fleet} feishu={feishu} />
          <div className="flex flex-col gap-1">
            <span className="text-xs uppercase" style={{ color: 'var(--red)', letterSpacing: '0.06em' }}>
              {t('admin.purgeDestroys')}
            </span>
            {[
              t('admin.purgeDestroyRuns'),
              t('admin.purgeDestroyData'),
              t('admin.purgeDestroyRuntime'),
            ].map((line) => (
              <span key={line} className="text-xs" style={{ color: 'var(--text-secondary)' }}>
                · {line}
              </span>
            ))}
          </div>
        </div>
      ),
      confirmLabel: t('admin.purgeConfirm'),
      requireText: username,
      danger: true,
      onConfirm: async () => {
        try {
          await adminApi.deleteUser(username)
          closeUserDrawer()
          await refreshUsers()
        } catch (e) {
          // The row is already tombstoned when teardown fails to start (502), so
          // re-list anyway: the table must show PURGING, never a live ACTIVE row.
          onError(e.message)
          await refreshUsers()
        }
      },
    })
  }

  return (
    <div className="flex flex-col gap-2">
      <label
        className="text-xs uppercase"
        style={{ color: 'var(--red)', letterSpacing: '0.06em', fontWeight: 600 }}
      >
        {t('admin.dangerZone')}
      </label>
      <div className="flex flex-col" style={{ border: '1px solid var(--red)', borderRadius: 4 }}>
        <DangerRow
          title={disabled ? t('admin.enableAccount') : t('admin.disableAccount')}
          desc={disabled ? t('admin.enableAccountDesc') : t('admin.disableAccountDesc')}
          actionLabel={disabled ? t('admin.enable') : t('admin.disable')}
          icon={disabled ? <Power size={12} strokeWidth={1.5} /> : <Ban size={12} strokeWidth={1.5} />}
          color={disabled ? 'var(--green)' : 'var(--red)'}
          locked={locked}
          onClick={handleToggle}
        />
        <div style={{ borderTop: '1px solid var(--border)' }}>
          <DangerRow
            title={t('admin.purgeAccount')}
            desc={t('admin.purgeAccountDesc')}
            actionLabel={t('admin.purgeAction')}
            icon={<Trash2 size={12} strokeWidth={1.5} />}
            color="var(--red)"
            locked={locked}
            onClick={handlePurge}
          />
        </div>
      </div>
      {lockReason && (
        <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
          &gt; {lockReason}
        </span>
      )}
    </div>
  )
}

export default function UserEditDrawer() {
  const { t } = useTranslation()
  const selectedUser = useAdminStore((s) => s.selectedUser)
  const closeUserDrawer = useAdminStore((s) => s.closeUserDrawer)
  const users = useAdminStore((s) => s.users)
  const fetchUsers = useAdminStore((s) => s.fetchUsers)
  const drawerWidth = useAdminStore((s) => s.drawerWidth)
  const setDrawerWidth = useAdminStore((s) => s.setDrawerWidth)
  const authUser = useAuthStore((s) => s.user)

  const { dragging, onMouseDown } = useResizable({
    initial: drawerWidth,
    min: 320,
    max: Math.round(window.innerWidth * 0.6),
    direction: 'left',
    onResize: setDrawerWidth,
  })

  const liveUser = users.find((u) => u.username === selectedUser)
  // Exit animation: closeUserDrawer() clears selectedUser immediately, so keep
  // rendering a snapshot of the last user while the drawer slides out.
  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open: !!liveUser, variant: 'drawer' })
  const shownUserRef = useRef(null)
  if (liveUser) shownUserRef.current = liveUser
  const user = liveUser ?? shownUserRef.current

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

  // Reset the form on every open (selectedUser flips null → username), which
  // matches the pre-snapshot behavior where `user` went undefined on close.
  useEffect(() => {
    if (liveUser) {
      setRole(liveUser.role)
      setPassword('')
      setRunnerType(liveUser.agent_runner_type || 'auto_scale')
      setCpuCores(String(liveUser.cpu_cores ?? 1))
      setMemoryMb(String(liveUser.memory_mb ?? 2048))
      setVolumeGb(String(liveUser.volume_gb ?? 1))
      setError(null)
    }
  }, [selectedUser]) // eslint-disable-line react-hooks/exhaustive-deps

  if (!mounted || !user) return null

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
        ref={backdropRef}
        className="fixed inset-0"
        style={{
          background: 'var(--bg-overlay)',
          backdropFilter: 'blur(4px)',
          zIndex: 200,
          pointerEvents: liveUser ? 'auto' : 'none',
        }}
        onClick={closeUserDrawer}
      />

      {/* Drawer */}
      <div
        ref={panelRef}
        className="fixed top-0 right-0 bottom-0 flex flex-col"
        style={{
          width: drawerWidth,
          background: 'var(--bg-surface)',
          borderLeft: '1px solid var(--border)',
          zIndex: 201,
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

          {/* Feishu bot — status + kill-switch (admin never edits credentials) */}
          <FeishuConfigSection username={user.username} />

          {/* Timestamps */}
          <div className="flex flex-col gap-1" style={{ marginTop: 'auto' }}>
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              {t('admin.createdAt')}: {formatDate(user.created_at)}
            </span>
            <span className="text-xs font-light" style={{ color: 'var(--text-dim)' }}>
              {t('admin.updatedAt')}: {formatDate(user.updated_at)}
            </span>
          </div>

          {/* Account lifecycle — disable (reversible) and purge (not) */}
          <DangerZone
            user={user}
            isSelf={user.username === authUser?.username}
            onError={setError}
          />

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
      </div>
    </>
  )
}
