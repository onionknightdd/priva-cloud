import { useMemo, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { useStaggerEntrance } from '@shared/motion/useStaggerEntrance'
import useChatStore from '../../stores/chatStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useSidebarStore from '../../stores/sidebarStore'
import useTaskStore from '../../stores/taskStore'
import useUserDataStore from '../../stores/userDataStore'
import useUiStore from '@shared/stores/uiStore'
import { fetchSessionMessages } from '../../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../../utils/sessionTransform'

function compactRelativeTime(value) {
  if (!value) return ''
  const ms = Number(value) < 1_000_000_000_000 ? Number(value) * 1000 : Number(value)
  if (!Number.isFinite(ms)) return ''
  const diff = Math.max(0, Date.now() - ms)
  const minute = 60 * 1000
  const hour = 60 * minute
  const day = 24 * hour
  const month = 30 * day
  const year = 365 * day
  if (diff < minute) return 'now'
  if (diff < hour) return `${Math.floor(diff / minute)}m`
  if (diff < day) return `${Math.floor(diff / hour)}h`
  if (diff < month) return `${Math.floor(diff / day)}d`
  if (diff < year) return `${Math.floor(diff / month)}mo`
  return `${Math.floor(diff / year)}y`
}

function cwdLabel(cwd) {
  if (!cwd) return ''
  const parts = String(cwd).split('/').filter(Boolean)
  return parts.at(-1) || cwd
}

function shortSessionId(id) {
  if (!id) return ''
  return String(id).slice(0, 8)
}

function buildRows(recentActivities, sessions) {
  const byId = new Map(sessions.map((s) => [s.sessionId || s.id, s]))
  return (recentActivities || []).slice(0, 5).map((activity) => {
    const sessionId = activity.session_id || activity.sessionId
    const session = byId.get(sessionId)
    const cwd = session?.cwd || activity.cwd || ''
    const title = session?.customTitle || session?.firstPrompt || session?.name || activity.title || cwdLabel(cwd) || shortSessionId(sessionId)
    const summary = session?.summary || activity.summary || (cwd ? cwd : shortSessionId(sessionId))
    const lastModified = session?.createdAt || activity.last_modified || activity.lastModified || null
    return {
      id: sessionId || `${cwd}:${title}`,
      sessionId,
      title,
      summary,
      time: compactRelativeTime(lastModified),
    }
  })
}

function RecentActivitySkeleton() {
  return (
    <div className="flex flex-col" style={{ gap: 6 }}>
      {[0, 1, 2].map((i) => (
        <div
          key={i}
          className="skeleton"
          style={{
            height: 36,
            borderRadius: 4,
          }}
        />
      ))}
    </div>
  )
}

export default function RecentActivities() {
  const recentActivities = useSidebarStore((s) => s.recentActivities)
  const sessions = useSidebarStore((s) => s.sessions)
  const setActiveSessionId = useSidebarStore((s) => s.setActiveSessionId)
  const overviewLoading = useUserDataStore((s) => s.overviewLoading)
  const loadSession = useChatStore((s) => s.loadSession)
  const [loadingSessionId, setLoadingSessionId] = useState(null)
  const entranceRef = useStaggerEntrance({ duration: 220, rise: 6, stepMs: 35 })

  const rows = useMemo(
    () => buildRows(recentActivities, sessions),
    [recentActivities, sessions]
  )

  const openActivity = async (row) => {
    if (!row.sessionId || loadingSessionId) return
    setLoadingSessionId(row.sessionId)
    try {
      useUiStore.getState().setActiveNavTab('priva')
      setActiveSessionId(row.sessionId)
      useTaskStore.getState().clearTasks()
      useFileOpsStore.getState().clearFileOps()
      useFileBrowserStore.getState().clear()
      const data = await fetchSessionMessages(row.sessionId)
      const {
        messages,
        fileOps,
        fileBrowserTabs,
        tasks,
        subagentContent,
      } = transformSessionMessages(data.messages || [])
      loadSession(row.sessionId, messages, null, subagentContent, data.add_dirs || [])

      const fileOpsStore = useFileOpsStore.getState()
      for (const op of fileOps) fileOpsStore.addFileOp(op)
      useFileBrowserStore.getState().setTabs(fileBrowserTabs)

      const taskStore = useTaskStore.getState()
      for (const task of tasks) taskStore.addTask(task)

      const hasInspectorItems = hasCanvasInspectorItems(messages)
      const canvasTab = fileBrowserTabs.length > 0
        ? 'file-browser'
        : fileOps.length > 0
          ? 'changes'
          : hasInspectorItems
            ? 'tasks'
            : null
      const ui = useUiStore.getState()
      if (canvasTab) {
        ui.showCanvas()
        ui.setActiveCanvasTab(canvasTab)
      } else {
        ui.hideCanvas()
      }
    } catch (err) {
      console.error('Failed to load recent activity:', err)
    } finally {
      setLoadingSessionId(null)
    }
  }

  return (
    <div className="flex flex-col min-w-0" style={{ gap: 6 }}>
      <div
        className="font-semibold"
        style={{
          color: 'var(--text-primary)',
          fontSize: 12,
          lineHeight: 1.3,
        }}
      >
        Recent activities
      </div>

      <div
        className="flex flex-col min-w-0"
        style={{
          gap: 6,
        }}
      >
        {overviewLoading && rows.length === 0 ? (
          <RecentActivitySkeleton />
        ) : rows.length === 0 ? (
          <div
            className="text-xs"
            style={{
              color: 'var(--text-dim)',
              padding: '14px 8px',
              textAlign: 'center',
            }}
          >
            No recent activity
          </div>
        ) : (
          rows.map((row) => {
            const loading = loadingSessionId === row.sessionId
            return (
              <button
                key={row.id}
                ref={entranceRef(row.id)}
                type="button"
                disabled={loading}
                onClick={() => openActivity(row)}
                className="flex items-center gap-2 min-w-0"
                style={{
                  minHeight: 36,
                  width: '100%',
                  padding: '6px 8px',
                  background: 'var(--bg-elevated)',
                  border: 'none',
                  borderRadius: 4,
                  color: 'var(--text-secondary)',
                  cursor: loading ? 'default' : 'pointer',
                  textAlign: 'left',
                  transition: 'background 150ms ease, color 150ms ease',
                }}
                onMouseEnter={(event) => {
                  event.currentTarget.style.background = 'var(--bg-surface)'
                  event.currentTarget.style.color = 'var(--text-primary)'
                }}
                onMouseLeave={(event) => {
                  event.currentTarget.style.background = 'var(--bg-elevated)'
                  event.currentTarget.style.color = 'var(--text-secondary)'
                }}
              >
                <div className="flex items-center min-w-0 flex-1" style={{ gap: 6 }}>
                  <span
                    className="truncate"
                    style={{
                      color: 'var(--text-primary)',
                      fontSize: 12,
                      fontWeight: 500,
                      maxWidth: '38%',
                      minWidth: 0,
                    }}
                    title={row.title}
                  >
                    {row.title}
                  </span>
                  <span
                    className="truncate"
                    style={{
                      color: 'var(--text-secondary)',
                      fontSize: 11,
                      fontWeight: 300,
                      minWidth: 0,
                      flex: 1,
                    }}
                    title={row.summary}
                  >
                    {row.summary}
                  </span>
                </div>
                {row.time && (
                  <span
                    style={{
                      color: 'var(--text-dim)',
                      fontSize: 10,
                      fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                      flexShrink: 0,
                    }}
                  >
                    {row.time}
                  </span>
                )}
                <ChevronRight size={13} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
              </button>
            )
          })
        )}
      </div>
    </div>
  )
}
