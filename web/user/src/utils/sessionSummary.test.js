import assert from 'node:assert/strict'
import test from 'node:test'

import {
  fileOpLineStats,
  hasSessionSummaryActivity,
  summarizeCanvasChanges,
  uniqueCanvasFiles,
  uniqueConversationAgents,
  uniqueConversationSources,
} from './sessionSummary.js'

test('deduplicates Canvas files by exact path and keeps deleted tabs', () => {
  const files = uniqueCanvasFiles([
    { id: 'a', filePath: '/workspace/src/a.js' },
    { id: 'a-again', filePath: '/workspace/src/a.js' },
    { id: 'removed', filePath: '/workspace/old.pdf', missing: true },
    { id: 'written', filePath: '/workspace/new.txt', sourceTool: 'Write' },
    { id: 'edited', filePath: '/workspace/changed.txt', sourceTool: 'Edit' },
  ])

  assert.deepEqual(files.map((file) => file.id), ['a', 'removed'])
  assert.equal(files[1].missing, true)
})

test('collects unique user-provided files and images as conversation sources', () => {
  const sources = uniqueConversationSources([
    {
      role: 'user',
      uuid: 'user-1',
      attachments: [
        { name: 'brief.pdf', path: '/uploads/brief.pdf' },
        { name: 'reference.png', path: '/uploads/reference.png', isImage: true },
      ],
      content: [
        { type: 'image', filename: 'reference.png', source: { media_type: 'image/png', data: 'YWJj' } },
        { type: 'text', text: 'Use these sources.' },
      ],
    },
    {
      role: 'assistant',
      attachments: [{ name: 'ignored.txt', path: '/uploads/ignored.txt' }],
    },
  ])

  assert.deepEqual(sources.map(({ label, kind }) => ({ label, kind })), [
    { label: 'brief.pdf', kind: 'file' },
    { label: 'reference.png', kind: 'image' },
  ])
  assert.equal(sources[1].src, 'data:image/png;base64,YWJj')
})

test('counts unique Agent and Task runs recursively across the conversation', () => {
  const nestedAgent = { type: 'tool_use', id: 'agent-nested', name: 'Task' }
  const agents = uniqueConversationAgents([
    {
      role: 'assistant',
      content: [
        { type: 'tool_use', id: 'agent-root', name: 'Agent' },
        { type: 'tool_use', id: 'read-1', name: 'Read' },
      ],
    },
    {
      role: 'assistant',
      content: [{ type: 'tool_use', id: 'agent-root', name: 'Agent' }],
    },
  ], {
    'agent-root': [nestedAgent],
  })

  assert.deepEqual(agents.map((agent) => agent.id), ['agent-root', 'agent-nested'])
})

test('prefers the session summary when files, changes, or Agents exist', () => {
  assert.equal(hasSessionSummaryActivity({ fileBrowserTabs: [{ filePath: 'a.js' }] }), true)
  assert.equal(hasSessionSummaryActivity({ fileOps: [{ id: 'edit-1' }] }), true)
  assert.equal(hasSessionSummaryActivity({
    messages: [{ content: [{ type: 'tool_use', id: 'agent-1', name: 'Agent' }] }],
  }), true)
  assert.equal(hasSessionSummaryActivity({ messages: [{ content: [{ type: 'tool_use', name: 'TodoWrite' }] }] }), false)
})

test('uses structured patches before Write and Edit input fallbacks', () => {
  assert.deepEqual(fileOpLineStats({
    type: 'edit',
    structuredPatch: {
      hunks: [{ lines: [' context', '-old', '+new', '+next'] }],
    },
    input: { old_string: 'ignored', new_string: 'ignored' },
  }), { added: 2, removed: 1 })

  assert.deepEqual(fileOpLineStats({
    type: 'write',
    input: { content: 'one\ntwo\n' },
  }), { added: 2, removed: 0 })

  assert.deepEqual(fileOpLineStats({
    type: 'edit',
    input: { old_string: 'one\ntwo', new_string: 'next' },
  }), { added: 1, removed: 2 })
})

test('counts unique operated files and only settled non-reverted line deltas', () => {
  const summary = summarizeCanvasChanges([
    { id: 'write-1', type: 'write', filePath: 'src/a.js', status: 'success', input: { content: 'one\ntwo' } },
    { id: 'edit-1', type: 'edit', filePath: 'src/a.js', status: 'success', input: { old_string: 'one', new_string: 'next' } },
    { id: 'edit-2', type: 'edit', filePath: 'src/b.js', status: 'running', input: { old_string: 'old', new_string: 'new' } },
    { id: 'edit-3', type: 'edit', filePath: 'src/c.js', status: 'success', input: { old_string: 'gone', new_string: 'back' } },
    { id: 'generated-1', type: 'generated', filePath: 'report.pdf', status: 'success' },
  ], ['edit-3'])

  assert.equal(summary.operations.length, 4)
  assert.equal(summary.fileCount, 3)
  assert.equal(summary.added, 3)
  assert.equal(summary.removed, 1)
})
