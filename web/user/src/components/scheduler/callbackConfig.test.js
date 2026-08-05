import assert from 'node:assert/strict'
import test from 'node:test'

import {
  buildFeishuCallback,
  canSetFeishuCallback,
  hasFeishuCallback,
  isFeishuCallbackReady,
} from './callbackConfig.js'

test('requires both an owner binding and an effectively enabled Feishu bot', () => {
  assert.equal(isFeishuCallbackReady({ owner_bound: true, effective_enabled: true }), true)
  assert.equal(isFeishuCallbackReady({ owner_bound: true, effective_enabled: false }), false)
  assert.equal(isFeishuCallbackReady({ owner_bound: false, effective_enabled: true }), false)
  assert.equal(isFeishuCallbackReady(null), false)
})

test('serializes the callback as the scheduler job-config contract', () => {
  assert.deepEqual(buildFeishuCallback(true), { type: 'feishu' })
  assert.equal(buildFeishuCallback(false), null)
  assert.equal(hasFeishuCallback({ callback: { type: 'feishu' } }), true)
  assert.equal(hasFeishuCallback({ callback: null }), false)
})

test('fails closed when enabling but still lets an unavailable existing callback be removed', () => {
  const unavailable = { owner_bound: false, effective_enabled: false }
  assert.equal(canSetFeishuCallback(true, unavailable), false)
  assert.equal(canSetFeishuCallback(false, unavailable), true)
  assert.equal(canSetFeishuCallback(true, undefined), false)
  assert.equal(canSetFeishuCallback(false, undefined), true)
})
