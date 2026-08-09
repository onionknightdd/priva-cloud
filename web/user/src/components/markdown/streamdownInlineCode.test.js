import assert from 'node:assert/strict'
import test from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { Streamdown } from 'streamdown'

test('Streamdown routes code spans separately from single-line fenced code', () => {
  const components = {
    inlineCode: ({ children }) => createElement('code', { 'data-probe-inline': 'true' }, children),
    code: ({ children }) => createElement('code', { 'data-probe-block': 'true' }, children),
  }
  const markdown = 'Open `docs/guide.md`.\n\n```\ndocs/guide.md\n```'
  const html = renderToStaticMarkup(createElement(Streamdown, {
    mode: 'static',
    controls: false,
    components,
  }, markdown))

  assert.equal((html.match(/data-probe-inline="true"/g) || []).length, 1)
  assert.equal((html.match(/data-probe-block="true"/g) || []).length, 1)
})
