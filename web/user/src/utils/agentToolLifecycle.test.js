import assert from 'node:assert/strict'
import test from 'node:test'

import {
  agentLifecycleFromStatus,
  getAgentDisplayId,
  getAgentLifecycle,
  getAgentResultInfo,
  normalizeAgentTaskNotification,
} from './agentToolLifecycle.js'

test('keeps an asynchronously launched Agent running until a terminal notification', () => {
  const result = {
    type: 'tool_result',
    content: 'Async agent launched successfully.\nagentId: ae68756d1752776a5',
    tool_use_result: { status: 'async_launched', isAsync: true, agentId: 'ae68756d1752776a5' },
  }

  assert.deepEqual(getAgentResultInfo(result), {
    agentId: 'ae68756d1752776a5',
    launchStatus: 'async_launched',
    isAsync: true,
    isError: false,
  })
  assert.equal(getAgentDisplayId({ result }), 'ae68756d1752776a5')
  assert.equal(getAgentLifecycle({ status: 'success', result }), 'running')
})

test('maps clean, terminated, and abnormal terminal states separately', () => {
  assert.equal(agentLifecycleFromStatus('completed'), 'completed')
  assert.equal(agentLifecycleFromStatus('killed'), 'terminated')
  assert.equal(agentLifecycleFromStatus('cancelled'), 'terminated')
  assert.equal(agentLifecycleFromStatus('failed'), 'failed')
  assert.equal(getAgentLifecycle({ agentTaskStatus: 'killed' }), 'terminated')
  assert.equal(getAgentLifecycle({ agentTaskStatus: 'error' }), 'failed')
})

test('normalizes snake-case live task notifications', () => {
  assert.deepEqual(normalizeAgentTaskNotification({
    tool_use_id: 'call-agent',
    task_id: 'agent-1',
    status: 'STOPPED',
    summary: 'Stopped by user',
  }), {
    toolUseId: 'call-agent',
    taskId: 'agent-1',
    status: 'stopped',
    summary: 'Stopped by user',
    timestamp: null,
  })
})
