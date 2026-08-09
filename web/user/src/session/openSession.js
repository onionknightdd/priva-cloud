import { fetchSessionMessages } from '../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../utils/sessionTransform'
import { hasSessionSummaryActivity } from '../utils/sessionSummary'
import { refreshSessionRecap } from '../utils/sessionRecap'
import {
  ensureRuntime,
  evictIfNeeded,
  getActiveKey,
  getRuntime,
  getSlice,
  hasRuntime,
  newDraftRuntime,
  resolveKey,
  setActiveKey,
} from '../stores/runtime/registry'
import useSessionStatusStore from '../stores/sessionStatusStore'
import useSidebarStore from '../stores/sidebarStore'
import useSettingsStore from '../stores/settingsStore'
import useUiStore from '@shared/stores/uiStore'
import useToastStore from '@shared/stores/toastStore'
import { UnauthorizedError } from '@shared/api/client'
import i18n from '@shared/i18n'

// The single open/switch path for sessions. Replaces the four duplicated
// "stop → clear stores → fetch → transform → load → repopulate → canvas"
// ladders (Sidebar / RecentActivities / fork / OptimizePopup). Switching
// never stops a stream any more: a live session keeps producing into its own
// runtime while another renders.

export const DEFAULT_UI_SNAPSHOT = {
  canvasVisible: false,
  sessionSummaryOpen: false,
  canvasMinimized: false,
  activeCanvasTab: 'tasks',
  canvasOpenTabs: [],
  planContent: null,
  planFilePath: null,
}

export function pickUiSnapshot() {
  const s = useUiStore.getState()
  return {
    canvasVisible: s.canvasVisible,
    sessionSummaryOpen: s.sessionSummaryOpen,
    canvasMinimized: s.canvasMinimized,
    activeCanvasTab: s.activeCanvasTab,
    canvasOpenTabs: s.canvasOpenTabs,
    planContent: s.planContent,
    planFilePath: s.planFilePath,
  }
}

// Park the on-screen canvas/plan state on the runtime being switched away
// from, so switching back restores its canvas exactly.
function snapshotActiveUi() {
  const rt = getRuntime(getActiveKey())
  if (rt) rt.meta.ui = pickUiSnapshot()
}

function applyUiSnapshot(ui) {
  const next = { ...DEFAULT_UI_SNAPSHOT, ...(ui || {}) }
  useUiStore.setState(next)
  if (next.canvasVisible && next.activeCanvasTab !== 'menu') {
    useUiStore.getState().setActiveCanvasTab(next.activeCanvasTab)
  }
}

function initialUiFor({ fileBrowserTabs, fileOps, messages, sdkTaskTracker, subagentContent }) {
  const hasSummaryContent = hasSessionSummaryActivity({
    fileBrowserTabs,
    fileOps,
    messages,
    subagentContent,
  })
  if (hasSummaryContent) return { sessionSummaryOpen: true }
  if (hasCanvasInspectorItems(messages, sdkTaskTracker)) return 'tasks'
  return null
}

// Rapid re-selects: only the LAST cold open wins; earlier fetches no-op.
let selectToken = 0

/**
 * Open a session (sidebar row object or bare session id) as the active one.
 *
 * - already active → mark its dot seen, sync the sidebar highlight.
 * - runtime retained (live stream or recently viewed) → instant swap, restore
 *   its parked canvas/plan UI. No fetch, no flicker, stream untouched.
 * - cold → hydrate a background runtime FIRST (fetch + transform + load),
 *   swap only when ready — the previous session (possibly live) stays on
 *   screen until then.
 */
export async function openSession(sessionOrId, opts = {}) {
  const row = sessionOrId && typeof sessionOrId === 'object' ? sessionOrId : null
  const rawSessionId = row ? (row.sessionId || row.id) : sessionOrId
  if (!rawSessionId) return false
  const knownRow = useSidebarStore.getState().sessions.find((item) => (
    (item.sessionId || item.id) === rawSessionId
  ))
  const rowHasResponseModel = row && Object.prototype.hasOwnProperty.call(row, 'lastResponseModel')
  const knownRowHasResponseModel = knownRow && Object.prototype.hasOwnProperty.call(
    knownRow, 'lastResponseModel',
  )
  const hasRowResponseModel = !!(rowHasResponseModel || knownRowHasResponseModel)
  const responseModel = rowHasResponseModel
    ? row.lastResponseModel
    : (knownRowHasResponseModel ? knownRow.lastResponseModel : null)
  // Resume rotates session ids per turn — a sidebar row may still hold a
  // former id. Resolve to the live runtime's canonical key so switching back
  // to a running (e.g. mid-workflow) session finds it instead of cold-loading
  // a stale snapshot.
  const sessionId = resolveKey(rawSessionId)
  const rowId = row ? row.id : rawSessionId
  const { forkParentId = null, navigate = true } = opts

  const statusStore = useSessionStatusStore.getState()
  if (navigate) useUiStore.getState().setActiveNavTab('priva')

  if (sessionId === getActiveKey()) {
    const rt = getRuntime(sessionId)
    if (rt && !hasRowResponseModel && rt.meta.lastResponseModel !== undefined) {
      useSettingsStore.getState().activateSessionModel(sessionId, rt.meta.lastResponseModel)
    } else {
      useSettingsStore.getState().activateSessionModel(sessionId, responseModel)
    }
    useSidebarStore.getState().setActiveSessionId(rowId)
    statusStore.markSeen(sessionId)
    return true
  }

  if (hasRuntime(sessionId)) {
    snapshotActiveUi()
    const rt = getRuntime(sessionId)
    rt.meta.sidebarRowId = rowId
    if (hasRowResponseModel || rt.meta.lastResponseModel === undefined) {
      rt.meta.lastResponseModel = responseModel
    }
    setActiveKey(sessionId)
    useSettingsStore.getState().activateSessionModel(sessionId, rt.meta.lastResponseModel)
    applyUiSnapshot(rt.meta.ui)
    useSidebarStore.getState().setActiveSessionId(rowId)
    statusStore.markSeen(sessionId)
    return true
  }

  const token = ++selectToken
  try {
    const data = await fetchSessionMessages(sessionId)
    if (token !== selectToken) return false
    const { messages, fileOps, fileBrowserTabs, tasks, sdkTaskTracker, subagentContent } =
      transformSessionMessages(data.messages || [])

    const rt = ensureRuntime(sessionId)
    rt.meta.sidebarRowId = rowId
    rt.meta.lastResponseModel = responseModel
    const chat = getSlice(sessionId, 'chat')
    const taskSlice = getSlice(sessionId, 'tasks')
    const fileOpsSlice = getSlice(sessionId, 'fileOps')
    const fileBrowserSlice = getSlice(sessionId, 'fileBrowser')

    // Idempotent re-hydration (a previously evicted runtime re-opens clean).
    taskSlice.getState().clearTasks()
    fileOpsSlice.getState().clearFileOps()
    chat.getState().loadSession(sessionId, messages, forkParentId, subagentContent, data.add_dirs || [])
    // Whatever recap this session already has; not awaited, so it fades in
    // after the transcript rather than holding the switch.
    refreshSessionRecap(sessionId, chat.getState)
    for (const op of fileOps) fileOpsSlice.getState().addFileOp(op)
    fileBrowserSlice.getState().setTabs(fileBrowserTabs)
    for (const task of tasks) taskSlice.getState().addTask(task)
    taskSlice.getState().hydrateSdkTaskTracker(sdkTaskTracker)

    if (token !== selectToken) return false
    snapshotActiveUi()
    setActiveKey(sessionId)
    useSettingsStore.getState().activateSessionModel(sessionId, rt.meta.lastResponseModel)
    const initialUi = initialUiFor({
      fileBrowserTabs,
      fileOps,
      messages,
      sdkTaskTracker,
      subagentContent,
    })
    applyUiSnapshot(typeof initialUi === 'string'
      ? { canvasVisible: true, activeCanvasTab: initialUi, canvasOpenTabs: [initialUi] }
      : initialUi)
    useSidebarStore.getState().setActiveSessionId(rowId)
    statusStore.markSeen(sessionId)
    evictIfNeeded()
    return true
  } catch (err) {
    if (err instanceof UnauthorizedError) return false
    console.error('Failed to load session messages:', err)
    // Keep the previous view instead of loading an empty session — an empty
    // chat looks like data loss. Offer a retry via toast.
    useToastStore.getState().pushToast({
      level: 'error',
      title: i18n.t('sidebar.loadFailedTitle'),
      body: String(err?.message || err),
      action: {
        label: i18n.t('sidebar.loadFailedRetry'),
        onClick: () => openSession(sessionOrId, opts),
      },
    })
    return false
  }
}

/**
 * Start a fresh conversation in a new draft runtime. The previous session's
 * runtime (and any live stream on it) is left untouched in the background.
 */
export function newDraftSession(opts = {}) {
  const { cwd = null, pendingComposerSend = null } = opts
  snapshotActiveUi()
  const key = newDraftRuntime()
  useSettingsStore.getState().activateSessionModel(key, null)
  applyUiSnapshot(null)
  useSidebarStore.getState().setActiveSessionId(null)
  const chat = getSlice(key, 'chat')
  if (cwd) chat.getState().setCwdDraft(cwd)
  if (pendingComposerSend) chat.getState().setPendingComposerSend(pendingComposerSend)
  evictIfNeeded()
  return key
}
