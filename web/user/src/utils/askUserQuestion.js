function normalizedQuestions(questions) {
  return Array.isArray(questions) ? questions : []
}

export function sameAskUserQuestions(left, right) {
  return JSON.stringify(normalizedQuestions(left)) === JSON.stringify(normalizedQuestions(right))
}

export function findMatchingAskUserBlockIndex(blocks, { toolUseId, questions }) {
  const list = Array.isArray(blocks) ? blocks : []

  if (toolUseId) {
    const exact = list.findIndex((block) => (
      block?.type === 'ask_user'
      && (block.toolUseId === toolUseId || block.id === toolUseId)
    ))
    if (exact >= 0) return exact
  }

  // Compatibility with older runners whose permission_request payload did
  // not expose the SDK tool_use_id. Prefer the newest still-pending block
  // with the exact same question payload.
  for (let index = list.length - 1; index >= 0; index -= 1) {
    const block = list[index]
    if (
      block?.type === 'ask_user'
      && block.status === 'pending'
      && sameAskUserQuestions(block.questions, questions)
    ) return index
  }

  return -1
}

function resultText(value) {
  if (typeof value === 'string') return value
  if (Array.isArray(value)) return value.map(resultText).join(' ')
  if (value && typeof value === 'object') {
    return resultText(value.content ?? value.text ?? value.message ?? '')
  }
  return ''
}

export function isAskUserInputValidationError(result, toolUseResult = null) {
  if (result?.is_error !== true && result?.isError !== true) return false
  const text = `${resultText(result)} ${resultText(toolUseResult)}`
  return /InputValidationError/i.test(text)
}
