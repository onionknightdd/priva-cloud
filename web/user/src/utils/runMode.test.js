import assert from 'node:assert/strict'
import test from 'node:test'

import { effectiveRunMode, isRunModeLocked, normalizeRunMode } from './runMode.js'

test('normalizes the public run modes and applies a safe fallback', () => {
  assert.equal(normalizeRunMode('agent'), 'agent')
  assert.equal(normalizeRunMode('code'), 'code')
  assert.equal(normalizeRunMode('legacy', 'code'), 'code')
  assert.equal(normalizeRunMode(undefined, 'invalid'), 'agent')
})

test('session state takes precedence over the new-session preference', () => {
  assert.equal(effectiveRunMode({ runMode: 'code' }, 'agent'), 'code')
  assert.equal(effectiveRunMode({ runMode: null }, 'code'), 'code')
  assert.equal(effectiveRunMode({}, 'agent'), 'agent')
})

test('a claimed session remains locked after streaming completes', () => {
  assert.equal(isRunModeLocked({ runModeLocked: true, sessionId: null }), true)
  assert.equal(isRunModeLocked({ runModeLocked: false, sessionId: 'session-1' }), true)
  assert.equal(isRunModeLocked({ runModeLocked: false, sessionId: null }), false)
})
