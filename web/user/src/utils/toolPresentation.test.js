import test from 'node:test'
import assert from 'node:assert/strict'
import {
  buildDiffRows,
  formatDuration,
  getMcpIdentity,
  getRunMetrics,
  getToolPresentation,
  toProjectRelativePath,
} from './toolPresentation.js'

test('formats durations with spaced second, minute, and hour units', () => {
  assert.equal(formatDuration(250), '0.3 s')
  assert.equal(formatDuration(1_000), '1 s')
  assert.equal(formatDuration(65_000), '1 m 5 s')
  assert.equal(formatDuration(3_725_000), '1 h 2 m 5 s')
})

test('formats MCP tools as server and final method segments', () => {
  assert.deepEqual(getMcpIdentity('mcp__github__get_issue'), {
    server: 'github',
    method: 'get_issue',
    rawName: 'mcp__github__get_issue',
  })
  assert.deepEqual(getMcpIdentity('mcp__team__nested__method'), {
    server: 'team__nested',
    method: 'method',
    rawName: 'mcp__team__nested__method',
  })
})

test('prefers an explicit or cwd-relative project path', () => {
  assert.equal(
    toProjectRelativePath('/workspace/priva/web/user/src/App.jsx', '/workspace/priva'),
    'web/user/src/App.jsx',
  )
  assert.equal(
    toProjectRelativePath('/workspace/priva/web/user/src/App.jsx', '/workspace/priva', './src/App.jsx'),
    'src/App.jsx',
  )
  assert.equal(toProjectRelativePath('src/App.jsx', '/workspace/priva'), 'src/App.jsx')
})

test('uses a Bash description as summary while keeping the command copyable', () => {
  const presentation = getToolPresentation({
    type: 'tool_use',
    name: 'Bash',
    status: 'success',
    input: { command: 'npm run build:user', description: 'Build the user UI' },
  })

  assert.equal(presentation.name, 'Bash')
  assert.equal(presentation.summary, 'Build the user UI')
  assert.equal(presentation.summaryIsCode, false)
  assert.equal(presentation.copyValue, 'npm run build:user')
})

test('builds semantic Write and Edit diffs without colored backgrounds', () => {
  const writeRows = buildDiffRows({
    name: 'Write',
    input: { file_path: 'src/new.js', content: 'one\ntwo' },
  })
  assert.deepEqual(writeRows.map((row) => row.kind), ['add', 'add'])
  assert.deepEqual(writeRows.map((row) => row.text), ['+one', '+two'])

  const editRows = buildDiffRows({
    name: 'Edit',
    input: { file_path: 'src/a.js', old_string: 'old', new_string: 'new' },
  })
  assert.deepEqual(editRows.map((row) => row.kind), ['remove', 'add'])
  assert.deepEqual(editRows.map((row) => row.text), ['-old', '+new'])
})

test('keeps a live group timer moving between consecutive tool calls', () => {
  const run = [
    { id: 'read', name: 'Read', status: 'success', startTime: 1_000, duration: 250 },
    { id: 'bash', name: 'Bash', status: 'success', startTime: 1_400, duration: 300 },
  ]

  const live = getRunMetrics(run, [], 2_000, true)
  assert.equal(live.count, 2)
  assert.equal(live.duration, 1_000)

  const complete = getRunMetrics(run, [], 2_000, false)
  assert.equal(complete.duration, 700)
})

test('reports only the failed summary segment as a failure count', () => {
  const metrics = getRunMetrics([
    { id: 'ok', name: 'Read', status: 'success' },
    { id: 'bad', name: 'Bash', status: 'error', result: { is_error: true } },
  ], [])

  assert.equal(metrics.count, 2)
  assert.equal(metrics.failed, 1)
  assert.equal(metrics.hasRunning, false)
})
