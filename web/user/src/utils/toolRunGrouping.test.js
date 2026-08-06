import test from 'node:test'
import assert from 'node:assert/strict'
import { collectToolRun } from './toolRunGrouping.js'

const isToolBlock = (block) => block?.type === 'tool_use' || block?.type === 'file_ref'
const isEmptyTextBlock = (block) => block?.type === 'text' && !block.text?.trim()

test('groups consecutive tool envelopes regardless of process group id', () => {
  const blocks = [
    { type: 'tool_use', id: 'read', processGroupId: 'process-1' },
    { type: 'tool_use', id: 'grep', processGroupId: 'process-2' },
    { type: 'file_ref', id: 'write', processGroupId: 'process-3' },
  ]

  const result = collectToolRun(blocks, 0, isToolBlock, isEmptyTextBlock)

  assert.deepEqual(result.run.map((block) => block.id), ['read', 'grep', 'write'])
  assert.deepEqual(result.toolIndexes, [0, 1, 2])
  assert.equal(result.endIndex, 2)
  assert.equal(result.lastToolIndex, 2)
})

test('ends a tool run at a visible message block', () => {
  const blocks = [
    { type: 'tool_use', id: 'read', processGroupId: 'process-1' },
    { type: 'text', text: 'Checking the result.', processGroupId: 'process-2' },
    { type: 'tool_use', id: 'grep', processGroupId: 'process-3' },
  ]

  const result = collectToolRun(blocks, 0, isToolBlock, isEmptyTextBlock)

  assert.deepEqual(result.run.map((block) => block.id), ['read'])
  assert.deepEqual(result.toolIndexes, [0])
  assert.equal(result.endIndex, 0)
  assert.equal(result.lastToolIndex, 0)
})

test('keeps empty text transparent without losing the trailing process group', () => {
  const blocks = [
    { type: 'tool_use', id: 'read', processGroupId: 'process-1' },
    { type: 'text', text: '   ', processGroupId: 'process-2' },
    { type: 'tool_use', id: 'grep', processGroupId: 'process-3' },
    { type: 'text', text: '' },
  ]

  const result = collectToolRun(blocks, 0, isToolBlock, isEmptyTextBlock)

  assert.deepEqual(result.run.map((block) => block.id), ['read', 'grep'])
  assert.deepEqual(result.toolIndexes, [0, 2])
  assert.equal(result.endIndex, 3)
  assert.equal(result.lastToolIndex, 2)
  assert.equal(result.run.at(-1).processGroupId, 'process-3')
})
