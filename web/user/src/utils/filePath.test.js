import assert from 'node:assert/strict'
import test from 'node:test'

import { resolveFilePathAgainstCwd } from './filePath.js'

test('resolves relative file paths against the active session cwd', () => {
  assert.equal(
    resolveFilePathAgainstCwd('docs/guide.md', '/workspace/priva'),
    '/workspace/priva/docs/guide.md',
  )
  assert.equal(
    resolveFilePathAgainstCwd('./docs/guide.md', '/workspace/priva/'),
    '/workspace/priva/docs/guide.md',
  )
})
test('preserves absolute, home-relative, and unresolved paths', () => {
  assert.equal(resolveFilePathAgainstCwd('/tmp/report.pdf', '/workspace/priva'), '/tmp/report.pdf')
  assert.equal(resolveFilePathAgainstCwd('~/notes.txt', '/workspace/priva'), '~/notes.txt')
  assert.equal(resolveFilePathAgainstCwd('README', ''), 'README')
  assert.equal(resolveFilePathAgainstCwd('README', '~'), 'README')
})
