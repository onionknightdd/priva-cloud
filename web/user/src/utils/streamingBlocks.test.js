import test from 'node:test'
import assert from 'node:assert/strict'
import { createStreamingBlockAssembler } from './streamingBlocks.js'

const stream = (event, overrides = {}) => ({
  type: 'stream_event',
  session_id: 'session-1',
  parent_tool_use_id: null,
  uuid: Math.random().toString(36),
  event,
  ...overrides,
})

const messageStart = (id = 'message-1') => stream({
  type: 'message_start',
  message: { id, role: 'assistant', content: [] },
})

function harness(options = {}) {
  const batches = []
  const assembler = createStreamingBlockAssembler({
    onFlush: (batch) => batches.push(batch),
    ...options,
  })
  return { assembler, batches }
}

test('DeepSeek signature-only thinking never reaches patches or logs', () => {
  const { assembler, batches } = harness()
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start',
    index: 0,
    content_block: { type: 'thinking', thinking: '', signature: '' },
  }))
  assembler.accept(stream({
    type: 'content_block_delta',
    index: 0,
    delta: { type: 'signature_delta', signature: 'provider-signature' },
  }))

  const entries = assembler.reconcileAssistant({
    message_id: 'message-1',
    content: [{ type: 'thinking', thinking: '', signature: 'provider-signature' }],
  })
  assembler.accept(stream({ type: 'content_block_stop', index: 0 }))

  assert.equal(entries.length, 1)
  assert.equal(batches.length, 0)
})

test('DeepSeek fragmented tool input is private and full Assistant wins before stop', () => {
  let parseCalls = 0
  const { assembler, batches } = harness({
    parseJson: (value) => {
      parseCalls += 1
      return JSON.parse(value)
    },
  })
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start',
    index: 1,
    content_block: { type: 'tool_use', id: 'call-1', name: 'Bash', input: {} },
  }))
  assembler.flush()

  const chunks = ['{', '"', 'command', '"', ': ', '"', 'echo', ' ', '1', '"', ', ', '"',
    'description', '"', ': ', '"', 'test', '"', '}']
  for (const partial_json of chunks) {
    assembler.accept(stream({
      type: 'content_block_delta',
      index: 1,
      delta: { type: 'input_json_delta', partial_json },
    }))
  }
  assembler.flush()

  const entries = assembler.reconcileAssistant({
    message_id: 'message-1',
    content: [{
      type: 'tool_use', id: 'call-1', name: 'Bash',
      input: { command: 'echo 1', description: 'test' },
    }],
  })
  assembler.accept(stream({ type: 'content_block_stop', index: 1 }))

  assert.equal(entries[0].streamKey.includes('message-1:1'), true)
  assert.equal(parseCalls, 0, 'authoritative Assistant arrives before block_stop')
  assert.equal(batches.length, 1, 'only the generic tool start is materialized')
  assert.equal(batches[0].logs.length, 0)
  assert.deepEqual(batches[0].patches[0].block.input, {})
})

test('tool JSON is parsed once at stop only as an authoritative fallback', () => {
  let parseCalls = 0
  const { assembler, batches } = harness({
    parseJson: (value) => {
      parseCalls += 1
      return JSON.parse(value)
    },
  })
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start', index: 0,
    content_block: { type: 'tool_use', id: 'call-2', name: 'Read', input: {} },
  }))
  assembler.flush()
  assembler.accept(stream({
    type: 'content_block_delta', index: 0,
    delta: { type: 'input_json_delta', partial_json: '{"file_path":"/tmp/a"}' },
  }))
  assembler.accept(stream({ type: 'content_block_stop', index: 0 }))
  assembler.flush()

  assert.equal(parseCalls, 1)
  assert.deepEqual(batches.at(-1).patches[0].block.input, { file_path: '/tmp/a' })
  assert.equal(batches.at(-1).logs.length, 0)
})

test('visible text deltas batch into one cumulative patch and one aggregate log', () => {
  const { assembler, batches } = harness()
  assembler.accept(messageStart('stable-message'))
  assembler.accept(stream({
    type: 'content_block_start', index: 2,
    content_block: { type: 'text', text: '' },
  }, { uuid: 'start-uuid' }))
  for (const [index, text] of ['# ', 'Title', '\n', '- ', 'one', '\n', 'done'].entries()) {
    assembler.accept(stream({
      type: 'content_block_delta', index: 2,
      delta: { type: 'text_delta', text },
    }, { uuid: `unique-${index}` }))
  }
  assembler.flush()

  assert.equal(batches.length, 1)
  assert.equal(batches[0].patches[0].block.text, '# Title\n- one\ndone')
  assert.equal(batches[0].logs[0].content, '# Title\n- one\ndone')
  assert.equal(batches[0].logs[0].eventCount, 7)
  assert.match(batches[0].patches[0].streamKey, /stable-message:2$/)
  assert.doesNotMatch(batches[0].patches[0].streamKey, /unique-/)
})

test('Qwen thinking deltas are visible and reconcile in place', () => {
  let clock = 100
  const { assembler, batches } = harness({ now: () => ++clock })
  assembler.accept(messageStart('qwen-message'))
  assembler.accept(stream({
    type: 'content_block_start', index: 0,
    content_block: { type: 'thinking', thinking: '', signature: 'omlx-reasoning' },
  }))
  for (const thinking of ['The user', ' wants', ' a test.']) {
    assembler.accept(stream({
      type: 'content_block_delta', index: 0,
      delta: { type: 'thinking_delta', thinking },
    }))
  }
  assembler.flush()
  const [entry] = assembler.reconcileAssistant({
    message_id: 'qwen-message',
    content: [{ type: 'thinking', thinking: 'The user wants a test.', signature: '' }],
  })

  assert.equal(batches[0].patches[0].block.thinking, 'The user wants a test.')
  assert.equal(batches[0].logs[0].deltaType, 'thinking_delta')
  assert.equal(entry.streamKey, batches[0].patches[0].streamKey)
})

test('replay gap drops unfinished state and ignores orphan deltas', () => {
  const { assembler, batches } = harness()
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start', index: 0,
    content_block: { type: 'text', text: '' },
  }))
  assembler.accept(stream({
    type: 'content_block_delta', index: 0,
    delta: { type: 'text_delta', text: 'lost' },
  }))
  assembler.resetForReplayGap()
  assembler.accept(stream({
    type: 'content_block_delta', index: 0,
    delta: { type: 'text_delta', text: 'orphan' },
  }))
  assembler.flush()

  assert.equal(batches.length, 0)
  assert.deepEqual(assembler.getState(), { lanes: 0, blocks: 0, dirty: 0, disposed: false })
})

test('a large provider burst still produces a single store batch', () => {
  const { assembler, batches } = harness()
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start', index: 0,
    content_block: { type: 'text', text: '' },
  }))
  for (let index = 0; index < 1000; index += 1) {
    assembler.accept(stream({
      type: 'content_block_delta', index: 0,
      delta: { type: 'text_delta', text: 'x' },
    }))
  }
  assembler.flush()

  assert.equal(batches.length, 1)
  assert.equal(batches[0].patches[0].block.text.length, 1000)
  assert.equal(batches[0].logs[0].eventCount, 1000)
})

test('automatic scheduling waits for one 40ms batch instead of flushing per delta', () => {
  let scheduled = null
  let scheduledDelay = null
  const { assembler, batches } = harness({
    schedule: (callback, delay) => {
      scheduled = callback
      scheduledDelay = delay
      return 1
    },
    cancel: () => {},
  })
  assembler.accept(messageStart())
  assembler.accept(stream({
    type: 'content_block_start', index: 0,
    content_block: { type: 'text', text: '' },
  }))
  for (const text of ['one', ' two', ' three']) {
    assembler.accept(stream({
      type: 'content_block_delta', index: 0,
      delta: { type: 'text_delta', text },
    }))
  }

  assert.equal(batches.length, 0)
  assert.equal(scheduledDelay, 40)
  scheduled()
  assert.equal(batches.length, 1)
  assert.equal(batches[0].patches[0].block.text, 'one two three')
})
