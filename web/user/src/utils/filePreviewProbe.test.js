import assert from 'node:assert/strict'
import test from 'node:test'

import { createFilePreviewProbe } from './filePreviewProbe.js'

test('deduplicates concurrent probes and caches successful metadata', async () => {
  let calls = 0
  const release = []
  const probe = createFilePreviewProbe((path) => {
    calls += 1
    return new Promise((resolve) => release.push(() => resolve({ path, name: 'guide.md' })))
  })

  const first = probe('/workspace/guide.md')
  const second = probe('/workspace/guide.md')
  await Promise.resolve()
  assert.equal(calls, 1)

  release.shift()()
  const [firstResult, secondResult] = await Promise.all([first, second])
  assert.deepEqual(firstResult, secondResult)
  assert.equal((await probe('/workspace/guide.md')).name, 'guide.md')
  assert.equal(calls, 1)
})

test('expires negative results sooner than positive results', async () => {
  let clock = 0
  let calls = 0
  const probe = createFilePreviewProbe(async (path) => {
    calls += 1
    return calls === 1 ? null : { path }
  }, {
    now: () => clock,
    negativeTtlMs: 5,
    positiveTtlMs: 50,
  })

  assert.equal(await probe('/workspace/later.md'), null)
  clock = 4
  assert.equal(await probe('/workspace/later.md'), null)
  assert.equal(calls, 1)

  clock = 6
  assert.deepEqual(await probe('/workspace/later.md'), { path: '/workspace/later.md' })
  clock = 40
  assert.deepEqual(await probe('/workspace/later.md'), { path: '/workspace/later.md' })
  assert.equal(calls, 2)
})

test('bounds the number of active preview requests', async () => {
  let active = 0
  let maxActive = 0
  const releases = []
  const probe = createFilePreviewProbe(() => {
    active += 1
    maxActive = Math.max(maxActive, active)
    return new Promise((resolve) => releases.push(() => {
      active -= 1
      resolve(null)
    }))
  }, { maxConcurrency: 2 })

  const pending = [probe('/a'), probe('/b'), probe('/c')]
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(maxActive, 2)
  assert.equal(releases.length, 2)

  releases.splice(0).forEach((release) => release())
  await new Promise((resolve) => setImmediate(resolve))
  assert.equal(maxActive, 2)
  assert.equal(releases.length, 1)

  releases.shift()()
  await Promise.all(pending)
})

test('keeps cache namespaces isolated while probing the original path', async () => {
  const calls = []
  const probe = createFilePreviewProbe(async (path) => {
    calls.push(path)
    return { path }
  })

  await probe('/workspace/report.md', 'alice\0/workspace/report.md')
  await probe('/workspace/report.md', 'bob\0/workspace/report.md')
  assert.deepEqual(calls, ['/workspace/report.md', '/workspace/report.md'])
})
