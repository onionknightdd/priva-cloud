const READ_TOOL_NAMES = new Set(['Read', 'NotebookRead'])
const EDIT_TOOL_NAMES = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit'])
const COMMAND_TOOL_NAMES = new Set(['Bash'])
const SUBAGENT_TOOL_NAMES = new Set(['Agent', 'Task'])

function normalizedText(value) {
  return String(value || '')
    .replace(/<think>[\s\S]*?<\/think>/g, '')
    .replace(/\r\n?/g, '\n')
    .replace(/[ \t]+\n/g, '\n')
    .trim()
}

function toolFilePath(block) {
  const input = block?.input || {}
  return input.file_path || input.notebook_path || input.path || null
}

function isFailed(block) {
  return block?.status === 'error'
    || block?.result?.is_error === true
    || block?.result?.isError === true
}

// Live tool_use blocks are inserted before their tool_result arrives. Keep
// those blocks visible in the process stream, but defer execution metrics until
// the matching result has settled. Historical blocks often omit status, so an
// absent status remains compatible with replayed transcripts and fixtures.
function isCompleted(block) {
  return block?.status !== 'running' && block?.status !== 'pending'
}

function questionCount(questions) {
  return Array.isArray(questions) && questions.length > 0 ? questions.length : 1
}

/**
 * Find the final text block(s) represented by the SDK ResultMessage. The SDK
 * streams those blocks as AssistantMessage content first, then sends the
 * authoritative ResultMessage text at the end of the turn.
 */
export function findFinalResultBlockIndexes(contentBlocks, resultText = null) {
  const blocks = Array.isArray(contentBlocks) ? contentBlocks : []
  const textIndexes = []
  blocks.forEach((block, index) => {
    if (block?.type === 'text' && normalizedText(block.text)) textIndexes.push(index)
  })
  if (textIndexes.length === 0) return []

  const lastTextIndex = textIndexes[textIndexes.length - 1]
  const expected = normalizedText(resultText)
  if (!expected) return [lastTextIndex]

  let combined = ''
  const matched = []
  for (let position = textIndexes.length - 1; position >= 0; position -= 1) {
    const index = textIndexes[position]
    // A tool/thinking/content block between text blocks starts a different SDK
    // assistant segment, so it cannot be part of the final result string.
    if (matched.length > 0) {
      const nextIndex = matched[0]
      const hasMeaningfulGap = blocks.slice(index + 1, nextIndex).some((block) => (
        block?.type !== 'text' || normalizedText(block.text)
      ))
      if (hasMeaningfulGap) break
    }

    const text = normalizedText(blocks[index].text)
    combined = combined ? `${text}\n${combined}` : text
    matched.unshift(index)
    if (normalizedText(combined) === expected) return matched
    if (!expected.endsWith(normalizedText(combined))) break
  }

  // Result text can be masked or normalized independently from the preceding
  // assistant event. The last non-empty text block is still the correct visual
  // result slot; callers render the authoritative ResultMessage string.
  return [lastTextIndex]
}

export function resultTextFromBlocks(contentBlocks, resultIndexes) {
  const indexSet = new Set(resultIndexes || [])
  return (Array.isArray(contentBlocks) ? contentBlocks : [])
    .filter((block, index) => indexSet.has(index) && block?.type === 'text')
    .map((block) => normalizedText(block.text))
    .filter(Boolean)
    .join('\n')
    .trim()
}

export function formatExecutionDuration(durationMs) {
  const numeric = Number(durationMs)
  if (!Number.isFinite(numeric) || numeric <= 0) return '0s'

  let seconds = Math.max(1, Math.round(numeric / 1000))
  const hours = Math.floor(seconds / 3600)
  seconds -= hours * 3600
  const minutes = Math.floor(seconds / 60)
  seconds -= minutes * 60

  const parts = []
  if (hours > 0) parts.push(`${hours}h`)
  if (minutes > 0) parts.push(`${minutes}m`)
  if (seconds > 0 || parts.length === 0) parts.push(`${seconds}s`)
  return parts.join(' ')
}

/**
 * Return only the summary metrics that have a meaningful, non-zero value.
 * Keeping this filtering outside the component makes live and replayed
 * responses follow the same display rule.
 */
export function visibleExecutionSummaryItems(summary = {}) {
  const items = []
  if (summary.duration && summary.duration !== '0s') {
    items.push({ key: 'duration', value: summary.duration })
  }

  for (const key of ['readFiles', 'editedFiles', 'commands', 'questions']) {
    const value = Number(summary[key])
    if (Number.isFinite(value) && value > 0) items.push({ key, value })
  }

  return items
}

/**
 * Count successful file reads/edits by unique path and executions/questions by
 * occurrence. Subagent buckets are followed recursively from Agent/Task blocks.
 */
export function summarizeResponseExecution({
  contentBlocks,
  subagentContent = {},
  fileOps = [],
  durationMs = 0,
  additionalQuestionCount = 0,
}) {
  const readFiles = new Set()
  const editedFiles = new Set()
  const visitedToolIds = new Set()
  const visitedSubagents = new Set()
  const visitedQuestionIds = new Set()
  const fileOpsById = new Map((fileOps || []).map((op) => [op.id, op]))
  let commands = 0
  let questions = Math.max(0, Number(additionalQuestionCount) || 0)

  const addPath = (target, value) => {
    const path = typeof value === 'string' ? value.trim() : ''
    if (path) target.add(path)
  }

  const visit = (blocks) => {
    if (!Array.isArray(blocks)) return
    blocks.forEach((block, blockIndex) => {
      if (!block) return

      if (block.type === 'ask_user') {
        const questionKey = block.id || block.toolUseId
        if (questionKey && visitedQuestionIds.has(questionKey)) return
        if (questionKey) visitedQuestionIds.add(questionKey)
        questions += questionCount(block.questions)
        return
      }

      if (block.type === 'file_ref') {
        if (!EDIT_TOOL_NAMES.has(block.name)) return
        const op = block.fileOpId ? fileOpsById.get(block.fileOpId) : null
        if (op && (op.status === 'running' || op.status === 'pending' || op.status === 'error')) return
        addPath(editedFiles, block.filePath || op?.filePath)
        return
      }

      if (block.type !== 'tool_use') return
      const toolKey = block.id || `${block.name || 'tool'}:${blockIndex}:${toolFilePath(block) || ''}`
      if (visitedToolIds.has(toolKey)) return
      visitedToolIds.add(toolKey)

      if (READ_TOOL_NAMES.has(block.name) && isCompleted(block) && !isFailed(block)) {
        addPath(readFiles, toolFilePath(block))
      }
      if (EDIT_TOOL_NAMES.has(block.name) && isCompleted(block) && !isFailed(block)) {
        addPath(editedFiles, toolFilePath(block))
      }
      if (COMMAND_TOOL_NAMES.has(block.name) && isCompleted(block)) commands += 1
      if (block.name === 'AskUserQuestion') {
        if (block.id && visitedQuestionIds.has(block.id)) return
        if (block.id) visitedQuestionIds.add(block.id)
        questions += questionCount(block.input?.questions)
      }

      if (SUBAGENT_TOOL_NAMES.has(block.name) && block.id && !visitedSubagents.has(block.id)) {
        visitedSubagents.add(block.id)
        visit(subagentContent[block.id])
      }
    })
  }

  visit(contentBlocks)

  return {
    duration: formatExecutionDuration(durationMs),
    readFiles: readFiles.size,
    editedFiles: editedFiles.size,
    commands,
    questions,
  }
}
