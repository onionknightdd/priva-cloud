import { fetchRunningSessions, fetchSessionMessages } from '../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../utils/sessionTransform'
import { hasSessionSummaryActivity } from '../utils/sessionSummary'
import { ensureRuntime, getSlice, hasRuntime } from '../stores/runtime/registry'
import { attachToRunningSession } from '../hooks/useSSE'
import { getSplitParams } from '../utils/splitMode'

// Phase 2 boot restore: re-join runs that survived a page refresh in the
// backend RunRegistry. For each live run we hydrate a BACKGROUND runtime from
// the session JSONL — cut at the run's first user message, because the run's
// own events are replayed in full over the attach socket — then attach. Dots
// go purple/orange again and clicking the row swaps to the live transcript.
//
// Old backends (no /sessions/running route) make this a silent no-op.

let restored = false

export async function restoreRunningSessions() {
  if (restored) return
  restored = true
  // Split-pane iframes host exactly one embedded session — the root window
  // owns background attach.
  if (getSplitParams().paneId) return

  let running = []
  try {
    const data = await fetchRunningSessions()
    running = Array.isArray(data?.running) ? data.running : []
  } catch {
    return // registry-less backend or transient error — nothing to restore
  }

  for (const run of running) {
    const sessionId = run.session_id
    if (!sessionId || hasRuntime(sessionId)) continue
    try {
      const data = await fetchSessionMessages(sessionId)
      let rows = data.messages || []
      // Drop the current run's rows from the snapshot: the attach replay
      // rebuilds them event-by-event (otherwise they'd render twice).
      if (run.first_user_uuid) {
        const cut = rows.findIndex((m) => m.uuid === run.first_user_uuid)
        if (cut >= 0) {
          const startMs = run.started_at ? run.started_at * 1000 : null
          const head = rows.slice(0, cut)
          // Sidechain (subagent) rows ride after the main rows — keep only
          // those from BEFORE the run started.
          rows = startMs
            ? head.filter((m) => {
              if (!m.parent_tool_use_id) return true
              const ts = Date.parse(m?.metadata?.timestamp || m?.timestamp || '')
              return !(Number.isFinite(ts) && ts >= startMs)
            })
            : head
        }
      }
      const { messages, fileOps, fileBrowserTabs, tasks, sdkTaskTracker, subagentContent } =
        transformSessionMessages(rows)

      const rt = ensureRuntime(sessionId)
      rt.meta.sidebarRowId = sessionId
      const chat = getSlice(sessionId, 'chat')
      const taskSlice = getSlice(sessionId, 'tasks')
      taskSlice.getState().clearTasks()
      getSlice(sessionId, 'fileOps').getState().clearFileOps()
      chat.getState().loadSession(sessionId, messages, null, subagentContent, data.add_dirs || [])
      for (const op of fileOps) getSlice(sessionId, 'fileOps').getState().addFileOp(op)
      getSlice(sessionId, 'fileBrowser').getState().setTabs(fileBrowserTabs)
      for (const task of tasks) taskSlice.getState().addTask(task)
      taskSlice.getState().hydrateSdkTaskTracker(sdkTaskTracker)
      const preferSummary = hasSessionSummaryActivity({
        fileBrowserTabs,
        fileOps,
        messages,
        subagentContent,
      })
      const canvasTab = !preferSummary && hasCanvasInspectorItems(messages, sdkTaskTracker)
        ? 'tasks'
        : null
      if (preferSummary) {
        rt.meta.ui = {
          ...(rt.meta.ui || {}),
          canvasVisible: false,
          sessionSummaryOpen: true,
        }
      } else if (canvasTab) {
        rt.meta.ui = {
          ...(rt.meta.ui || {}),
          canvasVisible: true,
          canvasMinimized: false,
          activeCanvasTab: canvasTab,
          canvasOpenTabs: [canvasTab],
        }
      }

      attachToRunningSession(sessionId, { sinceSeq: 0 })
    } catch (err) {
      console.warn('[attach] failed to restore running session', sessionId, err)
    }
  }
}
