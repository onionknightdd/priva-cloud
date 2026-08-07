import { useCallback } from 'react'
import { streamAgentRun, streamAgentRunWS, attachAgentRunWS, respondPermission as respondPermissionAPI } from '../api/sse'
import { setSessionAddDirs } from '../api/sessions'
import useChatStore from '../stores/chatStore'
import { isTerminalRawStatus, rawToTaskStatus } from '../stores/workflowStore'
import useUiStore from '@shared/stores/uiStore'
import useSidebarStore from '../stores/sidebarStore'
import useSettingsStore from '../stores/settingsStore'
import useToastStore from '@shared/stores/toastStore'
import useSessionStatusStore from '../stores/sessionStatusStore'
import {
  ensureRuntime,
  getActiveKey,
  getSlice,
  hasRuntime,
  listRuntimes,
  rekeyRuntime,
  resolveKey,
} from '../stores/runtime/registry'
import { DEFAULT_UI_SNAPSHOT } from '../session/openSession'
import i18n from '@shared/i18n'
import {
  GENERATED_TOOL_LABEL,
  getGeneratedInputPaths,
  isGeneratedToolName,
} from '../utils/generatedTool'
import {
  FILE_SOURCE_CURRENT,
  FILE_TOOL_NAMES,
  browserSourceLabel,
  fileNameFromPath,
  fileTabFromToolUse,
  fileTabsFromGeneratedFiles,
  isErroredToolResult,
} from '../utils/fileArtifacts'
import { getSplitParams } from '../utils/splitMode'
import { parseTaskNotification } from '../utils/taskNotification'
import { refreshSessionRecap } from '../utils/sessionRecap'
import { isSdkTaskToolName } from '../utils/sdkTaskTracker'
import {
  findMatchingAskUserBlockIndex,
  isAskUserInputValidationError,
} from '../utils/askUserQuestion'

// Max characters of background-shell output kept in the task store; only the
// tail is retained beyond this.
const MAX_LIVE_OUTPUT = 200_000

function broadcastSplitStop(sessionId) {
  if (!sessionId || typeof window === 'undefined' || typeof BroadcastChannel === 'undefined') return
  const { paneId } = getSplitParams()
  const channel = new BroadcastChannel(`priva-session:${sessionId}`)
  channel.postMessage({
    type: 'stop-request',
    paneId: paneId || window.__PRIVA_TAB_ID || 'root',
  })
  window.setTimeout(() => channel.close(), 0)
}

// Resolve a session id (or runtime key) to the runtime that hosts it —
// through rotated-id aliases first, then by scanning chat.sessionId (covers
// draft-keyed runtimes that learned their id but weren't rekeyed yet).
function resolveRuntimeKey(sessionIdOrKey) {
  if (!sessionIdOrKey) return null
  const canonical = resolveKey(sessionIdOrKey)
  if (hasRuntime(canonical)) return canonical
  for (const rt of listRuntimes()) {
    if (rt.slices.chat?.getState?.().sessionId === sessionIdOrKey) return rt.key
  }
  return null
}

// Terminal dot state for a runtime: a still-unanswered question keeps the
// orange dot even after the stream ends; otherwise green in the background,
// gray when the user is already looking at it.
function terminalStatusFor(key) {
  const chat = getSlice(key, 'chat').getState()
  const anyPending = chat.pendingAskUser || chat.pendingPermission || chat.pendingPlanApproval
    || (chat.permissionQueue || []).length > 0
  if (anyPending) return 'attention'
  return getActiveKey() === key ? 'seen' : 'unseen'
}

// Re-derive the ACTIVE session's dot after a permission/question resolution.
function recomputeActiveStatus() {
  const key = getActiveKey()
  const chat = getSlice(key, 'chat').getState()
  const anyPending = chat.pendingAskUser || chat.pendingPermission || chat.pendingPlanApproval
    || (chat.permissionQueue || []).length > 0
  const statuses = useSessionStatusStore.getState().statuses
  if (!(key in statuses)) return
  if (chat.isStreaming) {
    useSessionStatusStore.getState().setStatus(key, anyPending ? 'attention' : 'running')
  } else {
    useSessionStatusStore.getState().setStatus(key, anyPending ? 'attention' : 'seen')
  }
}

function markCurrentResponseInterrupted(chatSlice) {
  chatSlice.setState((state) => {
    let lastUserIndex = -1
    for (let index = state.messages.length - 1; index >= 0; index -= 1) {
      if (state.messages[index]?.role === 'user') {
        lastUserIndex = index
        break
      }
    }
    let changed = false
    const messages = state.messages.map((message, index) => {
      if (index <= lastUserIndex || message?.role !== 'assistant' || message.responseInterrupted) {
        return message
      }
      changed = true
      return { ...message, responseInterrupted: true }
    })
    return changed ? { messages } : {}
  })
}

/**
 * Stop ONE session's stream (by runtime key or session id): abort the
 * transport, mark that runtime's in-flight tools/tasks/workflows aborted,
 * and settle its dot. Other sessions' streams are untouched.
 */
export function stopSessionStream(sessionIdOrKey, options = {}) {
  const { broadcast = true } = options
  const key = resolveRuntimeKey(sessionIdOrKey)
  if (!key) return
  const chatSlice = getSlice(key, 'chat')
  const {
    sessionId, streamAbort, setStreaming, setStreamAbort, setWsSendPermission,
    clearPermissions, abortRunningTools, bumpStreamGeneration,
  } = chatSlice.getState()
  if (broadcast) broadcastSplitStop(sessionId)
  // Invalidate in-flight onEvent/onComplete callbacks even when the abort
  // handle is already gone.
  bumpStreamGeneration()
  if (streamAbort) {
    streamAbort()
    abortRunningTools()
    markCurrentResponseInterrupted(chatSlice)
    getSlice(key, 'tasks').getState().abortRunningTasks()
    getSlice(key, 'workflow').getState().abortRunning()
    setStreaming(false)
    setStreamAbort(null)
    setWsSendPermission(null)
    clearPermissions()
    const statuses = useSessionStatusStore.getState().statuses
    if (key in statuses) {
      useSessionStatusStore.getState().setStatus(key, terminalStatusFor(key))
    }
  }
}

// Back-compat: stop the ACTIVE session's stream (split-pane bridges, stop
// buttons, ErrorBlock retry all target the session the user is looking at).
export function stopActiveStream(options = {}) {
  stopSessionStream(getActiveKey(), options)
}

/**
 * The streaming pipeline, bound to ONE session runtime at start time. Every
 * event writes to that runtime's slices — never "whatever is active" — so
 * concurrent streams can't bleed into each other and switching sessions
 * leaves the stream running.
 *
 * `attach` mode (Phase 2) joins an already-running backend run instead of
 * sending a new user message: no local bubbles are created up front (the
 * server replays the run's events, including the prompt's user_message).
 */
function startStream({ key, message, permissionMode, attachments, attachmentsMeta, images, attach = null }) {
  const tabId = window.__PRIVA_TAB_ID || (window.__PRIVA_TAB_ID = Math.random().toString(36).slice(2, 8))
  const rt = ensureRuntime(key)

  // A runtime hosts one stream at a time — starting a second would clobber
  // the live abort/permission handles (and the backend refuses double-runs).
  {
    const chat = getSlice(rt.key, 'chat').getState()
    if (!attach && chat.isStreaming && chat.streamAbort) {
      console.warn('[SSE] startStream ignored: runtime %s already streaming', rt.key)
      return
    }
  }

  // Runtime-bound accessors. rt.key is live across a draft→sessionId rekey,
  // so these always resolve to THIS conversation's slices.
  const S = {
    chat: () => getSlice(rt.key, 'chat').getState(),
    chatSet: (partial) => getSlice(rt.key, 'chat').setState(partial),
    tasks: () => getSlice(rt.key, 'tasks').getState(),
    fileOps: () => getSlice(rt.key, 'fileOps').getState(),
    fileBrowser: () => getSlice(rt.key, 'fileBrowser').getState(),
    workflow: () => getSlice(rt.key, 'workflow').getState(),
  }
  const isActive = () => getActiveKey() === rt.key

  // UI side effects (canvas / plan) apply live only when this runtime is the
  // one on screen; otherwise the desired state parks on rt.meta.ui and is
  // applied when the user switches back.
  const uiFx = (apply, mergeUi) => {
    if (isActive()) {
      apply(useUiStore.getState())
    } else if (mergeUi) {
      rt.meta.ui = { ...DEFAULT_UI_SNAPSHOT, ...(rt.meta.ui || {}), ...mergeUi }
    }
  }
  const uiRead = (field) => (isActive() ? useUiStore.getState()[field] : (rt.meta.ui || {})[field])
  const fxShowCanvas = (tab) => uiFx(
    (ui) => { ui.showCanvas(); if (tab) ui.setActiveCanvasTab(tab) },
    { canvasVisible: true, ...(tab ? { activeCanvasTab: tab } : {}) },
  )

  const statusStore = () => useSessionStatusStore.getState()
  const setRunStatus = (status) => statusStore().setStatus(rt.key, status)

  // Toasts from a backgrounded session carry its name so the user knows
  // which conversation errored.
  const sessionLabel = () => {
    const sid = rt.sessionId || S.chat().sessionId
    if (!sid) return null
    const row = useSidebarStore.getState().sessions.find((x) => (x.sessionId || x.id) === sid)
    const name = row?.name || String(sid).slice(0, 8)
    return name.length > 40 ? `${name.slice(0, 40)}…` : name
  }
  const pushToast = ({ level, title, body }) => {
    const prefix = isActive() ? null : sessionLabel()
    useToastStore.getState().pushToast({
      level,
      title: prefix ? `[${prefix}] ${title}` : title,
      body,
    })
  }

  const chatApi = getSlice(rt.key, 'chat').getState()
  const {
    setStreaming, setStreamAbort, setWsSendPermission, addMessage, updateToolResult,
    setStreamId, setPendingPermission, queuePermission, setCompacting, setSessionId,
    recordCheckpoint, setRetryState, clearRetryState, setLastUserPrompt,
  } = chatApi
  const sessionIdAtSend = attach ? (attach.sessionId || S.chat().sessionId) : S.chat().sessionId
  const enableFileCheckpointing = S.chat().enableFileCheckpointing

  const promptPreview = attach
    ? `(attach ${String(attach.sessionId || '').slice(0, 8)})`
    : String(message).replace(/\s+/g, ' ').slice(0, 120)
  console.info('[TAB:%s] %s sessionId=%s prompt=%s', tabId, attach ? 'attachStream' : 'sendMessage', sessionIdAtSend, promptPreview)

  const {
    addTask,
    updateTask,
    setTodos,
    setTodoWriteInfo,
    beginSdkTaskRound,
    recordSdkTaskToolUse,
    recordSdkTaskToolResult,
  } = S.tasks()
  const { setLastResult } = useUiStore.getState()
  const { addFileOp, updateFileOp, incrementRound } = S.fileOps()
  void updateFileOp
  const openFileBrowserTab = (file) => S.fileBrowser().openFile(file)

  if (!attach) {
    setLastUserPrompt({ message, permissionMode, attachments, attachmentsMeta, images })
    clearRetryState()
    beginSdkTaskRound({ title: message, startedAt: Date.now() })

    // Add user message (with attachment info for display)
    const userMsg = {
      role: 'user',
      content: [],
      timestamp: Date.now(),
    }
    if (images && images.length > 0) {
      for (const img of images) {
        userMsg.content.push({
          type: 'image',
          source: { type: 'base64', media_type: img.media_type, data: img.data },
          filename: img.filename,
        })
      }
    }
    userMsg.content.push({ type: 'text', text: message })
    if (attachmentsMeta && attachmentsMeta.length > 0) {
      userMsg.attachments = attachmentsMeta
    }
    addMessage(userMsg)

    // Create assistant message placeholder
    addMessage({
      role: 'assistant',
      content: [],
      timestamp: Date.now(),
    })
  }

  const streamStartTime = Date.now()
  setStreaming(true)
  setRunStatus('running')

  // Capture the generation this stream belongs to; loadSession/stop bump it.
  const streamGen = S.chat().streamGeneration
  // Every assistant emission and tool_use envelope is a process group. The
  // message renderer uses this marker to keep only the newest group expanded
  // while older groups move into the collapsed process history.
  let processGroupSeq = 0
  const nextProcessGroupId = () => `${streamGen}-process-${++processGroupSeq}`

  // Track tool_use_ids that are canvas-only (hidden from message flow)
  const hiddenToolIds = new Set()
  const askUserToolIds = new Set()
  const generatedToolIds = new Set()
  const pendingToolFileTabs = new Map()
  // Track TodoWrite tool_use_ids for todo extraction
  const todoWriteIds = new Set()
  // Buffer hook_event payloads keyed by tool_use_id when they arrive before
  // the matching tool_use block is rendered. Flushed on tool_use arrival.
  const pendingHookEvents = new Map()

  const selectedModel = useSettingsStore.getState().selectedModel
  const transport = useSettingsStore.getState().transport

  // First session-id assignment of a brand-new conversation: rekey the draft
  // runtime, migrate its dot, and surface the new sidebar row mid-run.
  let announcedNewSession = Boolean(sessionIdAtSend)

  // system.init fires BEFORE the CLI has flushed the new session's JSONL, so
  // a single list fetch usually misses the row (it then only appeared at run
  // end / manual refresh). Poll with a bounded backoff until the row exists.
  const announceSessionRow = (sid) => {
    let attempts = 0
    const attempt = async () => {
      attempts += 1
      try {
        await useSidebarStore.getState().fetchSessions()
      } catch { /* transient — next attempt retries */ }
      const rows = useSidebarStore.getState().sessions
      const row = rows.find((x) => (x.sessionId || x.id) === rt.key || (x.sessionId || x.id) === sid)
      if (row) {
        rt.meta.sidebarRowId = row.id
        if (isActive()) useSidebarStore.getState().setActiveSessionId(row.id)
        return
      }
      if (attempts < 6) window.setTimeout(attempt, attempts * 1000) // ~15s window
    }
    attempt()
  }

  const adoptSessionId = (sid) => {
    if (!sid) return
    setSessionId(sid)
    if (rt.key !== sid) {
      const oldKey = rt.key
      rekeyRuntime(oldKey, sid)
      statusStore().rekey(oldKey, sid)
    }
    rt.meta.sidebarRowId = rt.meta.sidebarRowId || sid
    if (!announcedNewSession) {
      announcedNewSession = true
      if (isActive()) useSidebarStore.getState().setActiveSessionId(sid)
      announceSessionRow(sid)
    }
  }

  const openToolFileInBrowser = (block, options = {}) => {
    const tab = fileTabFromToolUse(block, FILE_SOURCE_CURRENT)
    if (!tab) return false
    openFileBrowserTab(tab)
    fxShowCanvas(options.activate ? 'file-browser' : null)
    return true
  }
  const isPlanWriteTool = (block) => (
    block?.name === 'Write' &&
    block.input?.file_path &&
    block.input.file_path.endsWith('.md') &&
    block.input.file_path.includes('/plans/')
  )

  const onEvent = (event, data) => {
    const state = S.chat()
    // Stale stream (stopped / reloaded since this stream began): drop the
    // event so it can't overwrite the freshly loaded state.
    if (state.streamGeneration !== streamGen) return
    const msgs = [...state.messages]
    const lastIdx = msgs.length - 1
    const lastMsg = msgs[lastIdx]

    console.debug('[SSE]', event, data)

    // Merge a hook_event payload into the tool_use block whose id matches
    // `toolUseId`. Returns true when the merge happened. The hookEvents
    // array is keyed by event uuid so a hook_started/hook_response pair
    // collapses into a single pill.
    function mergeHookEventIntoBlock(toolUseId, hookEvent) {
      if (!toolUseId) return false
      const messages = S.chat().messages
      for (let mi = messages.length - 1; mi >= 0; mi--) {
        const msg = messages[mi]
        if (!Array.isArray(msg.content)) continue
        let changed = false
        const newContent = msg.content.map((b) => {
          if (b?.type !== 'tool_use' || b.id !== toolUseId) return b
          const prevEvents = b.metadata?.hookEvents || []
          const evtKey = hookEvent.uuid || `${hookEvent.hook_event_name}-${hookEvent.subtype}-${prevEvents.length}`
          const filtered = prevEvents.filter((e) => (e.uuid || '') !== (hookEvent.uuid || '___none'))
          const nextEvents = [...filtered, { ...hookEvent, _key: evtKey }]
          changed = true
          return { ...b, metadata: { ...(b.metadata || {}), hookEvents: nextEvents } }
        })
        if (changed) {
          const updated = [...messages]
          updated[mi] = { ...msg, content: newContent }
          S.chatSet({ messages: updated })
          return true
        }
      }
      return false
    }

    // Extract todos from TodoWrite result content or tool_use_result
    function extractTodos(resultBlock, toolUseResult) {
      // Try tool_use_result dict first
      if (toolUseResult) {
        const items = toolUseResult.newTodos || toolUseResult.todos || toolUseResult.new_todos
        if (Array.isArray(items)) return items
      }
      // Try parsing result content as JSON
      if (typeof resultBlock.content === 'string' && resultBlock.content.trim()) {
        try {
          const parsed = JSON.parse(resultBlock.content)
          if (Array.isArray(parsed)) return parsed
          const items = parsed.newTodos || parsed.todos || parsed.new_todos
          if (Array.isArray(items)) return items
        } catch { /* not JSON */ }
      }
      return null
    }

    switch (event) {
      case 'stream_init': {
        console.info('[TAB:%s] stream_init streamId=%s', tabId, data.stream_id)
        if (data.stream_id) {
          setStreamId(data.stream_id)
        }
        break
      }

      case 'attach_ok': {
        // Phase 2 attach handshake: restore backend-held queue state.
        const queued = Array.isArray(data?.queued) ? data.queued : []
        if (queued.length > 0) {
          S.chatSet({
            queuedUserMessages: queued.map((q) => ({ id: q.id, text: q.text, status: 'pending' })),
          })
        }
        if (data?.session_id) adoptSessionId(data.session_id)
        break
      }

      case 'attach_error': {
        // The backend has no live run for this session (pod restarted or the
        // run ended long ago). Quietly settle back to idle — the transcript
        // snapshot is already loaded.
        setStreaming(false)
        setStreamAbort(null)
        statusStore().setStatus(rt.key, terminalStatusFor(rt.key))
        break
      }

      case 'user_message': {
        // A CLI-injected <task-notification> re-invokes the model after a
        // background Workflow / Bash finishes. Materialize it as a slim system
        // card + a fresh assistant turn for the summary to stream into — never
        // a raw-XML user bubble. Covers both the live drain and attach replay.
        {
          const rawText = Array.isArray(data.content)
            ? data.content.filter((b) => b?.type === 'text').map((b) => b.text).join('\n')
            : (typeof data.content === 'string' ? data.content : '')
          const notif = parseTaskNotification(rawText)
          if (notif) {
            const recent = S.chat().messages
            const dup = recent.slice(-4).some((m) => (
              m.role === 'system' && m.type === 'task_notification' && (
                (notif.taskId && m.notif?.taskId === notif.taskId) ||
                (notif.toolUseId && m.notif?.toolUseId === notif.toolUseId)
              )
            ))
            if (!dup) {
              const kind = notif.toolUseId && S.workflow().workflows[notif.toolUseId]
                ? 'workflow' : 'task'
              S.chat().addMessage({
                role: 'system',
                type: 'task_notification',
                notif: { ...notif, kind },
                uuid: data.uuid,
                timestamp: Date.now(),
              })
              // Fresh placeholder so the re-invocation summary streams as its
              // own turn below the card, not appended to the prior assistant.
              S.chat().addMessage({ role: 'assistant', content: [], timestamp: Date.now() })
            }
            break
          }
        }
        // Raw user frame (checkpoint carrier). Record UUID + attach to
        // the most recent user chat message so inline rewind/fork have
        // a target UUID. Deduped in-store by uuid.
        if (!data.uuid) break
        const currentMsgs = S.chat().messages
        let matched = false
        // Find last user message without a uuid yet and attach this UUID.
        for (let i = currentMsgs.length - 1; i >= 0; i--) {
          const m = currentMsgs[i]
          if (m.role === 'user' && !m.uuid) {
            const updated = [...currentMsgs]
            updated[i] = { ...m, uuid: data.uuid }
            S.chatSet({ messages: updated })
            matched = true
            break
          }
          if (m.role === 'user' && m.uuid === data.uuid) { matched = true; break }
        }
        // Attach replay: no locally-created bubble exists for the run's
        // prompt — rebuild it (plus the assistant placeholder) from the event.
        if (!matched && attach) {
          const content = []
          if (Array.isArray(data.content)) {
            for (const b of data.content) {
              if (b?.type === 'image') content.push(b)
              else if (b?.type === 'text' && typeof b.text === 'string') content.push({ type: 'text', text: b.text })
            }
          } else if (typeof data.content === 'string' && data.content.trim()) {
            content.push({ type: 'text', text: data.content })
          }
          const meaningful = content.some((b) => b.type === 'image'
            || (b.type === 'text' && b.text.trim() && !b.text.includes('<local-command-stdout>')))
          if (meaningful) {
            beginSdkTaskRound({
              title: content
                .filter((block) => block.type === 'text')
                .map((block) => block.text)
                .join(' ') || 'Image prompt',
              startedAt: Date.now(),
            })
            S.chat().addMessage({ role: 'user', content, uuid: data.uuid, timestamp: Date.now() })
            S.chat().addMessage({ role: 'assistant', content: [], timestamp: Date.now() })
          }
        }
        const previewRaw = Array.isArray(data.content)
          ? data.content.filter((b) => b?.type === 'text').map((b) => b.text).join(' ')
          : (typeof data.content === 'string' ? data.content : '')
        const preview = (previewRaw || '').trim().slice(0, 80)
        recordCheckpoint(data.uuid, S.chat().messages.length, preview)
        break
      }

      case 'permission_request': {
        console.info('[TAB:%s] RECEIVED permission_request request_id=%s tool=%s session_id=%s prompt=%s',
          tabId, data.request_id, data.tool_name, data.session_id, promptPreview)
        // ExitPlanMode → show plan approval card
        if (data.tool_name === 'ExitPlanMode') {
          if (S.chat().pendingPlanApproval?.requestId === data.request_id) break
          const planContent = data.input?.plan || data.input?.content || ''
          const planFilePath = uiRead('planFilePath')
          S.chat().setPendingPlanApproval({
            requestId: data.request_id,
            planContent,
            planFilePath,
          })
          // Also update plan content in canvas if not already set
          uiFx(
            (ui) => {
              if (planContent) ui.setPlanContent(planContent, planFilePath)
              ui.showCanvas()
              ui.setActiveCanvasTab('plan')
            },
            {
              ...(planContent ? { planContent, planFilePath } : {}),
              canvasVisible: true,
              activeCanvasTab: 'plan',
            },
          )
          setRunStatus('attention')
          break
        }
        // AskUserQuestion via can_use_tool → route to existing ask_user flow
        if (data.tool_name === 'AskUserQuestion' && data.input?.questions) {
          if (S.chat().pendingAskUser?._permissionRequestId === data.request_id) break
          const askProcessGroupId = nextProcessGroupId()
          const toolUseId = data.tool_use_id || data.request_id
          const existingIndex = lastMsg?.role === 'assistant'
            ? findMatchingAskUserBlockIndex(lastMsg.content, {
              toolUseId,
              questions: data.input.questions,
            })
            : -1
          const existingBlock = existingIndex >= 0 ? lastMsg.content[existingIndex] : null
          const askBlock = {
            ...existingBlock,
            type: 'ask_user',
            id: existingBlock?.id || toolUseId,
            toolUseId,
            questions: data.input.questions,
            _permissionRequestId: data.request_id,
            status: existingBlock?.status || 'pending',
            processGroupId: existingBlock?.processGroupId || askProcessGroupId,
          }
          S.chat().setPendingAskUser(askBlock)
          // The assistant tool_use normally arrives first. Reconcile the
          // permission request into that existing block instead of appending a
          // second copy under the unrelated permission request UUID.
          if (lastMsg && lastMsg.role === 'assistant') {
            const newContent = existingIndex >= 0
              ? lastMsg.content.map((block, index) => index === existingIndex ? askBlock : block)
              : [...lastMsg.content, askBlock]
            S.chatSet({
              messages: [...msgs.slice(0, lastIdx), { ...lastMsg, content: newContent }],
            })
          }
          setRunStatus('attention')
          break
        }
        const { pendingPermission, permissionQueue } = S.chat()
        const alreadyPending = pendingPermission?.request_id === data.request_id
          || permissionQueue.some((p) => p.request_id === data.request_id)
        if (alreadyPending) break
        if (pendingPermission) {
          queuePermission(data)
        } else {
          setPendingPermission(data)
        }
        setRunStatus('attention')
        break
      }

      case 'permission_timeout': {
        // Backend gave up waiting (default 600s) and auto-denied. Clear the
        // stale card so the UI matches reality.
        const rid = data?.request_id
        if (!rid) break
        const chat = S.chat()
        if (chat.pendingPlanApproval?.requestId === rid) chat.clearPendingPlanApproval()
        if (chat.pendingAskUser?._permissionRequestId === rid) chat.clearPendingAskUser()
        if (chat.pendingPermission?.request_id === rid) {
          chat.resolvePermission(rid)
        } else if (chat.permissionQueue.some((p) => p.request_id === rid)) {
          S.chatSet({ permissionQueue: chat.permissionQueue.filter((p) => p.request_id !== rid) })
        }
        const after = S.chat()
        const anyPending = after.pendingPermission || after.pendingAskUser || after.pendingPlanApproval
          || after.permissionQueue.length > 0
        if (after.isStreaming) setRunStatus(anyPending ? 'attention' : 'running')
        break
      }

      case 'assistant': {
        // Real assistant content arrived — flash "reconnect successful" if a
        // retry was pending, then clear after a beat. Use the store snapshot
        // captured right before mutation so a fresh retry started during the
        // 1.5s window doesn't get prematurely wiped.
        const priorRetry = S.chat().retryState
        if (priorRetry && !priorRetry.succeeded) {
          S.chat().markRetrySucceeded()
          setTimeout(() => {
            const current = S.chat().retryState
            if (current?.succeeded) S.chat().clearRetryState()
          }, 1500)
        }
        // A permission approval resumed the turn — if nothing else is pending
        // the dot goes back to purple.
        {
          const chat = S.chat()
          const anyPending = chat.pendingPermission || chat.pendingAskUser || chat.pendingPlanApproval
            || chat.permissionQueue.length > 0
          if (!anyPending && chat.isStreaming) setRunStatus('running')
        }
        // Increment round counter for file ops grouping
        incrementRound()
        if (!data.content) break
        // Stamp arrival time so a thinking block can render "Thought for Xs"
        // once it's finished (duration = gap before the block appeared).
        const blockArrivalTs = Date.now()
        const assistantProcessGroupId = nextProcessGroupId()
        const assistantBlocks = data.content
          .filter((b) => b.type === 'thinking' || b.type === 'text')
          .map((b) => ({ ...b, startTime: blockArrivalTs, processGroupId: assistantProcessGroupId }))

        // Subagent assistant frame: route content to the subagent's bucket
        // keyed by parent_tool_use_id instead of the main thread.
        if (data.parent_tool_use_id) {
          if (assistantBlocks.length > 0) {
            S.chat().appendToSubagentContent(data.parent_tool_use_id, assistantBlocks)
          }
          break
        }

        if (lastMsg && lastMsg.role === 'assistant') {
          const newContent = [...lastMsg.content, ...assistantBlocks]
          S.chatSet({
            messages: [
              ...msgs.slice(0, lastIdx),
              { ...lastMsg, content: newContent },
            ],
          })
        }
        break
      }

      case 'tool_use': {
        if (!data.content) break
        const toolProcessGroupId = nextProcessGroupId()
        const toolBlocks = data.content
          .filter((b) => b.type === 'tool_use')
          .map((b) => {
            const buffered = pendingHookEvents.get(b.id)
            const base = { ...b, status: 'running', startTime: Date.now(), processGroupId: toolProcessGroupId }
            if (buffered && buffered.length) {
              base.metadata = { ...(base.metadata || {}), hookEvents: buffered }
              pendingHookEvents.delete(b.id)
            }
            return base
          })

        const sdkTaskBlocks = toolBlocks.filter((block) => isSdkTaskToolName(block.name))
        for (const block of sdkTaskBlocks) {
          hiddenToolIds.add(block.id)
          recordSdkTaskToolUse(block)
        }
        if (sdkTaskBlocks.length > 0) fxShowCanvas('tasks')

        for (const block of toolBlocks) {
          if (isGeneratedToolName(block.name)) generatedToolIds.add(block.id)
          if (FILE_TOOL_NAMES.has(block.name)) {
            if (!isPlanWriteTool(block)) {
              const tab = fileTabFromToolUse(block, FILE_SOURCE_CURRENT)
              if (tab) pendingToolFileTabs.set(block.id, tab)
            }
          } else {
            openToolFileInBrowser(block, {
              activate: block.name === 'Read' || Boolean(data.parent_tool_use_id),
            })
          }
        }

        // Subagent tool_use frame: route tool_use blocks into the subagent's
        // bucket so they render nested inside their SubagentFrame.
        if (data.parent_tool_use_id) {
          const visibleSubagentBlocks = toolBlocks.filter(
            (block) => !isSdkTaskToolName(block.name),
          )
          if (visibleSubagentBlocks.length > 0) {
            S.chat().appendToSubagentContent(data.parent_tool_use_id, visibleSubagentBlocks)
          }
          break
        }

        if (lastMsg && lastMsg.role === 'assistant') {
          const TASK_TOOLS = [
            'TaskOutput', 'TaskStop',
            // OpenClaw delegations — render as live canvas tasks, not inline cards
            'delegate_to_openclaw',
            'mcp__priva_openclaw__delegate_to_openclaw',
          ]
          const messageBlocks = []
          let reconciledContent = lastMsg.content

          for (const block of toolBlocks) {
            // Claude Agent SDK task management is represented exclusively by
            // the Composer capsule and the Canvas aggregate tracker.
            if (isSdkTaskToolName(block.name)) continue

            // Workflow → live multi-phase status card. Seed the workflow store
            // (idempotent — task_started may arrive before or after this),
            // render inline as a WorkflowCard, and reveal the canvas mirror.
            if (block.name === 'Workflow') {
              S.workflow().ensureWorkflow(block.id, {
                script: block.input?.script,
                workflowName: block.input?.name,
                status: 'pending',
              })
              fxShowCanvas('tasks')
              messageBlocks.push(block)
              continue
            }

            // AskUserQuestion → interactive card in chat (not a regular tool card)
            if (block.name === 'AskUserQuestion' && block.input?.questions) {
              hiddenToolIds.add(block.id)
              askUserToolIds.add(block.id)
              const existingIndex = findMatchingAskUserBlockIndex(reconciledContent, {
                toolUseId: block.id,
                questions: block.input.questions,
              })
              const existingBlock = existingIndex >= 0 ? reconciledContent[existingIndex] : null
              const askBlock = {
                ...existingBlock,
                type: 'ask_user',
                id: block.id,
                toolUseId: block.id,
                questions: block.input.questions,
                status: existingBlock?.status || 'pending',
                processGroupId: existingBlock?.processGroupId || block.processGroupId,
              }
              if (existingIndex >= 0) {
                reconciledContent = reconciledContent.map((item, index) => (
                  index === existingIndex ? askBlock : item
                ))
              } else {
                messageBlocks.push(askBlock)
              }
              S.chat().setPendingAskUser({
                toolUseId: block.id,
                questions: block.input.questions,
                ...(existingBlock?._permissionRequestId
                  ? { _permissionRequestId: existingBlock._permissionRequestId }
                  : {}),
              })
              setRunStatus('attention')
              continue
            }

            // BashOutput → hidden, output appends to parent background Bash task
            if (block.name === 'BashOutput' || block.name === 'TaskOutput') {
              hiddenToolIds.add(block.id)
              if (block.name === 'BashOutput' && block.input?.bash_id) {
                const tasks = S.tasks().tasks
                const parentId = Object.keys(tasks).find(
                  (k) => tasks[k].shellId === block.input.bash_id
                )
                if (parentId) {
                  updateTask(parentId, { lastBashOutputId: block.id })
                }
              }
              continue
            }

            // KillBash → hidden, marks parent task as killed
            if (block.name === 'KillBash') {
              hiddenToolIds.add(block.id)
              continue
            }

            // TodoWrite → render inline via TodoWriteCard AND mirror in Canvas.
            if (block.name === 'TodoWrite') {
              todoWriteIds.add(block.id)
              setTodoWriteInfo({
                tool_use_id: block.id,
                name: block.name,
                input: block.input,
                status: 'running',
                startTime: Date.now(),
              })
              messageBlocks.push(block)
              continue
            }

            // Plan file Write → route to PLAN canvas tab instead of FILES
            if (isPlanWriteTool(block)) {
              hiddenToolIds.add(block.id)
              const planContent = block.input.content
              const planFilePath = block.input.file_path
              uiFx(
                (ui) => {
                  ui.setPlanContent(planContent, planFilePath)
                  ui.showCanvas()
                  ui.setActiveCanvasTab('plan')
                },
                { planContent, planFilePath, canvasVisible: true, activeCanvasTab: 'plan' },
              )
              continue
            }

            // ExitPlanMode → hidden, handled via permission_request
            if (block.name === 'ExitPlanMode') {
              hiddenToolIds.add(block.id)
              continue
            }

            // File operation tools → FILES panel (hidden from messages)
            const FILE_OP_TOOLS = ['Write', 'Edit']
            if (FILE_OP_TOOLS.includes(block.name)) {
              hiddenToolIds.add(block.id)
              addFileOp({
                id: block.id,
                type: block.name.toLowerCase(),
                filePath: block.input?.file_path || '',
                status: 'running',
                startTime: Date.now(),
                input: block.input,
                content: null,
                originalFile: null,
                structuredPatch: null,
                toolUseResult: null,
              })
              fxShowCanvas('changes')
              // Emit a per-file clickable indicator in the message flow.
              // Clicking selects this specific fileOp in the canvas.
              messageBlocks.push({
                type: 'file_ref',
                id: `file-ref-${block.id}`,
                fileOpId: block.id,
                name: block.name,
                filePath: block.input?.file_path || '',
                processGroupId: block.processGroupId,
              })
              continue
            }

            // Generated file registration → FILES panel (hidden from messages)
            if (isGeneratedToolName(block.name)) {
              hiddenToolIds.add(block.id)
              generatedToolIds.add(block.id)
              const generatedPaths = getGeneratedInputPaths(block.input)

              generatedPaths.forEach((filePath) => {
                // Auto-open in File Browser at tool_use time. The matching
                // tool_result event later re-opens with mime/size/extension
                // — fileBrowserStore.openFile merges into the same tab.
                openFileBrowserTab({
                  filePath,
                  name: fileNameFromPath(filePath),
                  source: GENERATED_TOOL_LABEL,
                  browserSource: FILE_SOURCE_CURRENT,
                  sourceTool: GENERATED_TOOL_LABEL,
                  toolUseId: block.id,
                })
              })

              fxShowCanvas('file-browser')
              continue
            }

            // Background Bash / TaskOutput / TaskStop / OpenClaw delegations
            // still get mirrored into the (now-hidden) taskStore for live
            // shell-output tracking, but we ALSO render them inline as
            // regular tool cards (or SubagentFrame for Agent/Task).
            const isBackgroundBash = block.input?.run_in_background === true
            const isShellTracked = isBackgroundBash || TASK_TOOLS.includes(block.name)

            if (isShellTracked) {
              const isOpenClawDelegation =
                block.name === 'delegate_to_openclaw' ||
                block.name === 'mcp__priva_openclaw__delegate_to_openclaw'
              const description =
                isOpenClawDelegation
                  ? `OpenClaw → ${block.input?.agent_id || 'default'}: ${block.input?.task || ''}`
                  : block.input?.description || block.input?.command || block.name
              addTask({
                tool_use_id: block.id,
                name: block.name,
                input: block.input,
                status: 'running',
                startTime: Date.now(),
                description,
              })
            }

            // All tools (including Agent/Task/TodoWrite/Bash) render inline.
            messageBlocks.push(block)
          }

          // Add blocks to message content
          if (messageBlocks.length > 0 || sdkTaskBlocks.length > 0) {
            const newContent = [...reconciledContent, ...messageBlocks]
            S.chatSet({
              messages: [
                ...msgs.slice(0, lastIdx),
                {
                  ...lastMsg,
                  content: newContent,
                  ...(sdkTaskBlocks.length > 0 ? { hasSdkTaskActivity: true } : {}),
                },
              ],
            })
          }
        }
        break
      }

      case 'tool_result': {
        // Handle compact tool_result events (summary + completion marker)
        // During compacting, tool_results with no tool_use_id / parent_tool_use_id=null carry summary or completion
        if (S.chat().isCompacting) {
          console.debug('[SSE][compact] tool_result during compacting:', JSON.stringify(data).slice(0, 500))
          // Extract text content from the event — could be on data directly or inside data.content blocks
          let compactText = null
          if (typeof data.content === 'string') {
            compactText = data.content
          } else if (Array.isArray(data.content)) {
            for (const rb of data.content) {
              if (rb && typeof rb.content === 'string' && (!rb.tool_use_id)) {
                compactText = rb.content
                break
              }
            }
          }
          // Also check top-level: compact tool_results have parent_tool_use_id === null or absent tool_use_id
          const isCompactResult = data.parent_tool_use_id === null || data.parent_tool_use_id === undefined
          const hasNoToolId = !data.tool_use_id && (!Array.isArray(data.content) || !data.content.some((rb) => rb && rb.tool_use_id))

          if (compactText && (isCompactResult || hasNoToolId)) {
            if (compactText.includes('<local-command-stdout>')) {
              // Completion marker — end compacting
              setCompacting(false)
              break
            } else {
              // Summary text — attach to the compact system message
              const currentMsgs = S.chat().messages
              const compactIdx = [...currentMsgs].reverse().findIndex(
                (m) => m.role === 'system' && m.type === 'compact'
              )
              if (compactIdx >= 0) {
                const realIdx = currentMsgs.length - 1 - compactIdx
                const updated = [...currentMsgs]
                updated[realIdx] = { ...updated[realIdx], summary: compactText }
                S.chatSet({ messages: updated })
              }
              break
            }
          }
        }

        const currentTasks = S.tasks().tasks
        const allResultBlocks = []

        // Collect tool_result blocks from data.content
        if (data.content) {
          const blocks = Array.isArray(data.content) ? data.content : [data.content]
          for (const rb of blocks) {
            if (rb && rb.type === 'tool_result' && rb.tool_use_id) {
              allResultBlocks.push(rb)
            }
          }
        }

        // Process all result blocks
        for (const rb of allResultBlocks) {
          const isToolResultError = isErroredToolResult(rb, data.tool_use_result)
          recordSdkTaskToolResult(rb.tool_use_id, rb, data.tool_use_result)

          // Invalid AskUserQuestion input never reached the permission UI.
          // Remove its optimistic live block so it neither remains pending nor
          // contributes to the response summary.
          if (
            askUserToolIds.has(rb.tool_use_id)
            && isAskUserInputValidationError(rb, data.tool_use_result)
          ) {
            const currentChat = S.chat()
            let changed = false
            const nextMessages = currentChat.messages.map((message) => {
              if (message.role !== 'assistant' || !Array.isArray(message.content)) return message
              const nextContent = message.content.filter((block) => !(
                block?.type === 'ask_user' && block.toolUseId === rb.tool_use_id
              ))
              if (nextContent.length === message.content.length) return message
              changed = true
              return { ...message, content: nextContent }
            })
            if (changed) S.chatSet({ messages: nextMessages })
            if (currentChat.pendingAskUser?.toolUseId === rb.tool_use_id) {
              S.chat().clearPendingAskUser()
            }
          }
          const pendingFileTab = pendingToolFileTabs.get(rb.tool_use_id)
          const handledPendingFile = Boolean(pendingFileTab)
          if (pendingFileTab) {
            openFileBrowserTab(pendingFileTab)
            if (pendingFileTab.sourceTool === 'Read') fxShowCanvas('file-browser')
            pendingToolFileTabs.delete(rb.tool_use_id)
          }

          // Update message flow only for visible tools
          if (!hiddenToolIds.has(rb.tool_use_id)) {
            updateToolResult(rb.tool_use_id, rb)
          }

          // Complete canvas task if tracked
          if (currentTasks[rb.tool_use_id]) {
            const taskEntry = currentTasks[rb.tool_use_id]
            const updateData = {
              status: rb.is_error ? 'error' : 'success',
              endTime: Date.now(),
              result: rb,
              toolUseResult: data.tool_use_result,
            }
            // Background Bash: extract shellId from result
            if (taskEntry.name === 'Bash' && taskEntry.input?.run_in_background) {
              const tur = data.tool_use_result || {}
              if (tur.shellId || tur.shell_id) {
                updateData.shellId = tur.shellId || tur.shell_id
                updateData.shellStatus = 'running'
                updateData.liveOutput = ''
                // Override status back to running since bg bash continues
                updateData.status = 'running'
                delete updateData.endTime
              }
            }
            updateTask(rb.tool_use_id, updateData)
          }

          // BashOutput result → append output to parent task
          {
            const tur = data.tool_use_result || {}
            const bashId = tur.bash_id || tur.bashId
            if (bashId) {
              const tasks = S.tasks().tasks
              const parentId = Object.keys(tasks).find(
                (k) => tasks[k].shellId === bashId
              )
              if (parentId) {
                const prevOutput = tasks[parentId].liveOutput || ''
                const newOutput = typeof rb.content === 'string' ? rb.content : ''
                const shellStatus = tur.status || tur.shellStatus || 'running'
                // Cap retained shell output — long-running background
                // commands can otherwise grow this string unboundedly.
                let liveOutput = prevOutput + newOutput
                if (liveOutput.length > MAX_LIVE_OUTPUT) {
                  liveOutput = '…[truncated]\n' + liveOutput.slice(liveOutput.length - MAX_LIVE_OUTPUT)
                }
                updateTask(parentId, {
                  liveOutput,
                  shellStatus: shellStatus === 'completed' || shellStatus === 'done' ? 'completed' : shellStatus === 'failed' ? 'failed' : 'running',
                })
                // If shell completed, mark parent task done
                if (shellStatus === 'completed' || shellStatus === 'done') {
                  updateTask(parentId, { status: 'success', endTime: Date.now() })
                }
              }
            }
          }

          // KillBash result → mark parent task as killed
          {
            const tur = data.tool_use_result || {}
            const shellId = tur.shell_id || tur.shellId
            if (shellId) {
              const tasks = S.tasks().tasks
              const parentId = Object.keys(tasks).find(
                (k) => tasks[k].shellId === shellId
              )
              if (parentId) {
                updateTask(parentId, {
                  shellStatus: 'killed',
                  status: 'error',
                  endTime: Date.now(),
                })
              }
            }
          }

          // Complete file operation if tracked
          const currentFileOps = S.fileOps().fileOps
          const matchingFileOps = currentFileOps.filter((op) =>
            op.id === rb.tool_use_id ||
            (op.type === 'generated' && op.toolUseId === rb.tool_use_id)
          )
          if (matchingFileOps.length > 0) {
            const tur = data.tool_use_result || {}

            for (const op of matchingFileOps) {
              const isErrorResult = isToolResultError || isErroredToolResult(rb, tur)
              S.fileOps().updateFileOp(op.id, {
                status: isErrorResult ? 'error' : 'success',
                endTime: Date.now(),
                content: tur.content || tur.new_content || null,
                originalFile: tur.original_file || tur.originalFile || null,
                structuredPatch: tur.structured_patch || tur.structuredPatch || null,
                resultContent: typeof rb.content === 'string' ? rb.content : null,
                toolUseResult: tur,
              })
              if ((op.type === 'write' || op.type === 'edit') && !handledPendingFile) {
                if (op.filePath) {
                  const sourceTool = op.type === 'edit' ? 'Edit' : 'Write'
                  openFileBrowserTab({
                    filePath: op.filePath,
                    name: fileNameFromPath(op.filePath),
                    source: browserSourceLabel(FILE_SOURCE_CURRENT),
                    browserSource: FILE_SOURCE_CURRENT,
                    sourceTool,
                    toolUseId: op.id,
                  })
                }
              }
            }

            const generatedFileOps = matchingFileOps
              .filter((op) => op.type === 'generated')
              .sort((a, b) => (a.sourceIndex || 0) - (b.sourceIndex || 0))
            const generatedFiles = Array.isArray(tur.files) ? tur.files : []

            generatedFileOps.forEach((op, index) => {
              const file = generatedFiles[index]
              if (!file) return
              S.fileOps().updateFileOp(op.id, {
                filePath: file.path || op.filePath,
                relativePath: file.relative_path || null,
                mimeType: file.mime_type || null,
                size: typeof file.size === 'number' ? file.size : null,
                extension: file.extension || null,
              })
            })
          }

          if (generatedToolIds.has(rb.tool_use_id)) {
            const tur = data.tool_use_result || {}
            const files = Array.isArray(tur.files) ? tur.files : []
            fileTabsFromGeneratedFiles(files, FILE_SOURCE_CURRENT, rb.tool_use_id)
              .forEach((file) => openFileBrowserTab(file))
            if (files.length > 0) {
              fxShowCanvas('file-browser')
            }
          }

          // Update TodoWrite info on result
          if (todoWriteIds.has(rb.tool_use_id)) {
            setTodoWriteInfo({
              status: rb.is_error ? 'error' : 'success',
              endTime: Date.now(),
              result: rb,
            })
          }

          // Extract TodoWrite todos
          if (todoWriteIds.has(rb.tool_use_id)) {
            const newTodos = extractTodos(rb, data.tool_use_result)
            if (newTodos) {
              setTodos(newTodos)
              fxShowCanvas(null)
            }
          }
        }

        // Fallback: extract TodoWrite todos from tool_use_result dict
        if (data.tool_use_result) {
          const tur = data.tool_use_result
          const todoItems = tur.newTodos || tur.todos || tur.new_todos
          if (Array.isArray(todoItems) && S.tasks().todos.length === 0) {
            setTodos(todoItems)
            fxShowCanvas(null)
          }
        }
        break
      }

      case 'result': {
        // Set duration on the assistant message
        const finalMsgs = [...S.chat().messages]
        const finalIdx = finalMsgs.length - 1
        if (finalMsgs[finalIdx]?.role === 'assistant') {
          const serverDuration = Number(data.duration_ms)
          finalMsgs[finalIdx] = {
            ...finalMsgs[finalIdx],
            duration: Number.isFinite(serverDuration) && serverDuration >= 0
              ? serverDuration
              : Date.now() - streamStartTime,
            inputTokens: data.usage?.input_tokens,
            outputTokens: data.usage?.output_tokens,
            agentLoops: data.num_turns,
            resultReceived: true,
            resultText: typeof data.result === 'string' ? data.result : null,
            resultIsError: data.is_error === true,
            resultSubtype: data.subtype || null,
          }
          S.chatSet({ messages: finalMsgs })
        }
        // ResultMessage can arrive before the transport has actually closed.
        // Keep the UI in streaming mode until onComplete so any late
        // assistant/tool/system events still render under the active state.
        if (data.session_id) {
          // sessionIdAtSend (not the live chat state) decides "new conversation"
          // — system.init already assigned chat.sessionId before this result.
          const isNewConversation = !sessionIdAtSend
          adoptSessionId(data.session_id)
          // First assignment of a brand-new conversation: persist its add_dirs
          // to the server-side sidecar so a resume on any device recovers them.
          if (isNewConversation) {
            const dirsNow = S.chat().addDirs
            if (dirsNow && dirsNow.length > 0) {
              setSessionAddDirs(data.session_id, dirsNow).catch(() => {})
            }
          }
        }
        setCompacting(false)
        setLastResult(data)
        // Update THIS conversation's sidebar row (never the active session's).
        if (data.session_id) {
          useSidebarStore.getState().updateSession(rt.meta.sidebarRowId || data.session_id, {
            sessionId: data.session_id,
            cost: data.total_cost_usd,
            duration: data.duration_ms,
          })
        }
        break
      }

      case 'task_started': {
        // Workflow task → dedicated workflow store (keyed accumulation).
        if (data.task_type === 'local_workflow') {
          const inner = data.data || {}
          S.workflow().applyStart(data.tool_use_id, {
            taskId: data.task_id,
            description: data.description ?? inner.description,
            workflowName: inner.workflow_name ?? data.workflow_name,
            script: inner.prompt ?? data.prompt,
          })
          fxShowCanvas('tasks')
          break
        }
        // Backend sends task_started as its own SSE event type
        // Fields are flat: data.tool_use_id, data.task_id, data.description, etc.
        const toolUseId = data.tool_use_id
        const currentTasks = S.tasks().tasks
        if (toolUseId && currentTasks[toolUseId]) {
          // Enrich existing canvas task (created from tool_use)
          updateTask(toolUseId, {
            task_id: data.task_id,
            description: data.description || currentTasks[toolUseId].description,
            task_type: data.task_type,
            status: 'running',
          })
        } else {
          // task_started fires for every tool the runner tracks as a "task".
          // A `local_bash` task is a plain shell command: foreground bash is
          // already shown inline as a Bash tool card (no canvas node needed),
          // and background bash was already tracked at tool_use time (enriched
          // by the updateTask branch above). Only real subagent/Task/workflow
          // tasks belong in the canvas — a local_bash must NOT spawn a node or
          // force the canvas open (that opened the canvas on every bash turn).
          const id = toolUseId || data.task_id
          if (id && data.task_type !== 'local_bash') {
            addTask({
              tool_use_id: id,
              name: 'Task',
              description: data.description || 'Task',
              status: 'running',
              startTime: Date.now(),
              task_id: data.task_id,
              task_type: data.task_type,
            })
            fxShowCanvas(null)
          }
        }
        break
      }

      case 'task_progress': {
        // Workflow progress → keyed accumulation in the workflow store.
        const inner = data.data || {}
        const wp = Array.isArray(inner.workflow_progress) ? inner.workflow_progress
          : Array.isArray(data.workflow_progress) ? data.workflow_progress : null
        if (wp) {
          S.workflow().applyProgress(data.tool_use_id, {
            taskId: data.task_id,
            usage: data.usage ?? inner.usage,
            summary: inner.summary ?? data.summary,
            lastToolName: data.last_tool_name ?? inner.last_tool_name,
            workflowProgress: wp,
          })
          fxShowCanvas(null)
          break
        }
        const toolUseId = data.tool_use_id
        const taskId = data.task_id
        const currentTasks = S.tasks().tasks
        const id = (toolUseId && currentTasks[toolUseId]) ? toolUseId
          : Object.keys(currentTasks).find((k) => currentTasks[k].task_id === taskId)
        if (id) {
          updateTask(id, {
            progress: data.data,
            description: data.description || currentTasks[id]?.description,
            last_tool_name: data.last_tool_name,
          })
        }
        break
      }

      case 'task_notification': {
        // Workflow completion (authoritative) → flip the workflow card.
        const wfId = S.workflow().resolveId(data.tool_use_id, data.task_id)
        if (wfId) {
          S.workflow().markCompletion(wfId, {
            rawStatus: data.status,
            source: 'task_notification',
            summary: data.summary,
          })
          break
        }
        const toolUseId = data.tool_use_id
        const taskId = data.task_id
        const currentTasks = S.tasks().tasks
        const id = (toolUseId && currentTasks[toolUseId]) ? toolUseId
          : Object.keys(currentTasks).find((k) => currentTasks[k].task_id === taskId)
        if (id) {
          const status = data.status || 'success'
          updateTask(id, {
            status: status === 'completed' ? 'success' : status,
            summary: data.summary,
            endTime: Date.now(),
          })
        }
        break
      }

      case 'hook_event': {
        // Lifecycle pings from include_hook_events. Only PreToolUse /
        // PostToolUse flow over the wire; others are dropped server-side.
        const payload = data?.data || {}
        const toolUseId = payload.tool_use_id || payload.toolUseId
        if (!toolUseId) break
        const merged = mergeHookEventIntoBlock(toolUseId, data)
        if (!merged) {
          const buf = pendingHookEvents.get(toolUseId) || []
          const evtKey = data.uuid || `${data.hook_event_name}-${data.subtype}-${buf.length}`
          const filtered = buf.filter((e) => (e.uuid || '') !== (data.uuid || '___none'))
          pendingHookEvents.set(toolUseId, [...filtered, { ...data, _key: evtKey }])
        }
        break
      }

      case 'system': {
        // SystemMessage with subtype — task events may arrive as system events
        // with fields nested under data.data
        const subtype = data.subtype
        if (subtype === 'task_started') {
          const nested = data.data || {}
          // Workflow task → dedicated workflow store.
          if (nested.task_type === 'local_workflow') {
            const inner = nested.data || {}
            S.workflow().applyStart(nested.tool_use_id, {
              taskId: nested.task_id,
              description: nested.description ?? inner.description,
              workflowName: inner.workflow_name ?? nested.workflow_name,
              script: inner.prompt ?? nested.prompt,
            })
            fxShowCanvas('tasks')
            return
          }
          const toolUseId = nested.tool_use_id
          const currentTasks = S.tasks().tasks
          if (toolUseId && currentTasks[toolUseId]) {
            updateTask(toolUseId, {
              task_id: nested.task_id,
              description: nested.description || currentTasks[toolUseId].description,
              task_type: nested.task_type,
              status: 'running',
            })
          } else {
            // See the top-level task_started handler: a `local_bash` task is a
            // plain shell command already shown inline (or tracked at tool_use
            // time for background bash) — it must NOT spawn a canvas node or
            // force the canvas open. Only real subagent/Task/workflow tasks do.
            const id = toolUseId || nested.task_id
            if (id && nested.task_type !== 'local_bash') {
              addTask({
                tool_use_id: id,
                name: 'Task',
                description: nested.description || 'Task',
                status: 'running',
                startTime: Date.now(),
                task_id: nested.task_id,
                task_type: nested.task_type,
              })
              fxShowCanvas(null)
            }
          }
        } else if (subtype === 'task_progress') {
          const nested = data.data || {}
          const inner = nested.data || {}
          // Workflow progress → keyed accumulation in the workflow store.
          const wp = Array.isArray(inner.workflow_progress) ? inner.workflow_progress
            : Array.isArray(nested.workflow_progress) ? nested.workflow_progress : null
          if (wp) {
            S.workflow().applyProgress(nested.tool_use_id, {
              taskId: nested.task_id,
              usage: nested.usage ?? inner.usage,
              summary: inner.summary ?? nested.summary,
              lastToolName: nested.last_tool_name ?? inner.last_tool_name,
              workflowProgress: wp,
            })
            fxShowCanvas(null)
            return
          }
          const toolUseId = nested.tool_use_id
          const taskId = nested.task_id
          const currentTasks = S.tasks().tasks
          const id = (toolUseId && currentTasks[toolUseId]) ? toolUseId
            : Object.keys(currentTasks).find((k) => currentTasks[k].task_id === taskId)
          if (id) {
            updateTask(id, {
              progress: nested.data,
              description: nested.description || currentTasks[id]?.description,
              last_tool_name: nested.last_tool_name,
            })
          }
        } else if (subtype === 'task_notification') {
          const nested = data.data || {}
          // Workflow completion (authoritative) → flip the workflow card.
          const wfId = S.workflow().resolveId(nested.tool_use_id, nested.task_id)
          if (wfId) {
            S.workflow().markCompletion(wfId, {
              rawStatus: nested.status,
              source: 'task_notification',
              summary: nested.summary,
            })
            return
          }
          const toolUseId = nested.tool_use_id
          const taskId = nested.task_id
          const currentTasks = S.tasks().tasks
          const id = (toolUseId && currentTasks[toolUseId]) ? toolUseId
            : Object.keys(currentTasks).find((k) => currentTasks[k].task_id === taskId)
          if (id) {
            const status = nested.status || 'success'
            updateTask(id, {
              status: status === 'completed' ? 'success' : status,
              summary: nested.summary,
              endTime: Date.now(),
            })
          }
        } else if (subtype === 'task_updated') {
          // Workflow completion may arrive ONLY as task_updated (no tool_use_id)
          // — correlate by task_id. Falls back to the generic taskStore.
          const nested = data.data || {}
          const patch = nested.patch || data.patch || {}
          const taskId = nested.task_id || data.task_id
          const wfId = S.workflow().resolveId(null, taskId)
          if (wfId) {
            S.workflow().markCompletion(wfId, {
              rawStatus: patch.status,
              source: 'task_updated',
              endedAt: patch.end_time,
            })
            return
          }
          const currentTasks = S.tasks().tasks
          const id = Object.keys(currentTasks).find((k) => currentTasks[k].task_id === taskId)
          // Only a terminal patch.status finishes a generic task. task_updated
          // fires on every state change (pending/running/paused) and may omit
          // status entirely — never default an unknown status to 'success' or
          // stamp endTime on a still-running task.
          if (id && isTerminalRawStatus(patch.status)) {
            updateTask(id, {
              status: rawToTaskStatus(patch.status),
              endTime: patch.end_time || Date.now(),
            })
          }
        } else if (subtype === 'status') {
          console.debug('[SSE][compact] system status event:', JSON.stringify(data).slice(0, 500))
          const nested = data.data || {}
          if (nested.status === 'compacting') {
            setCompacting(true)
            addMessage({ role: 'system', type: 'compact', status: 'compacting', timestamp: Date.now() })
          }
        } else if (subtype === 'compact_boundary') {
          console.debug('[SSE][compact] compact_boundary event:', JSON.stringify(data).slice(0, 500))
          const nested = data.data || {}
          const metadata = nested.compact_metadata || {}
          // Find the compacting system message and update it to complete
          const currentMsgs = S.chat().messages
          const compactIdx = currentMsgs.findIndex(
            (m) => m.role === 'system' && m.type === 'compact' && m.status === 'compacting'
          )
          if (compactIdx >= 0) {
            const updated = [...currentMsgs]
            updated[compactIdx] = {
              ...updated[compactIdx],
              status: 'complete',
              compactMetadata: {
                trigger: metadata.trigger || 'manual',
                preTokens: metadata.pre_tokens || 0,
              },
            }
            S.chatSet({ messages: updated })
          }
        } else if (subtype === 'init') {
          const nested = data.data || {}
          if (nested.session_id) adoptSessionId(nested.session_id)
        }
        break
      }

      case 'queued': {
        // Backend accepted the mid-stream queue frame. Nothing to do —
        // the bubble is already rendered in 'pending' status; leave it
        // there until 'queue_flush' promotes it.
        break
      }

      case 'queue_flush': {
        // Backend is about to deliver this queued message as a new turn.
        // Promote the dim queued row into a normal user bubble so the
        // history shows the question was actually asked.
        const qid = data?.id
        const qtext = data?.text
        const queued = S.chat().queuedUserMessages.find((m) => m.id === qid)
        const text = qtext || queued?.text || ''
        const images = queued?.images || []
        const attachmentsMeta = queued?.attachmentsMeta || null

        beginSdkTaskRound({ title: text || 'Image prompt', startedAt: Date.now() })
        const userMsg = { role: 'user', content: [], timestamp: Date.now() }
        for (const img of images) {
          userMsg.content.push({
            type: 'image',
            source: { type: 'base64', media_type: img.media_type, data: img.data },
            filename: img.filename,
          })
        }
        if (text) userMsg.content.push({ type: 'text', text })
        if (attachmentsMeta && attachmentsMeta.length > 0) userMsg.attachments = attachmentsMeta
        S.chat().addMessage(userMsg)
        S.chat().addMessage({
          role: 'assistant', content: [], timestamp: Date.now(),
        })
        S.chat().removeQueuedMessage(qid)
        break
      }

      case 'queue_cancelled': {
        // Backend confirmed removal — ensure local store is in sync (the UI
        // typically removes optimistically when the user clicks cancel).
        if (data?.id) S.chat().removeQueuedMessage(data.id)
        break
      }

      case 'retry_attempt': {
        setRetryState({
          attempt: data.attempt,
          max: data.max_attempts,
          delaySeconds: data.delay_seconds || 0,
          errorCode: data.error_code || null,
          message: data.message || null,
        })
        break
      }

      case 'retry_exhausted': {
        clearRetryState()
        setStreaming(false)
        setStreamAbort(null)
        S.chat().abortRunningTools()
        S.tasks().abortRunningTasks()
        S.workflow().abortRunning()
        if (lastMsg && lastMsg.role === 'assistant') {
          S.chatSet({
            messages: [
              ...msgs.slice(0, lastIdx),
              {
                ...lastMsg,
                is_synthetic: true,
                error: true,
                errorInfo: {
                  code: data.error_code || 'unknown',
                  attempts: data.attempts,
                  message: data.message,
                  raw_detail: data.raw_detail || null,
                  api_error_status: data.api_error_status ?? null,
                },
              },
            ],
          })
        }
        markCurrentResponseInterrupted(getSlice(rt.key, 'chat'))
        statusStore().setStatus(rt.key, terminalStatusFor(rt.key))
        pushToast({
          level: data.api_error_status === 429 ? 'warning' : 'error',
          title: i18n.t('chat.upstreamErrorTitle'),
          body: data.message || i18n.t('chat.retriesExhausted'),
        })
        break
      }

      case 'stream_error': {
        clearRetryState()
        setStreaming(false)
        setStreamAbort(null)
        S.chat().abortRunningTools()
        S.tasks().abortRunningTasks()
        S.workflow().abortRunning()
        if (lastMsg && lastMsg.role === 'assistant') {
          S.chatSet({
            messages: [
              ...msgs.slice(0, lastIdx),
              {
                ...lastMsg,
                is_synthetic: true,
                error: true,
                errorInfo: {
                  code: data.code || 'unknown',
                  attempts: 1,
                  message: data.message || 'Stream error',
                  api_error_status: data.api_error_status ?? null,
                },
              },
            ],
          })
        }
        markCurrentResponseInterrupted(getSlice(rt.key, 'chat'))
        statusStore().setStatus(rt.key, terminalStatusFor(rt.key))
        pushToast({
          level: data.api_error_status === 429 ? 'warning' : 'error',
          title: `${i18n.t('chat.streamErrorTitle')}${data.code ? ` (${data.code})` : ''}`,
          body: data.message || i18n.t('chat.streamEnded'),
        })
        break
      }

      case 'rate_limit_status': {
        // Informational — CLI is auto-handling 429s; don't retry, just notify.
        const status = data.status || 'unknown'
        if (status === 'allowed' || status === 'allowed_warning') {
          pushToast({
            level: 'warning',
            title: i18n.t('chat.rateLimitedTitle'),
            body: data.resets_at ? i18n.t('chat.rateLimitResets', { time: data.resets_at }) : undefined,
          })
        }
        break
      }

      case 'error': {
        setStreaming(false)
        setStreamAbort(null)
        S.chat().abortRunningTools()
        S.tasks().abortRunningTasks()
        S.workflow().abortRunning()
        // Add error to last message
        if (lastMsg && lastMsg.role === 'assistant') {
          S.chatSet({
            messages: [
              ...msgs.slice(0, lastIdx),
              {
                ...lastMsg,
                is_synthetic: true,
                error: true,
                errorInfo: {
                  code: 'transport',
                  attempts: 1,
                  message: data.message,
                },
              },
            ],
          })
        }
        markCurrentResponseInterrupted(getSlice(rt.key, 'chat'))
        statusStore().setStatus(rt.key, terminalStatusFor(rt.key))
        pushToast({
          level: 'error',
          title: i18n.t('connection.errorTitle'),
          body: data.message || i18n.t('connection.lost'),
        })
        break
      }
    }
  }

  const onComplete = () => {
    const { streamGeneration, messages: doneMsgs } = S.chat()
    if (streamGeneration !== streamGen) return
    setStreaming(false)
    setStreamAbort(null)
    setWsSendPermission(null)
    S.chat().setQueueSender(null)
    S.chat().clearQueuedMessages()
    // Stream-end fallback: finalize any still-'running' workflow honestly
    // (completed only when all agents finished; else failed/stopped — never a
    // false green DONE on a clean close; never touches 'pending').
    S.workflow().finalizeRunning()
    // Successful turn: release the retained prompt payload (multi-MB image
    // base64). Failed turns keep it so ErrorBlock [Retry] can resend.
    const lastAssistant = [...doneMsgs].reverse().find((m) => m.role === 'assistant')
    if (lastAssistant && !lastAssistant.error) {
      S.chat().clearLastUserPrompt()
    }
    statusStore().setStatus(rt.key, terminalStatusFor(rt.key))
    useSidebarStore.getState().fetchSessions()
    // The recap for this turn is generated after the stream closes, so this
    // polls rather than reads once. Not awaited — nothing here depends on it.
    const { sessionId: doneSid, recapTurns } = S.chat()
    refreshSessionRecap(doneSid, S.chat, { knownTurns: recapTurns, poll: true })
  }

  // Connection banners belong to the session on screen; background sockets
  // reconnect silently (their terminal errors surface via toast + dot).
  const surfaceConnUi = () => getActiveKey() === rt.key

  if (attach) {
    const { abort, sendPermission, sendQueue, sendQueueCancel } = attachAgentRunWS(
      attach.sessionId, attach.sinceSeq ?? 0, onEvent, onComplete, { tabId }, surfaceConnUi,
    )
    setStreamAbort(abort)
    setWsSendPermission(sendPermission)
    S.chat().setQueueSender({ sendQueue, sendQueueCancel })
    return
  }

  const mcpServers = S.chat().mcpServers
  const { cwdDraft, addDirs } = S.chat()
  // cwd is honored for NEW conversations only; on resume the backend uses the
  // session's recorded cwd. add_dirs is always sent (request wins; the
  // hydrated set is re-sent on resume).
  const cwdForRun = sessionIdAtSend ? null : (cwdDraft || null)

  if (transport === 'ws') {
    const { abort, sendPermission, sendQueue, sendQueueCancel } = streamAgentRunWS(message, sessionIdAtSend, onEvent, permissionMode, onComplete, selectedModel, attachments, mcpServers, images, { tabId }, enableFileCheckpointing, cwdForRun, addDirs, surfaceConnUi)
    setStreamAbort(abort)
    setWsSendPermission(sendPermission)
    S.chat().setQueueSender({ sendQueue, sendQueueCancel })
  } else {
    const { abort } = streamAgentRun(message, sessionIdAtSend, onEvent, permissionMode, onComplete, selectedModel, attachments, mcpServers, images, enableFileCheckpointing, cwdForRun, addDirs)
    setStreamAbort(abort)
  }
}

/**
 * Phase 2: join a run that is still executing on the backend (page refresh /
 * second browser). The runtime should already hold the pre-run transcript
 * snapshot; the server replays the run's events from `sinceSeq`.
 */
export function attachToRunningSession(sessionId, opts = {}) {
  if (!sessionId) return
  const key = resolveRuntimeKey(sessionId) || sessionId
  const chatState = getSlice(ensureRuntime(key).key, 'chat').getState()
  if (chatState.isStreaming) return // already attached
  startStream({ key, attach: { sessionId, sinceSeq: opts.sinceSeq ?? 0 } })
}

export function useSSE() {
  const sendMessage = useCallback((message, permissionMode, attachments, attachmentsMeta, images) => {
    // Compose always targets the session on screen; the stream stays bound to
    // it even after the user switches away.
    startStream({ key: getActiveKey(), message, permissionMode, attachments, attachmentsMeta, images })
  }, [])

  const sendAnswer = useCallback(async (answerText, toolUseId, answerData) => {
    const { pendingAskUser } = useChatStore.getState()
    const isPermissionBased = pendingAskUser?._permissionRequestId

    // Update the ask_user block: status + persist selections/customInputs + answeredText
    const msgs = [...useChatStore.getState().messages]
    for (let i = msgs.length - 1; i >= 0; i--) {
      const msg = msgs[i]
      if (msg.role !== 'assistant') continue
      const newContent = msg.content.map((b) => {
        if (b.type === 'ask_user' && b.toolUseId === toolUseId) {
          return {
            ...b,
            status: 'answered',
            answeredText: answerText,
            answeredSelections: answerData?.selections || {},
            answeredCustomInputs: answerData?.customInputs || {},
          }
        }
        return b
      })
      const changed = newContent.some((b, j) => b !== msg.content[j])
      if (changed) {
        msgs[i] = { ...msg, content: newContent }
        useChatStore.setState({ messages: msgs })
        break
      }
    }
    useChatStore.getState().clearPendingAskUser()

    if (isPermissionBased) {
      // Route through permission: WS if available, otherwise POST
      const { streamId, wsSendPermission } = useChatStore.getState()
      const updatedInput = { questions: pendingAskUser.questions, answer: answerText }
      if (wsSendPermission) {
        wsSendPermission(isPermissionBased, 'allow', null, updatedInput)
      } else if (streamId) {
        try {
          await respondPermissionAPI(streamId, isPermissionBased, 'allow', null, updatedInput)
        } catch (err) {
          useToastStore.getState().pushToast({
            level: 'error',
            title: i18n.t('chat.permissionRespondFailed'),
            body: String(err?.message || err),
          })
        }
      }
      recomputeActiveStatus()
    } else {
      // Original flow: send the answer as a new message to resume the session
      sendMessage(answerText)
    }
  }, [sendMessage])

  const declineAskUser = useCallback(async () => {
    const { pendingAskUser } = useChatStore.getState()
    if (!pendingAskUser) return
    const toolUseId = pendingAskUser.toolUseId
    const isPermissionBased = pendingAskUser._permissionRequestId
    // Mark all pending ask_user blocks as 'declined'
    const msgs = [...useChatStore.getState().messages]
    for (let i = msgs.length - 1; i >= 0; i--) {
      const msg = msgs[i]
      if (msg.role !== 'assistant') continue
      const newContent = msg.content.map((b) => {
        if (b.type === 'ask_user' && b.toolUseId === toolUseId) {
          return { ...b, status: 'declined' }
        }
        return b
      })
      const changed = newContent.some((b, j) => b !== msg.content[j])
      if (changed) {
        msgs[i] = { ...msg, content: newContent }
        useChatStore.setState({ messages: msgs })
        break
      }
    }
    useChatStore.getState().clearPendingAskUser()
    // If permission-based, send deny: WS if available, otherwise POST
    if (isPermissionBased) {
      const { streamId, wsSendPermission } = useChatStore.getState()
      if (wsSendPermission) {
        wsSendPermission(isPermissionBased, 'deny', 'User skipped the question')
      } else if (streamId) {
        try {
          await respondPermissionAPI(streamId, isPermissionBased, 'deny', 'User skipped the question')
        } catch (err) {
          useToastStore.getState().pushToast({
            level: 'error',
            title: i18n.t('chat.permissionDeclineFailed'),
            body: String(err?.message || err),
          })
        }
      }
    }
    recomputeActiveStatus()
  }, [])

  const respondPermission = useCallback(async (requestId, decision, message, updatedInput) => {
    const { streamId, wsSendPermission } = useChatStore.getState()
    if (wsSendPermission) {
      wsSendPermission(requestId, decision, message, updatedInput)
    } else if (streamId) {
      try {
        await respondPermissionAPI(streamId, requestId, decision, message, updatedInput)
      } catch (err) {
        useToastStore.getState().pushToast({
          level: 'error',
          title: i18n.t('chat.permissionRespondFailed'),
          body: String(err?.message || err),
        })
      }
    } else {
      console.warn('[SSE] respondPermission: no streamId, skipping API call')
    }
    // Always resolve the permission UI regardless of API success
    useChatStore.getState().resolvePermission(requestId)
    recomputeActiveStatus()
  }, [])

  const stopStream = useCallback(() => stopActiveStream(), [])

  return { sendMessage, stopStream, sendAnswer, declineAskUser, respondPermission }
}
