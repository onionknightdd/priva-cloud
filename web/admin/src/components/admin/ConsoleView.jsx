import { useCallback, useEffect, useMemo, useState } from 'react'
import { SquareTerminal, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import Dropdown from '@shared/components/shared/Dropdown'
import TerminalSession from '@shared/components/terminal/TerminalSession'
import useAdminStore from '../../stores/adminStore'

// Admin console: open a live shell into either
//   • an AGENT-RUNNER pod (a selected account) — WS rides /api/pty/ws and the EPP
//     steers it to that account's pod (targetUsername), or
//   • a CONTROL-PLANE pod (control-panel / operator / data-spine) — WS rides
//     /api/admin/console/ws and the control-panel k8s-execs into the target pod.
// See extproc.py + routers/console.py (both audit-log admin.console_open).

// Static control-plane targets (resolved server-side by `app` label).
const CONTROL_PLANE = ['control-panel', 'operator', 'data-spine']
const CP_PATH = '/api/admin/console/ws'
const AR_PATH = '/api/pty/ws'

function StatusLabel({ meta, ready, closed }) {
  const { t } = useTranslation()
  if (!meta) return null
  const [text, color] = closed
    ? [t('admin.consoleDisconnected'), 'var(--red)']
    : ready
      ? [t('admin.live'), 'var(--green)']
      : [t('admin.consoleConnecting'), 'var(--yellow)']
  return (
    <div className="flex items-center gap-2 flex-shrink-0">
      <span className="uppercase text-xs" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em' }}>
        {meta.kind === 'cp' ? t('admin.scopeControlPlane') : t('admin.agentRunner')}
      </span>
      <span className="uppercase text-xs" style={{ color, fontWeight: 600, letterSpacing: '0.06em' }}>
        {text}
      </span>
    </div>
  )
}

export default function ConsoleView() {
  const { t } = useTranslation()
  const users = useAdminStore((s) => s.users)
  const usersLoading = useAdminStore((s) => s.usersLoading)
  const fetchUsers = useAdminStore((s) => s.fetchUsers)

  const [selected, setSelected] = useState(null)   // option value, e.g. "cp:operator" / "acct:alice"
  const [ready, setReady] = useState(false)
  const [, setCwd] = useState('')
  const [closed, setClosed] = useState(false)

  useEffect(() => { fetchUsers() }, [fetchUsers])

  // value -> { kind: 'cp'|'acct', target, wsPath } for the selectable options.
  const targetMeta = useMemo(() => {
    const m = {}
    for (const t of CONTROL_PLANE) m[`cp:${t}`] = { kind: 'cp', target: t, wsPath: CP_PATH }
    for (const u of users) m[`acct:${u.username}`] = { kind: 'acct', target: u.username, wsPath: AR_PATH }
    return m
  }, [users])

  const options = useMemo(() => {
    const accounts = [...users]
      .map((u) => ({ value: `acct:${u.username}`, label: u.role === 'admin' ? `${u.username} · admin` : u.username }))
      .sort((a, b) => a.label.localeCompare(b.label))
    return [
      { value: '__hdr_cp', label: t('admin.consoleControlPlane'), disabled: true },
      ...CONTROL_PLANE.map((target) => ({ value: `cp:${target}`, label: target })),
      { value: '__hdr_ar', label: t('admin.consoleAgentRunners'), disabled: true },
      ...accounts,
    ]
  }, [t, users])

  const meta = selected ? targetMeta[selected] : null

  const handleSelect = useCallback((value) => {
    setSelected(value)
    setReady(false)
    setCwd('')
    setClosed(false)
  }, [])

  // TerminalSession captures these once per mount; we remount via key={selected}.
  // A live session reports ready:true — clear any stale `closed` from a prior
  // teardown (e.g. StrictMode's mount→unmount→remount fires onClosed for the
  // discarded first connection before the real one becomes ready).
  const onMetaChange = useCallback((mc) => {
    if (mc?.ready === true) { setReady(true); setClosed(false) }
    else if (mc?.ready === false) setReady(false)
    if (mc?.cwd != null) setCwd(mc.cwd)
  }, [])
  const onClosed = useCallback(() => { setReady(false); setClosed(true) }, [])

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      <style>{`@keyframes cv-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
      {/* Header */}
      <div className="flex items-center justify-between gap-4 flex-shrink-0" style={{ padding: '20px 24px 0 24px' }}>
        <div style={{ minWidth: 0 }}>
          <h2 className="font-semibold text-lg flex items-center gap-2" style={{ color: 'var(--text-primary)', margin: 0 }}>
            <SquareTerminal size={18} strokeWidth={1.5} className="flex-shrink-0" />
            {t('admin.consoleTitle')}
          </h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            {t('admin.consoleDescription')}
          </p>
        </div>
        <div className="flex items-center gap-3 flex-shrink-0">
          <StatusLabel meta={meta} ready={ready} closed={closed} />
          <Dropdown
            size="sm"
            align="right"
            searchable
            mono
            value={selected}
            onChange={handleSelect}
            options={options}
            placeholder={usersLoading ? t('sidebar.loading') : t('admin.consoleSelectTarget')}
          />
          <button
            className="flex items-center gap-2 px-2 py-1 text-xs uppercase flex-shrink-0"
            onClick={() => fetchUsers()}
            title={t('admin.reloadAccounts')}
            style={{
              background: 'transparent',
              border: '1px solid var(--border)',
              borderRadius: 4,
              color: 'var(--text-secondary)',
              cursor: 'pointer',
              letterSpacing: '0.06em',
              transition: 'color 150ms ease, border-color 150ms ease',
            }}
            onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)' }}
            onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
          >
            <RefreshCw size={12} strokeWidth={1.5} style={usersLoading ? { animation: 'cv-spin 1s linear infinite' } : undefined} />
            {t('admin.reload')}
          </button>
        </div>
      </div>

      {/* Body — terminal fills the area; TerminalSession positions itself inset:0. */}
      <div className="flex-1" style={{ padding: '16px 24px 24px 24px', minHeight: 0 }}>
        {meta ? (
          <div style={{ position: 'relative', height: '100%', border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden', background: 'var(--bg-base)' }}>
            <TerminalSession
              key={selected}
              visible
              panelMinimized={false}
              targetUsername={meta.target}
              wsPath={meta.wsPath}
              onMetaChange={onMetaChange}
              onClosed={onClosed}
            />
          </div>
        ) : (
          <div
            className="flex flex-col items-center justify-center gap-2"
            style={{ height: '100%', border: '1px dashed var(--border)', borderRadius: 4, color: 'var(--text-dim)' }}
          >
            <SquareTerminal size={24} strokeWidth={1.5} />
            <span className="text-sm">{t('admin.consoleEmpty')}</span>
          </div>
        )}
      </div>
    </div>
  )
}
