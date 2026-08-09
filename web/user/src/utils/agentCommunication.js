import {
  getAgentDisplayId,
  getAgentDisplayName,
  isAgentToolName,
} from './agentToolLifecycle.js'

const SEND_MESSAGE_TOOL = 'SendMessage'

function contentText(content) {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((block) => {
      if (typeof block === 'string') return block
      if (block?.type === 'text' && typeof block.text === 'string') return block.text
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

function resultText(result) {
  const content = result?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((block) => {
      if (typeof block === 'string') return block
      if (typeof block?.text === 'string') return block.text
      if (typeof block?.content === 'string') return block.content
      return ''
    })
    .filter(Boolean)
    .join('\n')
}

function normalizedMessageBody(value) {
  return String(value || '').replace(/\r\n/g, '\n').trim()
}

function normalizedTarget(value) {
  return String(value || '').trim()
}

function registerAgent(index, block) {
  if (!block?.id || !isAgentToolName(block.name)) return
  const agentId = getAgentDisplayId(block)
  const identity = {
    toolUseId: block.id,
    agentId,
    name: getAgentDisplayName(block),
  }
  index.byToolUseId.set(block.id, identity)
  if (agentId) index.byAgentId.set(agentId, identity)
}

function visitBlocks(blocks, visitor) {
  if (!Array.isArray(blocks)) return
  blocks.forEach((block) => visitor(block))
}

export function isSendMessageTool(block) {
  return block?.type === 'tool_use' && block.name === SEND_MESSAGE_TOOL
}

export function getSendMessageBody(block) {
  return normalizedMessageBody(block?.input?.message ?? block?.input?.content)
}

export function getSendMessageTarget(block) {
  return normalizedTarget(block?.input?.to ?? block?.input?.recipient)
}

export function parseAgentMessageEnvelope(content) {
  const text = contentText(content)
  if (!text) return null
  const tag = text.match(/<agent-message\b([^>]*)>([\s\S]*?)<\/agent-message>/i)
  if (!tag) return null
  const from = tag[1].match(/\bfrom\s*=\s*(?:"([^"]*)"|'([^']*)')/i)
  const body = normalizedMessageBody(tag[2])
  if (!body) return null
  return {
    body,
    senderName: normalizedTarget(from?.[1] || from?.[2]),
  }
}

export function parseSendMessageResult(block) {
  const result = block?.result
  const candidates = [
    block?.toolUseResult,
    result?.tool_use_result,
    result?.toolUseResult,
  ].filter((value) => value && typeof value === 'object')

  const text = resultText(result).trim()
  if (text.startsWith('{') && text.endsWith('}')) {
    try {
      const parsed = JSON.parse(text)
      if (parsed && typeof parsed === 'object') candidates.push(parsed)
    } catch {
      // The SDK also emits prose results; the outer result state remains valid.
    }
  }

  const explicitSuccess = candidates
    .map((value) => value.success)
    .find((value) => typeof value === 'boolean')
  const failed = result?.is_error === true
    || block?.status === 'error'
    || explicitSuccess === false

  if (!result && (!block?.status || block.status === 'running')) return 'running'
  return failed ? 'error' : 'success'
}

/**
 * Build a small identity/routing index from both main-thread and sidechain
 * blocks. Sent entries are retained so live peer envelopes (which omit the
 * sender id) can be matched to their real SendMessage call by body + target.
 */
export function buildAgentCommunicationIndex(messages, subagentContent) {
  const index = {
    byToolUseId: new Map(),
    byAgentId: new Map(),
    sent: [],
  }

  for (const message of messages || []) {
    if (message?.role === 'assistant') {
      visitBlocks(message.content, (block) => registerAgent(index, block))
    }
  }
  for (const blocks of Object.values(subagentContent || {})) {
    visitBlocks(blocks, (block) => registerAgent(index, block))
  }

  for (const message of messages || []) {
    if (message?.role !== 'assistant') continue
    visitBlocks(message.content, (block) => {
      if (!isSendMessageTool(block)) return
      index.sent.push({
        block,
        body: getSendMessageBody(block),
        target: getSendMessageTarget(block),
        source: null,
        fromMain: true,
      })
    })
  }
  for (const [ownerToolUseId, blocks] of Object.entries(subagentContent || {})) {
    visitBlocks(blocks, (block) => {
      if (!isSendMessageTool(block)) return
      index.sent.push({
        block,
        body: getSendMessageBody(block),
        target: getSendMessageTarget(block),
        source: index.byToolUseId.get(ownerToolUseId) || null,
        fromMain: false,
      })
    })
  }

  return index
}

export function buildReceivedFromMainEvents(index, ownerToolUseId) {
  const receiver = index?.byToolUseId?.get(ownerToolUseId)
  const receiverIds = new Set(
    [receiver?.agentId, receiver?.toolUseId].filter(Boolean).map(String),
  )
  if (receiverIds.size === 0) return []

  return (index?.sent || [])
    .filter((entry) => (
      entry.fromMain
      && receiverIds.has(entry.target)
      && entry.body
      && parseSendMessageResult(entry.block) === 'success'
    ))
    .map((entry) => ({
      type: 'agent_message',
      id: `agent-message-main-${entry.block.id}`,
      direction: 'received',
      body: entry.body,
      senderAgentId: 'main',
      senderName: 'main',
      sourceToolUseId: entry.block.id,
      timestamp: entry.block.endTime || entry.block.startTime || null,
    }))
}

export function resolveSentTarget(index, target) {
  const normalized = normalizedTarget(target)
  if (!normalized || normalized.toLowerCase() === 'main') return { isMain: true }
  const identity = index?.byAgentId?.get(normalized)
    || index?.byToolUseId?.get(normalized)
  return identity || { agentId: normalized, name: '' }
}

export function resolveReceivedSource(index, messageBlock, ownerToolUseId) {
  const senderAgentId = normalizedTarget(
    messageBlock?.senderAgentId ?? messageBlock?.sender_agent_id,
  )
  if (senderAgentId.toLowerCase() === 'main') return { isMain: true }
  if (senderAgentId) {
    const identity = index?.byAgentId?.get(senderAgentId)
      || index?.byToolUseId?.get(senderAgentId)
    if (identity) return identity
  }

  const receiver = index?.byToolUseId?.get(ownerToolUseId)
  const receiverIds = new Set(
    [receiver?.agentId, receiver?.toolUseId].filter(Boolean).map(String),
  )
  const body = normalizedMessageBody(messageBlock?.body)
  const matchingSend = [...(index?.sent || [])].reverse().find((entry) => (
    entry.body === body
    && (receiverIds.size === 0 || receiverIds.has(entry.target))
  ))
  if (matchingSend) return matchingSend.source || { isMain: true }

  const senderName = normalizedTarget(messageBlock?.senderName ?? messageBlock?.sender_name)
  if (senderName.toLowerCase() === 'main') return { isMain: true }
  return {
    agentId: senderAgentId,
    name: senderName,
  }
}
