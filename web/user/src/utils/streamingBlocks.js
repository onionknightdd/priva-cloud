const DEFAULT_BATCH_MS = 40
const MAIN_LANE = '__main__'

const laneKey = (parentToolUseId) => parentToolUseId || MAIN_LANE

function blockKey(sessionId, parentToolUseId, messageId, index) {
  return [sessionId || 'session', laneKey(parentToolUseId), messageId, index].join(':')
}

function isVisibleText(block) {
  if (block?.type === 'text') return Boolean(block.text)
  if (block?.type === 'thinking') return Boolean(block.thinking)
  return block?.type === 'tool_use'
}

/**
 * Assemble Claude Agent SDK StreamEvent payloads into stable UI blocks.
 *
 * Provider differences are deliberately absorbed here:
 * - visible text/thinking deltas are batched before touching Zustand;
 * - signature deltas are never exposed;
 * - fragmented tool JSON stays private until content_block_stop, and is
 *   normally superseded by the authoritative AssistantMessage first;
 * - SDK event UUIDs are transport identities, never React/store keys.
 */
export function createStreamingBlockAssembler({
  onFlush,
  batchMs = DEFAULT_BATCH_MS,
  now = () => Date.now(),
  schedule = (callback, delay) => setTimeout(callback, delay),
  cancel = (timer) => clearTimeout(timer),
  parseJson = JSON.parse,
} = {}) {
  const lanes = new Map()
  const blocks = new Map()
  const dirty = new Map()
  let timer = null
  let disposed = false

  const scheduleFlush = () => {
    if (timer !== null || disposed) return
    timer = schedule(() => {
      timer = null
      flush()
    }, batchMs)
  }

  const markDirty = (state, deltaType = null, deltaText = '') => {
    if (state.authoritative || !state.materialized) return
    dirty.set(state.key, state)
    if (deltaType === 'text_delta' && deltaText) {
      state.logText += deltaText
      state.logTextEvents += 1
    } else if (deltaType === 'thinking_delta' && deltaText) {
      state.logThinking += deltaText
      state.logThinkingEvents += 1
    }
    scheduleFlush()
  }

  const flush = () => {
    if (timer !== null) {
      cancel(timer)
      timer = null
    }
    if (dirty.size === 0) return { patches: [], logs: [] }

    const patches = []
    const logs = []
    for (const state of dirty.values()) {
      if (!state.authoritative && state.materialized) {
        patches.push({
          parentToolUseId: state.parentToolUseId,
          messageId: state.messageId,
          index: state.index,
          streamKey: state.key,
          block: { ...state.block, _streamKey: state.key },
        })
      }
      if (state.logText) {
        logs.push({
          parentToolUseId: state.parentToolUseId,
          messageId: state.messageId,
          index: state.index,
          deltaType: 'text_delta',
          content: state.logText,
          eventCount: state.logTextEvents,
        })
      }
      if (state.logThinking) {
        logs.push({
          parentToolUseId: state.parentToolUseId,
          messageId: state.messageId,
          index: state.index,
          deltaType: 'thinking_delta',
          content: state.logThinking,
          eventCount: state.logThinkingEvents,
        })
      }
      state.logText = ''
      state.logTextEvents = 0
      state.logThinking = ''
      state.logThinkingEvents = 0
    }
    dirty.clear()
    if (patches.length > 0 || logs.length > 0) onFlush?.({ patches, logs })
    return { patches, logs }
  }

  const stateForEvent = (payload, index) => {
    const lane = lanes.get(laneKey(payload?.parent_tool_use_id))
    if (!lane) return null
    return blocks.get(blockKey(
      payload.session_id || lane.sessionId,
      payload.parent_tool_use_id,
      lane.messageId,
      index,
    )) || null
  }

  const accept = (payload) => {
    if (disposed || !payload || payload.type !== 'stream_event') return false
    const event = payload.event
    if (!event || typeof event.type !== 'string') return false
    const parentToolUseId = payload.parent_tool_use_id || null
    const lane = laneKey(parentToolUseId)

    if (event.type === 'message_start') {
      const messageId = event.message?.id
      if (!messageId) return false
      lanes.set(lane, { messageId, sessionId: payload.session_id || null })
      return true
    }

    if (event.type === 'content_block_start') {
      const active = lanes.get(lane)
      const source = event.content_block
      if (!active || !source || !Number.isInteger(event.index)) return false
      if (!['text', 'thinking', 'tool_use'].includes(source.type)) return false

      const key = blockKey(
        payload.session_id || active.sessionId,
        parentToolUseId,
        active.messageId,
        event.index,
      )
      const startedAt = now()
      let block
      if (source.type === 'text') {
        block = { type: 'text', text: source.text || '', startTime: startedAt, _streamState: 'streaming' }
      } else if (source.type === 'thinking') {
        block = { type: 'thinking', thinking: source.thinking || '', startTime: startedAt, _streamState: 'streaming' }
      } else {
        block = {
          type: 'tool_use',
          id: source.id,
          name: source.name,
          input: source.input && typeof source.input === 'object' ? source.input : {},
          status: 'running',
          startTime: startedAt,
          _streamState: 'streaming-input',
        }
      }
      const state = {
        key,
        parentToolUseId,
        sessionId: payload.session_id || active.sessionId,
        messageId: active.messageId,
        index: event.index,
        block,
        rawJson: '',
        materialized: isVisibleText(block),
        authoritative: false,
        logText: '',
        logTextEvents: 0,
        logThinking: '',
        logThinkingEvents: 0,
      }
      blocks.set(key, state)
      if (state.materialized) markDirty(state)
      return true
    }

    if (event.type === 'content_block_delta') {
      const state = stateForEvent(payload, event.index)
      const delta = event.delta
      if (!state || state.authoritative || !delta) return false
      if (delta.type === 'text_delta' && state.block.type === 'text') {
        const text = typeof delta.text === 'string' ? delta.text : ''
        if (!text) return true
        state.block = { ...state.block, text: state.block.text + text }
        state.materialized = true
        markDirty(state, delta.type, text)
        return true
      }
      if (delta.type === 'thinking_delta' && state.block.type === 'thinking') {
        const thinking = typeof delta.thinking === 'string' ? delta.thinking : ''
        if (!thinking) return true
        state.block = { ...state.block, thinking: state.block.thinking + thinking }
        state.materialized = true
        markDirty(state, delta.type, thinking)
        return true
      }
      if (delta.type === 'input_json_delta' && state.block.type === 'tool_use') {
        if (typeof delta.partial_json === 'string') state.rawJson += delta.partial_json
        return true
      }
      // signature_delta and unknown provider extensions intentionally have no
      // UI/store/log representation.
      return true
    }

    if (event.type === 'content_block_stop') {
      const state = stateForEvent(payload, event.index)
      if (!state || state.authoritative) return false
      if (state.block.type === 'tool_use' && state.rawJson) {
        try {
          const input = parseJson(state.rawJson)
          if (input && typeof input === 'object' && !Array.isArray(input)) {
            state.block = { ...state.block, input }
          }
        } catch {
          // The complete AssistantMessage is authoritative. Keep the generic
          // card when a non-conforming provider stops with invalid JSON.
        }
      }
      if (state.materialized) {
        state.block = {
          ...state.block,
          endTime: now(),
          _streamState: 'awaiting-authoritative',
        }
        markDirty(state)
      }
      return true
    }

    return true
  }

  const reconcileAssistant = (message) => {
    flush()
    const parentToolUseId = message?.parent_tool_use_id || null
    const active = lanes.get(laneKey(parentToolUseId))
    const messageId = message?.message_id || active?.messageId || null
    const content = Array.isArray(message?.content) ? message.content : []
    const available = [...blocks.values()]
      .filter((state) => (
        !state.authoritative &&
        state.parentToolUseId === parentToolUseId &&
        (!messageId || state.messageId === messageId)
      ))
      .sort((a, b) => a.index - b.index)

    return content.map((block) => {
      let state = null
      if (block?.type === 'tool_use' && block.id) {
        state = available.find((candidate) => (
          !candidate.authoritative &&
          candidate.block.type === 'tool_use' &&
          candidate.block.id === block.id
        )) || null
      } else if (block?.type === 'text' || block?.type === 'thinking') {
        state = available.find((candidate) => (
          !candidate.authoritative && candidate.block.type === block.type
        )) || null
      }
      if (state) {
        state.authoritative = true
        dirty.delete(state.key)
      }
      return {
        block,
        parentToolUseId,
        messageId,
        index: state?.index ?? null,
        streamKey: state?.key || null,
        startTime: state?.block?.startTime || now(),
        endTime: now(),
      }
    })
  }

  const resetForReplayGap = () => {
    if (timer !== null) cancel(timer)
    timer = null
    dirty.clear()
    blocks.clear()
    lanes.clear()
  }

  const dispose = ({ flushPending = false } = {}) => {
    if (flushPending) flush()
    if (timer !== null) cancel(timer)
    timer = null
    dirty.clear()
    disposed = true
  }

  return {
    accept,
    flush,
    reconcileAssistant,
    resetForReplayGap,
    dispose,
    // Test-only observability without exposing mutable internals.
    getState: () => ({ lanes: lanes.size, blocks: blocks.size, dirty: dirty.size, disposed }),
  }
}

export { DEFAULT_BATCH_MS }
