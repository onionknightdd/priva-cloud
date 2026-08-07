import assert from 'node:assert/strict'
import test from 'node:test'

import {
  findMatchingAskUserBlockIndex,
  isAskUserInputValidationError,
  sameAskUserQuestions,
} from './askUserQuestion.js'

const questions = [{ question: 'Continue?', options: [] }]

test('matches permission events to the SDK tool block', () => {
  const blocks = [
    { type: 'ask_user', id: 'tool-1', toolUseId: 'tool-1', questions, status: 'pending' },
  ]

  assert.equal(findMatchingAskUserBlockIndex(blocks, {
    toolUseId: 'tool-1',
    questions,
  }), 0)
})

test('falls back to matching pending questions for older runners', () => {
  const blocks = [
    { type: 'ask_user', id: 'tool-1', toolUseId: 'tool-1', questions, status: 'pending' },
  ]

  assert.equal(findMatchingAskUserBlockIndex(blocks, {
    toolUseId: 'permission-1',
    questions: structuredClone(questions),
  }), 0)
  assert.equal(sameAskUserQuestions(questions, structuredClone(questions)), true)
})

test('identifies input validation failures without treating user declines as invalid', () => {
  assert.equal(isAskUserInputValidationError({
    is_error: true,
    content: '<tool_use_error>InputValidationError: invalid answers</tool_use_error>',
  }), true)
  assert.equal(isAskUserInputValidationError({
    is_error: true,
    content: 'User declined to answer questions',
  }), false)
})
