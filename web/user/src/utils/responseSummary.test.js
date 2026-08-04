import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findFinalResultBlockIndexes,
  formatExecutionDuration,
  resultTextFromBlocks,
  summarizeResponseExecution,
} from './responseSummary.js'

test('formats elapsed time without zero-value units', () => {
  assert.equal(formatExecutionDuration(3723000), '1h 2m 3s')
  assert.equal(formatExecutionDuration(180000), '3m')
  assert.equal(formatExecutionDuration(4200), '4s')
  assert.equal(formatExecutionDuration(100), '1s')
})

test('locates the authoritative trailing result text', () => {
  const blocks = [
    { type: 'thinking', thinking: 'inspect files' },
    { type: 'text', text: 'I will inspect the project.' },
    { type: 'tool_use', id: 'read-1', name: 'Read', input: { file_path: 'a.js' } },
    { type: 'text', text: 'Done.' },
  ]
  const indexes = findFinalResultBlockIndexes(blocks, 'Done.')
  assert.deepEqual(indexes, [3])
  assert.equal(resultTextFromBlocks(blocks, indexes), 'Done.')
})

test('summarizes unique files and nested subagent activity', () => {
  const summary = summarizeResponseExecution({
    durationMs: 65000,
    additionalQuestionCount: 2,
    contentBlocks: [
      { type: 'tool_use', id: 'read-1', name: 'Read', status: 'success', input: { file_path: 'src/a.js' } },
      { type: 'tool_use', id: 'read-2', name: 'Read', status: 'success', input: { file_path: 'src/a.js' } },
      { type: 'file_ref', fileOpId: 'edit-1', name: 'Edit', filePath: 'src/a.js' },
      { type: 'tool_use', id: 'bash-1', name: 'Bash', input: { command: 'npm test' } },
      { type: 'ask_user', id: 'ask-1', questions: [{ question: 'Continue?' }] },
      { type: 'tool_use', id: 'agent-1', name: 'Agent', input: {} },
    ],
    subagentContent: {
      'agent-1': [
        { type: 'tool_use', id: 'read-3', name: 'Read', status: 'success', input: { file_path: 'src/b.js' } },
        { type: 'tool_use', id: 'bash-2', name: 'Bash', input: { command: 'npm run build' } },
      ],
    },
    fileOps: [{ id: 'edit-1', status: 'success', filePath: 'src/a.js' }],
  })

  assert.deepEqual(summary, {
    duration: '1m 5s',
    readFiles: 2,
    editedFiles: 1,
    commands: 2,
    questions: 3,
  })
})

test('does not count failed file operations', () => {
  const summary = summarizeResponseExecution({
    contentBlocks: [
      { type: 'tool_use', id: 'read-1', name: 'Read', status: 'error', input: { file_path: 'missing.js' } },
      { type: 'file_ref', fileOpId: 'edit-1', name: 'Edit', filePath: 'src/a.js' },
    ],
    fileOps: [{ id: 'edit-1', status: 'error', filePath: 'src/a.js' }],
  })
  assert.equal(summary.readFiles, 0)
  assert.equal(summary.editedFiles, 0)
})
