import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildAgentCommunicationIndex,
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

test('extracts an actual coordinator delivery as a message received from main', () => {
  assert.deepEqual(parseAgentMessageEnvelope(
    'The coordinator sent a message while you were working:\nhello from main'
      + '\n\nAddress this before completing your current task.',
  ), {
    body: 'hello from main',
    senderName: 'main',
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
