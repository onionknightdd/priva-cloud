import test from 'node:test'
import assert from 'node:assert/strict'

import {
  GENERATED_TOOL_LABEL,
  GENERATED_TOOL_METHOD,
  GENERATED_TOOL_NAME,
  getToolDisplayName,
  isGeneratedToolName,
} from './generatedTool.js'

test('recognizes only the current FileCanvas tool identities', () => {
  assert.equal(GENERATED_TOOL_NAME, 'mcp__FileCanvas__register_file')
  assert.equal(GENERATED_TOOL_METHOD, 'register_file')
  assert.equal(isGeneratedToolName(GENERATED_TOOL_NAME), true)
  assert.equal(isGeneratedToolName(GENERATED_TOOL_METHOD), true)
  assert.equal(isGeneratedToolName(GENERATED_TOOL_LABEL), false)
  assert.equal(isGeneratedToolName('unknown_file_tool'), false)
})

test('keeps FileCanvas as the presentation label', () => {
  assert.equal(getToolDisplayName(GENERATED_TOOL_NAME), GENERATED_TOOL_LABEL)
  assert.equal(getToolDisplayName(GENERATED_TOOL_METHOD), GENERATED_TOOL_LABEL)
})
