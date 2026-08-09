import { createStore } from 'zustand/vanilla'
import safeStorage from '@shared/utils/safeStorage'
import { makeFacade, registerSliceFactory } from './runtime/registry'
import {
  agentLifecycleFromStatus,
  agentLifecycleToBlockStatus,
  getAgentDisplayId,
  getAgentResultInfo,
  isAgentToolName,
  normalizeAgentTaskNotification,
} from '../utils/agentToolLifecycle'

const CKPT_STORAGE_PREFIX = 'priva-ckpt:'
const REWIND_STORAGE_PREFIX = 'priva-rewind:'

// Monotonic counter for `_cid` — stable React list keys for live messages.
// 'c-' prefix keeps these distinct from sessionTransform's 's-' load-path ids.
// Module-scoped (shared across all session runtimes) so keys stay unique.
let cidCounter = 0
const withCid = (message) => (message && !message._cid ? { ...message, _cid: `c-${++cidCounter}` } : message)

const isUnfinishedStreamBlock = (block) => (
  typeof block?._streamState === 'string' && block._streamState !== 'complete'
)

function applyBlockPatches(blocks, patches) {
  let next = blocks
  let changed = false
  for (const patch of patches) {
    const index = next.findIndex((block) => block?._streamKey === patch.streamKey)
    if (index >= 0) {
      if (!changed) next = [...next]
      next[index] = { ...next[index], ...patch.block }
      changed = true
    } else {
      if (!changed) next = [...next]
      next.push(patch.block)
      changed = true
    }
  }
  return changed ? next : blocks
}

function mapStreamBlocks(blocks, mapper) {
  let changed = false
  const next = blocks.map((block) => {
    if (!isUnfinishedStreamBlock(block)) return block
    const mapped = mapper(block)
    if (mapped !== block) changed = true
    return mapped
  }).filter((block) => {
    if (block !== null) return true
    changed = true
    return false
  })
  return changed ? next : blocks
}

function timestampToMillis(value) {
  if (typeof value === 'number' && Number.isFinite(value)) {
    return value < 1_000_000_000_000 ? value * 1000 : value
  }
  const parsed = Date.parse(String(value || ''))
  return Number.isFinite(parsed) ? parsed : Date.now()
}

function withAgentToolResult(block, result, toolUseResult = null) {
  const enrichedResult = toolUseResult && !result?.tool_use_result
    ? { ...result, tool_use_result: toolUseResult }
    : result
  const resultInfo = getAgentResultInfo(enrichedResult, toolUseResult)

  // A terminal task notification is authoritative. Attach-replay can deliver
  // the earlier async launch result again after that terminal event.
  const existingLifecycle = agentLifecycleFromStatus(block.agentTaskStatus)
  if (existingLifecycle && existingLifecycle !== 'running') {
    return {
      ...block,
      result: enrichedResult,
      toolUseResult: toolUseResult || block.toolUseResult,
      agentId: resultInfo.agentId || block.agentId,
    }
  }

  const lifecycle = resultInfo.isError
    ? 'failed'
    : resultInfo.isAsync
      ? 'running'
      : 'completed'
  const isTerminal = lifecycle !== 'running'
  const endTime = isTerminal ? Date.now() : null
  return {
    ...block,
    result: enrichedResult,
    toolUseResult: toolUseResult || undefined,
    agentId: resultInfo.agentId || block.agentId,
    agentTaskStatus: lifecycle,
    status: agentLifecycleToBlockStatus(lifecycle),
    ...(isTerminal ? {
      endTime,
      duration: block.startTime ? endTime - block.startTime : null,
    } : {}),
  }
}

function applyAgentNotificationToBlock(block, rawNotification) {
  if (block?.type !== 'tool_use' || !isAgentToolName(block.name)) return block
  const notification = normalizeAgentTaskNotification(rawNotification)
  if (!notification) return block
  const matchesToolUse = notification.toolUseId && notification.toolUseId === block.id
  const agentId = getAgentDisplayId(block)
  const matchesTask = notification.taskId && agentId && notification.taskId === agentId
  if (!matchesToolUse && !matchesTask) return block

  const lifecycle = agentLifecycleFromStatus(notification.status)
  if (!lifecycle) return block
  const terminal = lifecycle !== 'running'
  const endTime = terminal ? timestampToMillis(notification.timestamp) : null
  return {
    ...block,
    agentId: notification.taskId || agentId || block.agentId,
    agentTaskStatus: notification.status,
    agentTaskSummary: notification.summary || block.agentTaskSummary,
    status: agentLifecycleToBlockStatus(lifecycle),
    ...(terminal ? {
      endTime,
      duration: block.startTime ? Math.max(0, endTime - block.startTime) : block.duration,
    } : {}),
  }
}

function abortRunningBlock(block, agentTaskStatus) {
  if (block.type !== 'tool_use' || (block.status && block.status !== 'running')) return block
  const duration = block.startTime ? Date.now() - block.startTime : null
  if (isAgentToolName(block.name)) {
    const lifecycle = agentLifecycleFromStatus(agentTaskStatus) || 'terminated'
    return {
      ...block,
      status: agentLifecycleToBlockStatus(lifecycle),
      agentTaskStatus,
      result: { is_error: true, content: agentTaskStatus === 'failed' ? 'Failed' : 'Aborted' },
      duration,
    }
  }
  return { ...block, status: 'error', result: { is_error: true, content: 'Aborted' }, duration }
}

// One chat slice per session runtime. `getSibling(name)` resolves another
// slice of the SAME runtime (never the active one) — see runtime/registry.js.
export const createChatStore = (getSibling) => createStore((set, get) => ({
  messages: [],
  // Subagent content: parent_tool_use_id -> flat array of content blocks
  // (text, thinking, tool_use with status/result). Streamed in via useSSE,
  // hydrated in loadSession from sessionTransform.
  subagentContent: {},
  sessionId: null,
  // One-line server-generated recap of this session, shown above the composer.
  // `recapTurns` is the message count it was derived from — used to tell a
  // refreshed recap from the one already on screen. `recapDismissed` is the ×:
  // it hides the current text only, and clears itself when a newer one lands.
  recap: null,
  recapTurns: 0,
  recapDismissed: false,
  inputText: '',
  isStreaming: false,
  isCompacting: false,
  streamAbort: null,
  pendingAskUser: null,
  streamId: null,
  pendingPermission: null,
  permissionQueue: [],
  permissionMode: 'bypassPermissions',
  mcpServers: 'auto', // 'auto' | 'disable' | ['srv-A', ...]
  // Working directory chosen for a NEW conversation (before first send). cwd is
  // locked once a session exists; on resume the cwd comes from the session.
  cwdDraft: null,
  // Additional directories (SDK --add-dir) for this conversation. Editable any
  // time; persisted server-side per session and recovered on resume.
  addDirs: [],
  pendingPlanApproval: null,
  wsSendPermission: null,
  // Mid-stream queue of user messages awaiting injection at the next
  // tool-result boundary (backend). Each entry:
  //   { id, text, attachments, images, status: 'pending' | 'flushing' }
  queuedUserMessages: [],
  queueSender: null, // (payload) => void — active only while streaming
  attachments: [],
  quotedText: null,
  fileReference: null,
  fileReferenceTemplate: null,
  selectedXlsxReference: null,
  selectedFileReference: null,

  enableFileCheckpointing: false,
  checkpoints: [], // [{ uuid, afterMessageIndex, preview, timestamp }]
  forkParentId: null,
  rewindMarker: null, // { checkpointUuid, rewindTs, revertedToolUseIds: string[] }

  // Backend retry indicator: shown inside the assistant-message slot until
  // the next assistant event resolves it (success) or retry_exhausted
  // promotes the slot to an ErrorBlock.
  retryState: null, // { attempt, max, delaySeconds, errorCode, message, succeeded? } | null
  setRetryState: (s) => set({ retryState: s }),
  clearRetryState: () => set({ retryState: null }),
  // Flag the indicator as "reconnect successful" — kept around briefly so the
  // user sees confirmation before the banner disappears. The caller schedules
  // the eventual clear.
  markRetrySucceeded: () => set((s) => (
    s.retryState
      ? { retryState: { ...s.retryState, succeeded: true, delaySeconds: 0 } }
      : {}
  )),
  tickRetryDelay: () => set((s) => (
    s.retryState && s.retryState.delaySeconds > 0
      ? { retryState: { ...s.retryState, delaySeconds: s.retryState.delaySeconds - 1 } }
      : {}
  )),

  // Last user-sent prompt — used by ErrorBlock [Retry] to resend on demand.
  lastUserPrompt: null,
  setLastUserPrompt: (p) => set({ lastUserPrompt: p }),
  clearLastUserPrompt: () => set({ lastUserPrompt: null }),

  // Monotonic stream generation. Bumped on session load and on stop so a
  // stale stream's late events can't write into a freshly loaded session.
  // NOTE: a generation counter, not a sessionId compare — new-session streams
  // legitimately assign their sessionId mid-flight.
  streamGeneration: 0,
  bumpStreamGeneration: () => set((s) => ({ streamGeneration: s.streamGeneration + 1 })),

  quickActionVariableMode: false,
  setQuickActionVariableMode: (active) => set({ quickActionVariableMode: active }),

  setQuotedText: (text) => set({ quotedText: text }),
  clearQuotedText: () => set({ quotedText: null }),

  setFileReference: (ref) => set({ fileReference: ref }),
  clearFileReference: () => set({ fileReference: null }),
  setFileReferenceTemplate: (tpl) => set({ fileReferenceTemplate: tpl }),
  clearFileReferenceTemplate: () => set({ fileReferenceTemplate: null }),
  setSelectedXlsxReference: (ref) => set({ selectedXlsxReference: ref }),
  clearSelectedXlsxReference: () => set({ selectedXlsxReference: null }),
  setSelectedFileReference: (ref) => set({ selectedFileReference: ref }),
  clearSelectedFileReference: () => set({ selectedFileReference: null }),

  // Pending optimize request from Skills file viewer
  pendingOptimize: null,
  setPendingOptimize: (data) => set({ pendingOptimize: data }),
  clearPendingOptimize: () => set({ pendingOptimize: null }),
  // Pending composer text to prefill + auto-send once the chat view mounts
  // (used by "Create Skill with Agent" to seed a /skill-creator prompt).
  pendingComposerSend: null,
  setPendingComposerSend: (text) => set({ pendingComposerSend: text }),
  clearPendingComposerSend: () => set({ pendingComposerSend: null }),
  setInputText: (text) => set({ inputText: text }),
  addAttachment: (attachment) => set((s) => ({
    attachments: [...s.attachments, attachment],
  })),
  updateAttachment: (id, data) => set((s) => ({
    attachments: s.attachments.map((a) => a.id === id ? { ...a, ...data } : a),
  })),
  removeAttachment: (id) => set((s) => {
    const removed = s.attachments.find((a) => a.id === id)
    if (removed?.previewUrl) URL.revokeObjectURL(removed.previewUrl)
    return { attachments: s.attachments.filter((a) => a.id !== id) }
  }),
  clearAttachments: ({ keepErrors = false } = {}) => set((s) => {
    const kept = keepErrors ? s.attachments.filter((a) => a.status === 'error') : []
    s.attachments.forEach((a) => {
      if (a.previewUrl && !kept.includes(a)) URL.revokeObjectURL(a.previewUrl)
    })
    return { attachments: kept }
  }),
  setCompacting: (value) => set({ isCompacting: value }),
  setPermissionMode: (mode) => set({ permissionMode: mode }),
  setMcpServers: (value) => set({ mcpServers: value }),
  setCwdDraft: (path) => set({ cwdDraft: path || null }),
  setAddDirs: (dirs) => set({ addDirs: Array.isArray(dirs) ? dirs : [] }),
  setCheckpointingEnabled: (v) => {
    const { sessionId } = get()
    set({ enableFileCheckpointing: v })
    if (sessionId) {
      safeStorage.setItem(`${CKPT_STORAGE_PREFIX}${sessionId}`, String(v))
    }
  },
  recordCheckpoint: (uuid, afterMessageIndex, preview) => set((s) => {
    if (!uuid) return {}
    if (s.checkpoints.some((c) => c.uuid === uuid)) return {}
    return {
      checkpoints: [...s.checkpoints, { uuid, afterMessageIndex, preview, timestamp: Date.now() }],
    }
  }),
  findCheckpointForAssistant: (assistantIdx) => {
    const msgs = get().messages
    for (let i = assistantIdx - 1; i >= 0; i--) {
      if (msgs[i]?.role === 'user' && msgs[i]?.uuid) return msgs[i].uuid
    }
    return null
  },
  truncateMessagesTo: (assistantIdx) => set((s) => ({
    messages: s.messages.slice(0, assistantIdx + 1),
  })),
  setRewindMarker: (marker) => {
    const { sessionId } = get()
    set({ rewindMarker: marker })
    if (sessionId) {
      safeStorage.setItem(`${REWIND_STORAGE_PREFIX}${sessionId}`, JSON.stringify(marker))
    }
  },
  clearRewindMarker: () => {
    const { sessionId } = get()
    set({ rewindMarker: null })
    if (sessionId) {
      safeStorage.removeItem(`${REWIND_STORAGE_PREFIX}${sessionId}`)
    }
  },
  setSessionId: (id) => set({ sessionId: id }),
  setRecap: (text, turns) => set((s) => (
    // A newer recap un-dismisses: × means "I've read this one", not "never
    // show recaps for this session".
    (turns || 0) > s.recapTurns
      ? { recap: text || null, recapTurns: turns || 0, recapDismissed: false }
      : {}
  )),
  dismissRecap: () => set({ recapDismissed: true }),
  setStreaming: (streaming) => set({ isStreaming: streaming }),
  setStreamAbort: (abort) => set({ streamAbort: abort }),
  setPendingAskUser: (data) => set({ pendingAskUser: data }),
  clearPendingAskUser: () => set({ pendingAskUser: null }),
  setStreamId: (id) => set({ streamId: id }),
  setPendingPermission: (data) => set({ pendingPermission: data }),
  queuePermission: (data) => set((s) => ({
    permissionQueue: [...s.permissionQueue, data],
  })),
  resolvePermission: (requestId) => set((s) => {
    const [next, ...rest] = s.permissionQueue
    return {
      pendingPermission: next || null,
      permissionQueue: rest,
    }
  }),
  clearPermissions: () => set({ pendingPermission: null, permissionQueue: [], streamId: null, wsSendPermission: null }),
  setWsSendPermission: (fn) => set({ wsSendPermission: fn }),
  setQueueSender: (fn) => set({ queueSender: fn }),
  enqueueUserMessage: (msg) => set((s) => ({
    queuedUserMessages: [...s.queuedUserMessages, { ...msg, status: 'pending' }],
  })),
  markQueuedFlushing: (id) => set((s) => ({
    queuedUserMessages: s.queuedUserMessages.map((m) => m.id === id ? { ...m, status: 'flushing' } : m),
  })),
  removeQueuedMessage: (id) => set((s) => ({
    queuedUserMessages: s.queuedUserMessages.filter((m) => m.id !== id),
  })),
  clearQueuedMessages: () => set({ queuedUserMessages: [] }),
  setPendingPlanApproval: (data) => set({ pendingPlanApproval: data }),
  clearPendingPlanApproval: () => set({ pendingPlanApproval: null }),

  addMessage: (message) => set((s) => {
    const next = { messages: [...s.messages, withCid(message)] }
    if (message?.role === 'user' && s.rewindMarker) {
      next.rewindMarker = null
      if (s.sessionId) {
        safeStorage.removeItem(`${REWIND_STORAGE_PREFIX}${s.sessionId}`)
      }
    }
    return next
  }),

  appendToLastAssistant: (text) => set((s) => {
    const msgs = [...s.messages]
    const last = msgs[msgs.length - 1]
    if (last && last.role === 'assistant') {
      msgs[msgs.length - 1] = {
        ...last,
        content: [...last.content, { type: 'text', text }],
      }
    }
    return { messages: msgs }
  }),

  updateLastAssistantContent: (content) => set((s) => {
    const msgs = [...s.messages]
    const last = msgs[msgs.length - 1]
    if (last && last.role === 'assistant') {
      msgs[msgs.length - 1] = { ...last, content }
    }
    return { messages: msgs }
  }),

  // One Zustand mutation per assembler batch (normally 40ms), regardless of
  // how many provider deltas arrived in that window.
  applyStreamBlockPatches: (patches) => set((s) => {
    if (!Array.isArray(patches) || patches.length === 0) return {}
    const mainPatches = patches.filter((patch) => !patch.parentToolUseId)
    const subPatches = patches.filter((patch) => patch.parentToolUseId)
    const next = {}

    if (mainPatches.length > 0) {
      let assistantIndex = -1
      for (let index = s.messages.length - 1; index >= 0; index -= 1) {
        if (s.messages[index]?.role === 'assistant') { assistantIndex = index; break }
      }
      if (assistantIndex >= 0) {
        const message = s.messages[assistantIndex]
        const content = applyBlockPatches(message.content || [], mainPatches)
        if (content !== message.content) {
          const messages = [...s.messages]
          messages[assistantIndex] = { ...message, content }
          next.messages = messages
        }
      }
    }

    if (subPatches.length > 0) {
      let subagentContent = s.subagentContent
      let subChanged = false
      const grouped = new Map()
      for (const patch of subPatches) {
        const group = grouped.get(patch.parentToolUseId) || []
        group.push(patch)
        grouped.set(patch.parentToolUseId, group)
      }
      for (const [parentId, group] of grouped) {
        const current = subagentContent[parentId] || []
        const content = applyBlockPatches(current, group)
        if (content !== current) {
          if (!subChanged) subagentContent = { ...subagentContent }
          subagentContent[parentId] = content
          subChanged = true
        }
      }
      if (subChanged) next.subagentContent = subagentContent
    }
    return next
  }),

  // A reconnect older than the 4000-event replay tail cannot safely continue
  // provisional blocks. Drop only unfinished partials; complete transcript
  // content is retained and later authoritative events heal the conversation.
  clearUnfinishedStreamBlocks: () => set((s) => {
    let messagesChanged = false
    const messages = s.messages.map((message) => {
      if (message?.role !== 'assistant' || !Array.isArray(message.content)) return message
      const content = mapStreamBlocks(message.content, () => null)
      if (content === message.content) return message
      messagesChanged = true
      return { ...message, content }
    })
    let subChanged = false
    const subagentContent = {}
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      const content = mapStreamBlocks(blocks, () => null)
      if (content !== blocks) subChanged = true
      subagentContent[parentId] = content
    }
    return {
      ...(messagesChanged ? { messages } : {}),
      ...(subChanged ? { subagentContent } : {}),
    }
  }),

  finalizeStreamBlocks: () => set((s) => {
    const finish = (block) => ({
      ...block,
      _streamState: 'complete',
      endTime: block.endTime || Date.now(),
    })
    let messagesChanged = false
    const messages = s.messages.map((message) => {
      if (message?.role !== 'assistant' || !Array.isArray(message.content)) return message
      const content = mapStreamBlocks(message.content, finish)
      if (content === message.content) return message
      messagesChanged = true
      return { ...message, content }
    })
    let subChanged = false
    const subagentContent = {}
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      const content = mapStreamBlocks(blocks, finish)
      if (content !== blocks) subChanged = true
      subagentContent[parentId] = content
    }
    return {
      ...(messagesChanged ? { messages } : {}),
      ...(subChanged ? { subagentContent } : {}),
    }
  }),

  // Append blocks (text/thinking/tool_use) to a subagent's content bucket.
  appendToSubagentContent: (parentId, blocks) => set((s) => {
    if (!parentId || !blocks || blocks.length === 0) return {}
    const existing = s.subagentContent[parentId] || []
    return {
      subagentContent: { ...s.subagentContent, [parentId]: [...existing, ...blocks] },
    }
  }),

  // Update a tool_use block nested inside a subagent with its result + status.
  updateSubagentToolResult: (parentId, toolUseId, result) => set((s) => {
    if (!parentId) return {}
    const existing = s.subagentContent[parentId]
    if (!existing) return {}
    let changed = false
    const updated = existing.map((block) => {
      if (block.type === 'tool_use' && block.id === toolUseId) {
        changed = true
        const duration = block.startTime ? Date.now() - block.startTime : null
        return { ...block, result, status: result.is_error ? 'error' : 'success', duration }
      }
      return block
    })
    if (!changed) return {}
    return { subagentContent: { ...s.subagentContent, [parentId]: updated } }
  }),

  // Mark all in-progress subagent tool_use blocks as aborted.
  abortRunningSubagentTools: () => set((s) => {
    const next = {}
    let changed = false
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      const hasRunning = blocks.some((b) => b.type === 'tool_use' && (!b.status || b.status === 'running'))
      if (!hasRunning) { next[parentId] = blocks; continue }
      changed = true
      next[parentId] = blocks.map((b) => {
        if (b.type === 'tool_use' && (!b.status || b.status === 'running')) {
          const duration = b.startTime ? Date.now() - b.startTime : null
          return { ...b, status: 'error', result: { is_error: true, content: 'Aborted' }, duration }
        }
        return b
      })
    }
    if (!changed) return {}
    return { subagentContent: next }
  }),

  setSubagentContent: (map) => set({ subagentContent: map || {} }),

  addToolUse: (block) => set((s) => {
    const msgs = [...s.messages]
    const last = msgs[msgs.length - 1]
    if (last && last.role === 'assistant') {
      msgs[msgs.length - 1] = {
        ...last,
        content: [...last.content, { ...block, status: 'running' }],
      }
    }
    return { messages: msgs }
  }),

  updateToolResult: (toolUseId, result, toolUseResult = null) => set((s) => {
    // Keep identity for untouched messages so memo(MessageBubble) holds —
    // same bail pattern as updateSubagentToolResult above.
    let msgsChanged = false
    const msgs = s.messages.map((msg) => {
      if (msg.role !== 'assistant') return msg
      const has = msg.content.some((b) => b.type === 'tool_use' && b.id === toolUseId)
      if (!has) return msg
      msgsChanged = true
      return {
        ...msg,
        content: msg.content.map((block) => {
          if (block.type === 'tool_use' && block.id === toolUseId) {
            if (isAgentToolName(block.name)) {
              return withAgentToolResult(block, result, toolUseResult)
            }
            const duration = block.startTime ? Date.now() - block.startTime : null
            return { ...block, result, status: result.is_error ? 'error' : 'success', duration }
          }
          return block
        }),
      }
    })
    // Also update subagent tool_use blocks in case this result belongs to one.
    const subNext = {}
    let subChanged = false
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      const has = blocks.some((b) => b.type === 'tool_use' && b.id === toolUseId)
      if (!has) { subNext[parentId] = blocks; continue }
      subChanged = true
      subNext[parentId] = blocks.map((b) => {
        if (b.type === 'tool_use' && b.id === toolUseId) {
          if (isAgentToolName(b.name)) {
            return withAgentToolResult(b, result, toolUseResult)
          }
          const duration = b.startTime ? Date.now() - b.startTime : null
          return { ...b, result, status: result.is_error ? 'error' : 'success', duration }
        }
        return b
      })
    }
    if (!msgsChanged && !subChanged) return {}
    const next = {}
    if (msgsChanged) next.messages = msgs
    if (subChanged) next.subagentContent = subNext
    return next
  }),

  applyAgentTaskNotification: (notification) => set((s) => {
    let messagesChanged = false
    const messages = s.messages.map((message) => {
      if (message.role !== 'assistant' || !Array.isArray(message.content)) return message
      let contentChanged = false
      const content = message.content.map((block) => {
        const next = applyAgentNotificationToBlock(block, notification)
        if (next !== block) contentChanged = true
        return next
      })
      if (!contentChanged) return message
      messagesChanged = true
      return { ...message, content }
    })

    let subagentChanged = false
    const subagentContent = {}
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      let blocksChanged = false
      subagentContent[parentId] = blocks.map((block) => {
        const next = applyAgentNotificationToBlock(block, notification)
        if (next !== block) blocksChanged = true
        return next
      })
      if (blocksChanged) subagentChanged = true
    }

    if (!messagesChanged && !subagentChanged) return {}
    return {
      ...(messagesChanged ? { messages } : {}),
      ...(subagentChanged ? { subagentContent } : {}),
    }
  }),

  // Mark all in-progress tool_use blocks as error (used when stream is aborted)
  abortRunningTools: (agentTaskStatus = 'aborted') => set((s) => {
    const msgs = s.messages.map((msg) => {
      if (msg.role !== 'assistant') return msg
      const hasRunning = msg.content.some((b) => b.type === 'tool_use' && (!b.status || b.status === 'running'))
      if (!hasRunning) return msg
      return {
        ...msg,
        content: msg.content.map((block) => abortRunningBlock(block, agentTaskStatus)),
      }
    })
    // Also abort any running subagent tools.
    const subNext = {}
    for (const [parentId, blocks] of Object.entries(s.subagentContent)) {
      const hasRunning = blocks.some((b) => b.type === 'tool_use' && (!b.status || b.status === 'running'))
      if (!hasRunning) { subNext[parentId] = blocks; continue }
      subNext[parentId] = blocks.map((block) => abortRunningBlock(block, agentTaskStatus))
    }
    return { messages: msgs, subagentContent: subNext }
  }),

  setResult: (data) => set({
    sessionId: data.session_id,
    isStreaming: false,
    isCompacting: false,
    streamAbort: null,
    wsSendPermission: null,
  }),

  clearMessages: () => set({
    messages: [], subagentContent: {}, sessionId: null, streamId: null, pendingPermission: null,
    permissionQueue: [], pendingPlanApproval: null, wsSendPermission: null,
    attachments: [], quotedText: null, fileReference: null,
    fileReferenceTemplate: null, selectedXlsxReference: null, selectedFileReference: null, isCompacting: false,
    checkpoints: [], forkParentId: null, enableFileCheckpointing: false,
    rewindMarker: null, queuedUserMessages: [], retryState: null, lastUserPrompt: null,
    cwdDraft: null, addDirs: [], pendingComposerSend: null,
  }),

  reset: () => set({
    messages: [], subagentContent: {}, sessionId: null, inputText: '', isStreaming: false, isCompacting: false,
    streamAbort: null, pendingAskUser: null, streamId: null,
    pendingPermission: null, permissionQueue: [], permissionMode: 'bypassPermissions',
    pendingPlanApproval: null,
    wsSendPermission: null, attachments: [], quickActionVariableMode: false, mcpServers: 'auto',
    pendingOptimize: null, quotedText: null, fileReference: null, fileReferenceTemplate: null,
    selectedXlsxReference: null, selectedFileReference: null,
    enableFileCheckpointing: false, checkpoints: [], forkParentId: null,
    rewindMarker: null, queuedUserMessages: [], queueSender: null,
    retryState: null, lastUserPrompt: null,
    cwdDraft: null, addDirs: [], pendingComposerSend: null,
  }),

  // For loading a session
  loadSession: (sessionId, messages, parentId = null, subagentContent = {}, addDirs = []) => {
    const restored = safeStorage.getBoolean(`${CKPT_STORAGE_PREFIX}${sessionId}`)
    let rewindMarker = null
    const parsed = safeStorage.getJSON(`${REWIND_STORAGE_PREFIX}${sessionId}`)
    if (parsed && typeof parsed === 'object' && Array.isArray(parsed.revertedToolUseIds)) {
      rewindMarker = parsed
    }
    // Reset THIS runtime's workflow slice atomically with the message swap.
    // Old WorkflowCards still mounted during the async fetch re-seed themselves
    // on an early clear — this final clear (batched with the new messages)
    // wipes those stragglers so a prior load's workflow can't leak into the
    // recovered one. The freshly-mounted cards re-seed cleanly.
    getSibling('workflow').getState().clear()
    set((s) => ({
      sessionId,
      messages,
      subagentContent: subagentContent || {},
      isStreaming: false,
      // The incoming session's recap is fetched separately; clear the outgoing
      // one so it can't flash under the new conversation.
      recap: null,
      recapTurns: 0,
      recapDismissed: false,
      inputText: '',
      checkpoints: [],
      forkParentId: parentId,
      enableFileCheckpointing: restored,
      rewindMarker,
      streamGeneration: s.streamGeneration + 1,
      // cwd is locked to the session; clear any new-chat draft. Hydrate add_dirs
      // from the session's server-side set so the chip shows the recovered dirs.
      cwdDraft: null,
      addDirs: Array.isArray(addDirs) ? addDirs : [],
    }))
  },
}))

registerSliceFactory('chat', createChatStore)

// Facade over the ACTIVE runtime's chat slice — same call patterns as the old
// module-global store (hook + getState/setState/subscribe).
const useChatStore = makeFacade('chat')

export default useChatStore
