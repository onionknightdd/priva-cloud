import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildAgentCommunicationIndex,
  buildReceivedFromMainEvents,
  parseAgentMessageEnvelope,
  parseSendMessageResult,
  resolveReceivedSource,
  resolveSentTarget,
} from './agentCommunication.js'

const agentA = {
  type: 'tool_use',
  id: 'call-agent-a',
  name: 'Agent',
  input: { description: 'Agent A' },
  result: { content: 'agentId: agent-a' },
}
const agentB = {
  type: 'tool_use',
  id: 'call-agent-b',
  name: 'Agent',
  input: { description: 'Agent B' },
  result: { content: 'agentId: agent-b' },
}

test('extracts only the peer message body from the SDK envelope', () => {
  assert.deepEqual(parseAgentMessageEnvelope(
    'SDK preface\n<agent-message from="general-purpose">hello B</agent-message>\npolicy suffix',
  ), {
    body: 'hello B',
    senderName: 'general-purpose',
  })
})

test('treats SendMessage success false as an error even when tool_result is not errored', () => {
  assert.equal(parseSendMessageResult({
    status: 'success',
    result: {
      is_error: false,
      content: '{"success":false,"message":"Unknown agent"}',
    },
  }), 'error')
})

test('resolves sent and received peers from agent ids and real SendMessage calls', () => {
  const messages = [{ role: 'assistant', content: [agentA, agentB] }]
  const subagentContent = {
    'call-agent-a': [{
      type: 'tool_use',
      id: 'send-a-b',
      name: 'SendMessage',
      input: { to: 'agent-b', message: 'hello B' },
    }],
    'call-agent-b': [],
  }
  const index = buildAgentCommunicationIndex(messages, subagentContent)

  assert.equal(resolveSentTarget(index, 'agent-b').name, 'Agent B')
  assert.equal(resolveReceivedSource(index, { body: 'hello B' }, 'call-agent-b').name, 'Agent A')
})

test('recognizes main as a communication endpoint', () => {
  const index = buildAgentCommunicationIndex([], {})
  assert.deepEqual(resolveSentTarget(index, 'main'), { isMain: true })
  assert.deepEqual(resolveReceivedSource(index, { senderAgentId: 'main', body: 'ready' }, 'agent'), { isMain: true })
})

test('projects a successful main SendMessage onto the target Agent as received', () => {
  const mainSend = {
    type: 'tool_use',
    id: 'main-send-b',
    name: 'SendMessage',
    input: { to: 'agent-b', message: 'hello from main' },
    status: 'success',
    result: {
      is_error: false,
      content: '{"success":true,"message":"Message queued for delivery"}',
    },
  }
  const index = buildAgentCommunicationIndex(
    [{ role: 'assistant', content: [agentB, mainSend] }],
    { 'call-agent-b': [] },
  )

  assert.deepEqual(buildReceivedFromMainEvents(index, 'call-agent-b'), [{
    type: 'agent_message',
    id: 'agent-message-main-main-send-b',
    direction: 'received',
    body: 'hello from main',
    senderAgentId: 'main',
    senderName: 'main',
    sourceToolUseId: 'main-send-b',
    timestamp: null,
  }])
})
