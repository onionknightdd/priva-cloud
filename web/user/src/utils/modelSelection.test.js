import assert from 'node:assert/strict'
import test from 'node:test'

import {
  modelCapabilitiesFromResponse,
  modelReferenceForRequest,
  modelSelectionFromResponse,
  normalizeLastResponseModel,
} from './modelSelection.js'

const profiles = [
  { id: 'default', default_model: 'claude-sonnet-4-5' },
  { id: 'gateway', default_model: 'ollama:llama3:8b' },
]

test('normalizes the nested session model and preserves 1m capability', () => {
  assert.deepEqual(normalizeLastResponseModel({
    profile_id: 'gateway',
    model: {
      id: 'ollama:llama3:8b',
      capabilities: { context: '1m' },
    },
    observed_at: 123,
  }), {
    profileId: 'gateway',
    model: {
      id: 'ollama:llama3:8b',
      capabilities: { context: '1m' },
    },
    observedAt: 123,
  })
})

test('migrates a legacy suffixed model id into capabilities', () => {
  const legacy = {
    profile_id: 'default',
    model_id: 'claude-sonnet-4-5[1M]',
  }

  assert.equal(modelSelectionFromResponse(legacy, profiles, 'default'), null)
  assert.deepEqual(modelCapabilitiesFromResponse(legacy), { context: '1m' })
})

test('builds the API model reference with 1m after the base model id', () => {
  assert.equal(
    modelReferenceForRequest(null, { context: '1m' }, profiles, 'default'),
    'default:claude-sonnet-4-5[1m]',
  )
  assert.equal(
    modelReferenceForRequest('gateway:ollama:llama3:8b', { context: '1m' }, profiles, 'default'),
    'gateway:ollama:llama3:8b[1m]',
  )
  assert.equal(
    modelReferenceForRequest('gateway:model[1m]', { context: null }, profiles, 'default'),
    'gateway:model',
  )
})
