import { getToken } from '@shared/api/tokenStore'
import { fetchRunningSessions, fetchSessionMessages } from '../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../utils/sessionTransform'
import { hasSessionSummaryActivity } from '../utils/sessionSummary'
import { ensureRuntime, getSlice } from '../stores/runtime/registry'
import { attachToRunningSession } from '../hooks/useSSE'
import { getSplitParams } from '../utils/splitMode'

// Re-join registry-owned runs after a refresh, a network interruption, or a
// local WS retry budget being exhausted. Hydration is cut at the current run's
// first user event because attach replays that run from seq 0.
//
// The registry is process-local, so this is deliberately a reconciliation
// loop rather than a one-shot boot action: a transient request failure must not
// strand a healthy backend run until the user refreshes again.

const RECONCILE_BACKOFF_MS = [1000, 2000, 4000, 8000, 16000, 30000]

let reconcileInFlight = null
let retryTimer = null
let retryAttempt = 0
let listenersInstalled = false

function clearRetry() {
  if (retryTimer !== null) {
    window.clearTimeout(retryTimer)
    retryTimer = null
  }
  retryAttempt = 0
}

function scheduleRetry() {
  if (retryTimer !== null || !getToken()) return
  const index = Math.min(retryAttempt, RECONCILE_BACKOFF_MS.length - 1)
  const delay = RECONCILE_BACKOFF_MS[index]
  retryAttempt += 1
  retryTimer = window.setTimeout(() => {
    retryTimer = null
    void restoreRunningSessions()
  }, delay)
}

function installReconcileListeners() {
  if (listenersInstalled) return
  listenersInstalled = true

  const reconcile = () => {
    if (getToken()) void restoreRunningSessions()
  }
  const reconcileWhenVisible = () => {
    if (document.visibilityState === 'visible') reconcile()
  }

  window.addEventListener('priva:reconcile-running-sessions', reconcile)
  window.addEventListener('online', reconcile)
  document.addEventListener('visibilitychange', reconcileWhenVisible)
}

function hydrateRuntime(sessionId, run, data, { includeCurrentRun = false } = {}) {
  let rows = data.messages || []
  // Drop the current run's rows from the snapshot: attach replay rebuilds
  // them event-by-event (otherwise they render twice).
  if (!includeCurrentRun && run.first_user_uuid) {
    const cut = rows.findIndex((message) => message.uuid === run.first_user_uuid)
    if (cut >= 0) {
      const startMs = run.started_at ? run.started_at * 1000 : null
      const head = rows.slice(0, cut)
      // Sidechain (subagent) rows ride after main rows. Keep only sidechain
      // events written before this run began.
      rows = startMs
        ? head.filter((message) => {
          if (!message.parent_tool_use_id) return true
          const timestamp = Date.parse(message?.metadata?.timestamp || message?.timestamp || '')
          return !(Number.isFinite(timestamp) && timestamp >= startMs)
        })
        : head
    }
  }

  const { messages, fileOps, fileBrowserTabs, tasks, sdkTaskTracker, subagentContent } =
    transformSessionMessages(rows)

  const runtime = ensureRuntime(sessionId)
  runtime.meta.sidebarRowId = sessionId
  const chat = getSlice(sessionId, 'chat')
  const taskSlice = getSlice(sessionId, 'tasks')
  taskSlice.getState().clearTasks()
  getSlice(sessionId, 'fileOps').getState().clearFileOps()
  chat.getState().loadSession(
    sessionId,
    messages,
    null,
    subagentContent,
    data.add_dirs || [],
    data.run_mode || run.run_mode,
  )
  for (const operation of fileOps) getSlice(sessionId, 'fileOps').getState().addFileOp(operation)
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
    runtime.meta.ui = {
      ...(runtime.meta.ui || {}),
      canvasVisible: false,
      sessionSummaryOpen: true,
    }
  } else if (canvasTab) {
    runtime.meta.ui = {
      ...(runtime.meta.ui || {}),
      canvasVisible: true,
      canvasMinimized: false,
      activeCanvasTab: canvasTab,
      canvasOpenTabs: [canvasTab],
    }
  }
}

async function reconcileRunningSessions() {
  let running
  try {
    const data = await fetchRunningSessions()
    running = Array.isArray(data?.running) ? data.running : []
  } catch {
    return false
  }

  let allAttached = true
  for (const run of running) {
    const sessionId = run.session_id
    if (!sessionId) {
      allAttached = false
      continue
    }

    const runtime = ensureRuntime(sessionId)
    const chat = getSlice(runtime.key, 'chat')
    if (chat.getState().isStreaming) continue

    try {
      const data = await fetchSessionMessages(sessionId)
      // Another reconcile trigger may have attached while the transcript was
      // loading. Never replace a live runtime underneath its stream.
      if (chat.getState().isStreaming) continue
      if (data.live_run_id !== run.run_id) {
        allAttached = false
        continue
      }
      // Use the first sequence captured with this exact transcript snapshot,
      // not the earlier /sessions/running response: the replay buffer may trim
      // between those two requests during a high-volume run.
      const replayGap = Number(data.live_first_seq) > 1
      const barrierSeq = Number(data.live_seq)
      if (replayGap && !Number.isInteger(barrierSeq)) {
        allAttached = false
        continue
      }
      hydrateRuntime(sessionId, run, data, { includeCurrentRun: replayGap })
      attachToRunningSession(sessionId, {
        sinceSeq: replayGap ? barrierSeq : 0,
      })
    } catch (error) {
      allAttached = false
      console.warn('[attach] failed to restore running session', sessionId, error)
    }
  }
  return allAttached
}

export function restoreRunningSessions() {
  // Split-pane iframes host exactly one embedded session; the root window owns
  // background reconciliation and attach sockets.
  if (getSplitParams().paneId || !getToken()) return Promise.resolve()
  installReconcileListeners()
  if (reconcileInFlight) return reconcileInFlight

  reconcileInFlight = reconcileRunningSessions()
    .then((succeeded) => {
      if (succeeded) clearRetry()
      else scheduleRetry()
    })
    .finally(() => {
      reconcileInFlight = null
    })
  return reconcileInFlight
}
