import {
  GENERATED_TOOL_NAME,
  buildGeneratedFileOpId,
  getGeneratedInputPaths,
  isGeneratedToolName,
} from './generatedTool'
import {
  FILE_SOURCE_PAST,
  fileTabFromToolUse,
  fileTabsFromGeneratedFiles,
  shouldOpenToolFileInBrowser,
} from './fileArtifacts'
import { parseTaskNotification } from './taskNotification'
import {
  beginSdkTaskRound,
  createSdkTaskTrackerState,
  hasSdkTaskTrackerRounds,
  isSdkTaskToolName,
  recordSdkTaskToolResult,
  recordSdkTaskToolUse,
} from './sdkTaskTracker'

// Monotonic counter for `_cid` (stable React list keys). 's-' prefix keeps
// load-path ids distinct from chatStore's live 'c-' ids.
let cidCounter = 0

function getContentBlocks(msg) {
  const content = msg.message
  if (typeof content === 'string') return [{ type: 'text', text: content }]
  if (Array.isArray(content)) return content
  if (content && typeof content === 'object') {
    const inner = content.content
    if (typeof inner === 'string') return [{ type: 'text', text: inner }]
    if (Array.isArray(inner)) return inner
  }
  return []
}

function stripCommandEnvelope(text) {
  if (!text) return text
  if (!/<command-message>/.test(text) && !/<command-name>/.test(text)) return text
  const nameMatch = text.match(/<command-name>\s*(.*?)\s*<\/command-name>/)
  const argsMatch = text.match(/<command-args>([\s\S]*?)<\/command-args>/)
  const name = nameMatch ? nameMatch[1].replace(/^\//, '') : ''
  const args = argsMatch ? argsMatch[1].trim() : ''
  return name ? `/${name} ${args}`.trim() : text
}

function extractUserText(blocks) {
  if (typeof blocks === 'string') return stripCommandEnvelope(blocks)
  if (!Array.isArray(blocks)) return ''
  return blocks
    .filter((b) => b.type === 'text')
    .map((b) => stripCommandEnvelope(b.text))
    .join('\n')
}

function finiteNumber(value) {
  if (value == null || value === '') return null
  const n = Number(value)
  return Number.isFinite(n) ? n : null
}

function timestampToMillis(value) {
  if (value == null || value === '') return null
  if (typeof value === 'number' && Number.isFinite(value)) return value
  const ms = Date.parse(String(value))
  return Number.isFinite(ms) ? ms : null
}

function getAssistantReplayMetadata(msg) {
  const source = msg?.metadata || {}
  const message = msg?.message && typeof msg.message === 'object' ? msg.message : {}
  const usage = source.usage || message.usage || {}
  const metadata = {}

  const inputTokens = finiteNumber(usage.input_tokens)
  const outputTokens = finiteNumber(usage.output_tokens)
  const duration = finiteNumber(source.duration_ms ?? source.durationMs ?? msg.duration_ms)
  const agentLoops = finiteNumber(source.agent_loops ?? source.agentLoops ?? source.num_turns)
  const timestamp = timestampToMillis(source.timestamp ?? msg.timestamp)

  if (inputTokens != null) metadata.inputTokens = inputTokens
  if (outputTokens != null) metadata.outputTokens = outputTokens
  if (duration != null) metadata.duration = duration
  if (agentLoops != null) metadata.agentLoops = agentLoops
  if (timestamp != null) metadata.timestamp = timestamp

  return metadata
}

function getUserReplayMetadata(msg) {
  const source = msg?.metadata || {}
  const timestamp = timestampToMillis(source.timestamp ?? msg.timestamp)
  return timestamp != null ? { timestamp } : {}
}

const HIDDEN_TOOLS = new Set([
  'Write', 'Edit', 'TaskOutput', 'TaskStop',
  'BashOutput', 'KillBash', 'ExitPlanMode', 'AskUserQuestion',
  'TaskCreate', 'TaskUpdate', 'TaskGet', 'TaskList',
])

/**
 * Rebuild an *answered* ask_user block on session replay.
 *
 * The live answered-card state (answeredText/answeredSelections) is React
 * state and is never persisted — only the AskUserQuestion tool_result is in
 * the session JSONL. The backend attaches the raw toolUseResult
 * ({questions, answers:{question_text: answer_string}}) onto the tool_result
 * block as `tool_use_result` for replay consumers
 * (agent.py::_with_inline_tool_use_result). We reconstruct the selection from
 * that map so MessageBubble renders the answered AskUserQuestionCard exactly
 * as it looked live. Returns null when there is no recorded answer (declined
 * / legacy empty) — caller then keeps it hidden as before.
 */
function extractResultText(content) {
  if (typeof content === 'string') return content
  if (Array.isArray(content)) {
    return content
      .map((b) => (typeof b === 'string' ? b : b?.text || b?.content || ''))
      .join(' ')
  }
  return ''
}

function buildAnsweredAskUserBlock(block, result) {
  const tur = result?.tool_use_result || result?.toolUseResult || {}
  const questions = block.input?.questions || tur.questions || []
  let answers = tur.answers || {}
  if (!Object.keys(answers).length) {
    // Main-session replay does NOT carry the structured toolUseResult
    // (only the subagent path attaches it). Fall back to the CLI's stable
    // result prose: User has answered your questions: "Q1"="A1", "Q2"="A2".
    // "User declined to answer questions" yields no pairs -> stays hidden.
    const text = extractResultText(result?.content)
    const pairs = {}
    const re = /"([^"]+)"\s*=\s*"([^"]*)"/g
    let m
    while ((m = re.exec(text)) !== null) pairs[m[1]] = m[2]
    answers = pairs
  }
  if (!questions.length || !Object.keys(answers).length) return null

  const answeredSelections = {}
  const answeredCustomInputs = {}
  const textLines = []

  questions.forEach((q, qi) => {
    const raw = answers[q.question]
    if (raw == null || raw === '') return
    const val = String(raw)
    // Multi-select answers are comma/semicolon separated (CLI contract).
    const parts = val.split(/[;,]/).map((s) => s.trim()).filter(Boolean)
    const chosen = []
    ;(q.options || []).forEach((opt, oi) => {
      const label = String(opt?.label || '')
      if (!label) return
      const hit = parts.some(
        (p) => p === label || p.startsWith(`${label} `) || p.includes(label),
      ) || val.includes(label)
      if (hit) chosen.push(oi)
    })
    if (chosen.length) {
      answeredSelections[qi] = chosen
    } else {
      // Free-text / custom answer — render as a custom snapshot.
      answeredCustomInputs[qi] = { enabled: true, text: val }
    }
    textLines.push(`- ${q.header || q.question} -> ${val}`)
  })

  if (!Object.keys(answeredSelections).length && !Object.keys(answeredCustomInputs).length) {
    return null
  }

  return {
    type: 'ask_user',
    id: block.id,
    toolUseId: block.id,
    questions,
    status: 'answered',
    answeredText: textLines.join('\n'),
    answeredSelections,
    answeredCustomInputs,
  }
}

function getGeneratedTabs(block, result) {
  const resultFiles = Array.isArray(result?.tool_use_result?.files)
    ? result.tool_use_result.files
    : Array.isArray(result?.files) ? result.files : []
  if (resultFiles.length > 0) {
    return fileTabsFromGeneratedFiles(resultFiles, FILE_SOURCE_PAST, block.id)
  }
  return fileTabsFromGeneratedFiles(
    getGeneratedInputPaths(block.input).map((filePath) => ({ path: filePath })),
    FILE_SOURCE_PAST,
    block.id,
  )
}

export function transformSessionMessages(sdkMessages) {
  const resultMap = {}
  // Workflow tool_use ids — used to tag a <task-notification> as a workflow (vs
  // a background Bash) so the replayed card shows the right glyph/label.
  const workflowToolIds = new Set()
  for (const msg of sdkMessages) {
    const blocks = getContentBlocks(msg)
    for (const b of blocks) {
      if (b.type === 'tool_result' && b.tool_use_id) {
        resultMap[b.tool_use_id] = b
      } else if (b.type === 'tool_use' && b.name === 'Workflow' && b.id) {
        workflowToolIds.add(b.id)
      }
    }
  }

  const messages = []
  const fileOps = []
  const fileBrowserTabs = []
  const tasks = []
  let sdkTaskTracker = createSdkTaskTrackerState()
  // Main-thread tool id -> conversation round. Sidechain task tools use their
  // parent Agent/Task id to land in the same round during historical replay.
  const toolRoundMap = {}
  // Subagent content map: parent_tool_use_id -> flat array of content blocks
  const subagentContent = {}

  for (const msg of sdkMessages) {
    // Subagent assistant messages: collect under parent_tool_use_id instead of
    // flattening into the main message thread.
    if (msg.type === 'assistant' && msg.parent_tool_use_id) {
      const parentId = msg.parent_tool_use_id
      const rawBlocks = getContentBlocks(msg)
      const out = subagentContent[parentId] || (subagentContent[parentId] = [])
      for (const block of rawBlocks) {
        if (block.type === 'text' || block.type === 'thinking') {
          out.push(block)
        } else if (block.type === 'tool_use') {
          const result = resultMap[block.id]
          if (isSdkTaskToolName(block.name)) {
            sdkTaskTracker = recordSdkTaskToolUse(sdkTaskTracker, block, {
              roundId: toolRoundMap[parentId] || sdkTaskTracker.currentRoundId,
            })
            if (result) {
              sdkTaskTracker = recordSdkTaskToolResult(
                sdkTaskTracker,
                block.id,
                result,
                result.tool_use_result || result.toolUseResult,
              )
            }
            continue
          }
          const isError = result?.is_error || false
          const fileTab = shouldOpenToolFileInBrowser(block, result)
            ? fileTabFromToolUse(block, FILE_SOURCE_PAST)
            : null
          if (fileTab) fileBrowserTabs.push(fileTab)
          if (isGeneratedToolName(block.name)) {
            fileBrowserTabs.push(...getGeneratedTabs(block, result))
          }
          out.push({
            ...block,
            status: isError ? 'error' : 'success',
            result: result || undefined,
          })
        }
      }
      continue
    }
    if (msg.type === 'user') {
      const blocks = getContentBlocks(msg)
      const text = extractUserText(blocks)
      // CLI-injected <task-notification> (a background Workflow / Bash finished
      // and re-invoked the model) → slim system card, not a raw-XML user bubble.
      // The following assistant message stays its own turn (the summary).
      const notif = parseTaskNotification(text)
      if (notif) {
        const kind = notif.toolUseId && workflowToolIds.has(notif.toolUseId)
          ? 'workflow' : 'task'
        messages.push({
          role: 'system',
          type: 'task_notification',
          notif: { ...notif, kind },
          uuid: msg.uuid,
        })
        continue
      }
      const imageBlocks = Array.isArray(blocks)
        ? blocks.filter((b) => b.type === 'image')
        : []
      if (!text.trim() && imageBlocks.length === 0) continue
      const content = []
      if (imageBlocks.length > 0) content.push(...imageBlocks)
      if (text.trim()) content.push({ type: 'text', text })
      sdkTaskTracker = beginSdkTaskRound(sdkTaskTracker, {
        title: text || 'Image prompt',
        startedAt: getUserReplayMetadata(msg).timestamp,
      })
      messages.push({ role: 'user', content, uuid: msg.uuid, ...getUserReplayMetadata(msg) })

    } else if (msg.type === 'assistant') {
      const rawBlocks = getContentBlocks(msg)
      const outputBlocks = []
      const replayMetadata = getAssistantReplayMetadata(msg)
      let hasSdkTaskActivity = false
      let hiddenQuestionCount = 0

      for (const block of rawBlocks) {
        if (block.type === 'thinking') {
          outputBlocks.push(block)
          continue
        }
        if (block.type === 'text') {
          outputBlocks.push(block)
          continue
        }
        if (block.type !== 'tool_use') {
          outputBlocks.push(block)
          continue
        }

        const result = resultMap[block.id]
        const isError = result?.is_error || false
        const status = isError ? 'error' : 'success'
        toolRoundMap[block.id] = sdkTaskTracker.currentRoundId

        if (isSdkTaskToolName(block.name)) {
          hasSdkTaskActivity = true
          sdkTaskTracker = recordSdkTaskToolUse(sdkTaskTracker, block)
          if (result) {
            sdkTaskTracker = recordSdkTaskToolResult(
              sdkTaskTracker,
              block.id,
              result,
              result.tool_use_result || result.toolUseResult,
            )
          }
          // SDK task tools are represented only by Composer/Canvas trackers.
          // They never create a message-flow card or placeholder.
          continue
        }
        const fileTab = shouldOpenToolFileInBrowser(block, result)
          ? fileTabFromToolUse(block, FILE_SOURCE_PAST)
          : null
        if (fileTab) fileBrowserTabs.push(fileTab)

        if (block.name === 'Write' || block.name === 'Edit') {
          fileOps.push({
            id: block.id,
            type: block.name.toLowerCase(),
            filePath: block.input?.file_path || '',
            status,
            input: block.input,
            content: null,
            originalFile: null,
            structuredPatch: null,
            toolUseResult: null,
            resultContent: typeof result?.content === 'string' ? result.content : null,
          })
          outputBlocks.push({
            type: 'file_ref',
            id: `file-ref-${block.id}`,
            fileOpId: block.id,
            name: block.name,
            filePath: block.input?.file_path || '',
          })
          continue
        }

        if (isGeneratedToolName(block.name)) {
          const generatedFiles = getGeneratedTabs(block, result)

          generatedFiles.forEach((file, index) => {
            const generatedOpId = buildGeneratedFileOpId(block.id, index)
            fileBrowserTabs.push(file)
            outputBlocks.push({
              type: 'file_ref',
              id: `file-ref-${generatedOpId}`,
              name: GENERATED_TOOL_NAME,
              filePath: file.filePath,
            })
          })
          continue
        }

        const TASK_TOOLS = ['TaskOutput', 'TaskStop']
        const isCanvasTracked =
          block.input?.run_in_background === true ||
          TASK_TOOLS.includes(block.name)

        if (isCanvasTracked) {
          // Background-shell / managed task tools still get a taskStore entry
          // so live shell-output tracking keeps working, but are also rendered
          // inline as regular tool cards so users see them in the timeline.
          const description =
            block.input?.description || block.input?.command || block.name
          tasks.push({
            tool_use_id: block.id,
            name: block.name,
            input: block.input,
            status,
            description,
            result: result || undefined,
          })
          outputBlocks.push({
            ...block,
            status,
            result: result || undefined,
          })
          continue
        }

        if (block.name === 'AskUserQuestion') {
          // Reconstruct the answered card so the user's feedback survives a
          // session reload. MessageBubble renders ask_user blocks when
          // status === 'answered'. Null (declined / no recorded answer) ->
          // stays hidden, matching prior behaviour.
          const answered = buildAnsweredAskUserBlock(block, result)
          if (answered) {
            outputBlocks.push(answered)
          } else {
            const questions = block.input?.questions
            hiddenQuestionCount += Array.isArray(questions) && questions.length > 0
              ? questions.length
              : 1
          }
          continue
        }

        // ExitPlanMode is represented by an interactive plan-approval prompt
        // live, but the tool itself is hidden from the transcript UI. It is an
        // approval request, not an AskUserQuestion, so it is not included in
        // the question total.
        if (block.name === 'ExitPlanMode') {
          continue
        }

        if (HIDDEN_TOOLS.has(block.name)) {
          // Genuinely hidden tools (BashOutput / KillBash / ExitPlanMode /
          // TaskOutput / TaskStop). No inline block.
          continue
        }

        outputBlocks.push({
          ...block,
          status,
          result: result || undefined,
        })
      }

      const prev = messages[messages.length - 1]
      if (prev && prev.role === 'assistant') {
        prev.content = [...prev.content, ...outputBlocks]
        if (hasSdkTaskActivity) prev.hasSdkTaskActivity = true
        if (hiddenQuestionCount > 0) {
          prev.summaryQuestionCount = (Number(prev.summaryQuestionCount) || 0) + hiddenQuestionCount
        }
        const { timestamp, ...restMetadata } = replayMetadata
        Object.assign(prev, restMetadata)
        if (prev.timestamp == null && timestamp != null) prev.timestamp = timestamp
      } else {
        messages.push({
          role: 'assistant',
          content: outputBlocks,
          ...(hasSdkTaskActivity ? { hasSdkTaskActivity: true } : {}),
          ...(hiddenQuestionCount > 0 ? { summaryQuestionCount: hiddenQuestionCount } : {}),
          ...replayMetadata,
        })
      }
    }
  }

  // Stable client ids so list keys survive rewind/fork truncation (index keys
  // would swap bubble contents when earlier messages are removed).
  for (const m of messages) {
    // The history endpoint returns completed turns without the live SSE
    // ResultMessage envelope. Mark replayed assistant turns as complete so
    // the UI can apply the same process folding used for live responses.
    if (m.role === 'assistant') m.replayComplete = true
    if (!m._cid) m._cid = `s-${++cidCounter}`
  }

  return {
    messages,
    fileOps,
    fileBrowserTabs,
    tasks,
    sdkTaskTracker,
    subagentContent,
  }
}

export function hasCanvasInspectorItems(messages, sdkTaskTracker = null) {
  return hasSdkTaskTrackerRounds(sdkTaskTracker) || messages.some((msg) => (
    msg.role === 'assistant' &&
    Array.isArray(msg.content) &&
    msg.content.some((block) => (
      block?.type === 'tool_use' &&
      (block.name === 'Agent' || block.name === 'Task' || block.name === 'TodoWrite')
    ))
  ))
}
