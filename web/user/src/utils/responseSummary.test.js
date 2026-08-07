import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findFinalResultBlockIndexes,
  formatExecutionDuration,
  resultTextFromBlocks,
  summarizeResponseExecution,
  visibleExecutionSummaryItems,
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

test('defers live tool metrics until tool results settle', () => {
  const running = summarizeResponseExecution({
    contentBlocks: [
      { type: 'tool_use', id: 'read-1', name: 'Read', status: 'running', input: { file_path: 'src/live.js' } },
      { type: 'tool_use', id: 'bash-1', name: 'Bash', status: 'running', input: { command: 'npm test' } },
      { type: 'file_ref', id: 'file-ref-edit-1', fileOpId: 'edit-1', name: 'Edit', filePath: 'src/live.js' },
    ],
    fileOps: [{ id: 'edit-1', status: 'running', filePath: 'src/live.js' }],
  })
  assert.equal(running.readFiles, 0)
  assert.equal(running.editedFiles, 0)
  assert.equal(running.commands, 0)

  const settled = summarizeResponseExecution({
    contentBlocks: [
      { type: 'tool_use', id: 'read-1', name: 'Read', status: 'success', input: { file_path: 'src/live.js' } },
      { type: 'tool_use', id: 'bash-1', name: 'Bash', status: 'success', input: { command: 'npm test' } },
      { type: 'file_ref', id: 'file-ref-edit-1', fileOpId: 'edit-1', name: 'Edit', filePath: 'src/live.js' },
    ],
    fileOps: [{ id: 'edit-1', status: 'success', filePath: 'src/live.js' }],
  })
  assert.equal(settled.readFiles, 1)
  assert.equal(settled.editedFiles, 1)
  assert.equal(settled.commands, 1)
})

test('counts AskUserQuestion prompts without treating approval tools as questions', () => {
  const summary = summarizeResponseExecution({
    contentBlocks: [
      { type: 'tool_use', id: 'plan-1', name: 'ExitPlanMode', input: {} },
      { type: 'tool_use', id: 'permission-1', name: 'Write', input: { file_path: 'notes.md' } },
      { type: 'ask_user', id: 'ask-1', questions: [{ question: 'Continue?' }] },
    ],
  })

  assert.equal(summary.questions, 1)
})

test('does not count failed AskUserQuestion attempts', () => {
  const summary = summarizeResponseExecution({
    contentBlocks: [
      {
        type: 'ask_user',
        id: 'ask-invalid',
        status: 'error',
        questions: [{ question: 'This was never shown' }],
      },
      {
        type: 'tool_use',
        id: 'ask-tool-invalid',
        name: 'AskUserQuestion',
        status: 'error',
        input: { questions: [{ question: 'This also failed' }] },
      },
    ],
  })

  assert.equal(summary.questions, 0)
})

test('only exposes non-zero summary metrics for display', () => {
  assert.deepEqual(visibleExecutionSummaryItems({
    duration: '12s',
    readFiles: 3,
    editedFiles: 0,
    commands: 2,
    questions: 0,
  }), [
    { key: 'duration', value: '12s' },
    { key: 'readFiles', value: 3 },
    { key: 'commands', value: 2 },
  ])

  assert.deepEqual(visibleExecutionSummaryItems({
    duration: '0s',
    readFiles: 0,
    editedFiles: 0,
    commands: 0,
    questions: 0,
  }), [])
})
