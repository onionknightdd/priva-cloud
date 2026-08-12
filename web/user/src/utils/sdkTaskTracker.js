export const SDK_TASK_TOOL_NAMES = new Set([
  'TaskCreate',
  'TaskUpdate',
  'TaskGet',
  'TaskList',
])

const TERMINAL_STATUSES = new Set(['completed', 'deleted'])

function own(object, key) {
  return Object.prototype.hasOwnProperty.call(object || {}, key)
}

function asId(value) {
  if (value == null || value === '') return null
  return String(value)
}

function asList(value) {
  if (Array.isArray(value)) return value.map(asId).filter(Boolean)
  if (value == null || value === '') return []
  return [String(value)]
}

function mergeUnique(current, added) {
  return [...new Set([...asList(current), ...asList(added)])]
}

function resultText(resultBlock) {
  const content = resultBlock?.content
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((item) => {
      if (typeof item === 'string') return item
      return item?.text || item?.content || ''
    })
    .filter(Boolean)
    .join('\n')
}

function structuredResult(resultBlock, toolUseResult) {
  if (toolUseResult && typeof toolUseResult === 'object') return toolUseResult
  const inline = resultBlock?.tool_use_result || resultBlock?.toolUseResult
  return inline && typeof inline === 'object' ? inline : {}
}

function parseJsonResult(text) {
  const trimmed = String(text || '').trim()
  if (!trimmed || (!trimmed.startsWith('{') && !trimmed.startsWith('['))) return null
  try {
    return JSON.parse(trimmed)
  } catch {
    return null
  }
}

function normalizeStatus(value) {
  const status = String(value || 'pending').toLowerCase().replace(/[\s-]+/g, '_')
  if (status === 'running' || status === 'active') return 'in_progress'
  if (status === 'done' || status === 'success') return 'completed'
  if (status === 'waiting') return 'pending'
  if (status === 'blocked') return 'blocked'
  if (status === 'deleted') return 'deleted'
  if (status === 'in_progress' || status === 'completed' || status === 'pending') return status
  return 'pending'
}

function normalizeTask(raw, fallback = {}) {
  const source = raw && typeof raw === 'object' ? raw : {}
  const id = asId(source.id ?? source.taskId ?? source.task_id ?? fallback.id)
  if (!id) return null
  const blockedBy = asList(source.blockedBy ?? source.blocked_by ?? fallback.blockedBy)
  const blocks = asList(source.blocks ?? fallback.blocks)
  return {
    ...fallback,
    id,
    subject: source.subject ?? fallback.subject ?? `Task #${id}`,
    description: source.description ?? fallback.description ?? '',
    activeForm: source.activeForm ?? source.active_form ?? fallback.activeForm ?? '',
    owner: source.owner ?? fallback.owner ?? '',
    metadata: source.metadata ?? fallback.metadata ?? null,
    status: normalizeStatus(source.status ?? fallback.status),
    blocks,
    blockedBy,
    provisional: Boolean(source.provisional ?? fallback.provisional),
  }
}

function patchTaskFromUpdate(task, input) {
  const next = { ...task }
  if (own(input, 'subject')) next.subject = input.subject || next.subject
  if (own(input, 'description')) next.description = input.description || ''
  if (own(input, 'activeForm')) next.activeForm = input.activeForm || ''
  if (own(input, 'owner')) next.owner = input.owner || ''
  if (own(input, 'metadata')) next.metadata = input.metadata ?? null
  if (own(input, 'status')) next.status = normalizeStatus(input.status)
  if (own(input, 'addBlocks')) next.blocks = mergeUnique(next.blocks, input.addBlocks)
  if (own(input, 'addBlockedBy')) next.blockedBy = mergeUnique(next.blockedBy, input.addBlockedBy)
  return normalizeTask(next)
}

function upsertRoundTask(round, task, replaceKey = null) {
  if (!task) return round
  const taskKey = task._key || task.id
  const tasks = { ...round.tasks }
  let taskOrder = [...round.taskOrder]
  if (replaceKey && replaceKey !== taskKey) {
    delete tasks[replaceKey]
    taskOrder = taskOrder.map((id) => (id === replaceKey ? taskKey : id))
  }
  tasks[taskKey] = { ...task, _key: taskKey }
  if (!taskOrder.includes(taskKey)) taskOrder.push(taskKey)
  return { ...round, tasks, taskOrder }
}

function removeRoundTask(round, taskKey) {
  if (!round.tasks[taskKey]) return round
  const tasks = { ...round.tasks }
  delete tasks[taskKey]
  return {
    ...round,
    tasks,
    taskOrder: round.taskOrder.filter((id) => id !== taskKey),
  }
}

function upsertCanonicalTask(state, task) {
  if (!task) return state
  const canonicalTasks = { ...state.canonicalTasks, [task.id]: { ...task, _key: task.id } }
  const canonicalTaskOrder = state.canonicalTaskOrder.includes(task.id)
    ? state.canonicalTaskOrder
    : [...state.canonicalTaskOrder, task.id]
  return { ...state, canonicalTasks, canonicalTaskOrder }
}

function removeCanonicalTask(state, taskId) {
  if (!state.canonicalTasks[taskId]) return state
  const canonicalTasks = { ...state.canonicalTasks }
  delete canonicalTasks[taskId]
  return {
    ...state,
    canonicalTasks,
    canonicalTaskOrder: state.canonicalTaskOrder.filter((id) => id !== taskId),
  }
}

function findLatestSnapshot(state, taskId) {
  for (let index = state.roundOrder.length - 1; index >= 0; index -= 1) {
    const task = state.rounds[state.roundOrder[index]]?.tasks?.[taskId]
    if (task) return task
  }
  return null
}

function roundHasOpenCanonicalTask(state, roundId) {
  const round = state.rounds[roundId]
  if (!round) return false
  return round.taskOrder.some((taskKey) => {
    const snapshot = round.tasks[taskKey]
    if (!snapshot || snapshot.provisional) return Boolean(snapshot)
    const current = state.canonicalTasks[snapshot.id] || snapshot
    return !isSdkTaskDone(current)
  })
}

function ensureRound(state, requestedRoundId = null) {
  if (requestedRoundId && state.rounds[requestedRoundId]) {
    return { state, roundId: requestedRoundId }
  }
  if (state.currentRoundId && state.rounds[state.currentRoundId]) {
    return { state, roundId: state.currentRoundId }
  }
  const next = beginSdkTaskRound(state, { title: 'Agent task update' })
  return { state: next, roundId: next.currentRoundId }
}

function parseCreatedTask(input, resultBlock, toolUseResult) {
  const structured = structuredResult(resultBlock, toolUseResult)
  const parsed = parseJsonResult(resultText(resultBlock))
  const resultTask = structured.task || parsed?.task || null
  let id = asId(resultTask?.id)
  if (!id) {
    const match = resultText(resultBlock).match(/Task\s+#([^\s:]+)\s+created/i)
    if (match) id = match[1]
  }
  if (!id) return null
  return normalizeTask({
    ...input,
    ...(resultTask || {}),
    id,
    status: resultTask?.status || 'pending',
  })
}

function parseTaskGet(input, resultBlock, toolUseResult) {
  const structured = structuredResult(resultBlock, toolUseResult)
  const parsed = parseJsonResult(resultText(resultBlock))
  const task = structured.task || parsed?.task || (
    parsed && !Array.isArray(parsed) ? parsed : null
  )
  if (!task) return null
  return normalizeTask(task, { id: asId(input?.taskId) })
}

function parseTaskList(resultBlock, toolUseResult) {
  const structured = structuredResult(resultBlock, toolUseResult)
  const parsed = parseJsonResult(resultText(resultBlock))
  const tasks = Array.isArray(structured.tasks)
    ? structured.tasks
    : Array.isArray(parsed?.tasks)
      ? parsed.tasks
      : Array.isArray(parsed)
        ? parsed
        : null
  if (tasks) return tasks.map((task) => normalizeTask(task)).filter(Boolean)
  if (/^\s*No tasks found\s*$/i.test(resultText(resultBlock))) return []
  return null
}

export function isSdkTaskToolName(name) {
  return SDK_TASK_TOOL_NAMES.has(name)
}

export function createSdkTaskTrackerState() {
  return {
    rounds: {},
    roundOrder: [],
    currentRoundId: null,
    nextRoundNumber: 1,
    composerRoundIds: [],
    canonicalTasks: {},
    canonicalTaskOrder: [],
    toolUses: {},
  }
}

export function summarizeSdkTaskRoundTitle(value) {
  const text = String(value || '')
    .replace(
      /<(uploaded-files|selected-file|selected-xlsx|file-reference|quote-content)\b[^>]*>[\s\S]*?<\/\1>/gi,
      ' ',
    )
    .replace(/\[Attached files[^\]]*\]\s*\n```[\s\S]*?```/gi, ' ')
    .replace(/<[^>]+>/g, ' ')
    .replace(/\s+/g, ' ')
    .trim()
  if (!text) return 'Agent task update'
  return text.length > 72 ? `${text.slice(0, 71)}…` : text
}

export function beginSdkTaskRound(state, options = {}) {
  const base = state || createSdkTaskTrackerState()
  const number = Number.isFinite(options.number) ? options.number : base.nextRoundNumber
  const id = String(options.id || `round-${number}`)
  const existing = base.rounds[id]
  const round = existing || {
    id,
    number,
    title: summarizeSdkTaskRoundTitle(options.title),
    startedAt: options.startedAt || Date.now(),
    hasTaskEvents: false,
    tasks: {},
    taskOrder: [],
  }
  const rounds = {
    ...base.rounds,
    [id]: existing
      ? {
        ...existing,
        title: options.title ? summarizeSdkTaskRoundTitle(options.title) : existing.title,
      }
      : round,
  }
  const seeded = { ...base, rounds }
  const composerRoundIds = base.composerRoundIds.filter(
    (roundId) => roundHasOpenCanonicalTask(seeded, roundId),
  )
  return {
    ...seeded,
    roundOrder: base.roundOrder.includes(id) ? base.roundOrder : [...base.roundOrder, id],
    currentRoundId: id,
    nextRoundNumber: Math.max(base.nextRoundNumber, number + 1),
    composerRoundIds,
  }
}

export function recordSdkTaskToolUse(state, block, options = {}) {
  if (!block?.id || !isSdkTaskToolName(block.name)) return state
  const ensured = ensureRound(state || createSdkTaskTrackerState(), options.roundId)
  let next = ensured.state
  const roundId = options.roundId && next.rounds[options.roundId]
    ? options.roundId
    : ensured.roundId
  let round = { ...next.rounds[roundId], hasTaskEvents: true }
  const input = block.input || {}
  let previousTask = null
  let provisionalKey = null

  if (block.name === 'TaskCreate') {
    provisionalKey = `pending:${block.id}`
    round = upsertRoundTask(round, normalizeTask({
      id: provisionalKey,
      subject: input.subject || 'Creating task…',
      description: input.description || '',
      activeForm: input.activeForm || '',
      owner: input.owner || '',
      metadata: input.metadata || null,
      status: 'pending',
      provisional: true,
      _key: provisionalKey,
    }))
  } else if (block.name === 'TaskUpdate') {
    const taskId = asId(input.taskId)
    if (taskId) {
      previousTask = next.canonicalTasks[taskId] || findLatestSnapshot(next, taskId)
      const baseTask = previousTask || normalizeTask({ id: taskId })
      const updated = patchTaskFromUpdate(baseTask, input)
      next = upsertCanonicalTask(next, updated)
      round = upsertRoundTask(round, updated)
    }
  } else if (block.name === 'TaskGet') {
    const taskId = asId(input.taskId)
    const task = taskId && (next.canonicalTasks[taskId] || findLatestSnapshot(next, taskId))
    if (task) round = upsertRoundTask(round, task)
  } else if (block.name === 'TaskList') {
    for (const taskId of next.canonicalTaskOrder) {
      const task = next.canonicalTasks[taskId]
      if (task) round = upsertRoundTask(round, task)
    }
  }

  return {
    ...next,
    rounds: { ...next.rounds, [roundId]: round },
    composerRoundIds: next.composerRoundIds.includes(roundId)
      ? next.composerRoundIds
      : [...next.composerRoundIds, roundId],
    toolUses: {
      ...next.toolUses,
      [block.id]: {
        name: block.name,
        input,
        roundId,
        previousTask,
        provisionalKey,
      },
    },
  }
}

export function recordSdkTaskToolResult(state, toolUseId, resultBlock, toolUseResult) {
  const entry = state?.toolUses?.[toolUseId]
  if (!entry) return state
  const toolUses = { ...state.toolUses }
  delete toolUses[toolUseId]
  let next = { ...state, toolUses }
  let round = next.rounds[entry.roundId]
  if (!round) return next

  const structured = structuredResult(resultBlock, toolUseResult)
  const failed = Boolean(resultBlock?.is_error || structured.success === false || structured.error)
  if (failed) {
    if (entry.provisionalKey) round = removeRoundTask(round, entry.provisionalKey)
    if (entry.name === 'TaskUpdate') {
      const taskId = asId(entry.input?.taskId)
      if (taskId && entry.previousTask) {
        next = upsertCanonicalTask(next, entry.previousTask)
        round = upsertRoundTask(round, entry.previousTask)
      } else if (taskId) {
        next = removeCanonicalTask(next, taskId)
        round = removeRoundTask(round, taskId)
      }
    }
    return { ...next, rounds: { ...next.rounds, [entry.roundId]: round } }
  }

  if (entry.name === 'TaskCreate') {
    const task = parseCreatedTask(entry.input, resultBlock, toolUseResult)
    if (entry.provisionalKey) round = removeRoundTask(round, entry.provisionalKey)
    if (task) {
      next = upsertCanonicalTask(next, task)
      round = upsertRoundTask(round, task, entry.provisionalKey)
    }
  } else if (entry.name === 'TaskUpdate') {
    const taskId = asId(structured.taskId ?? structured.task_id ?? entry.input?.taskId)
    if (taskId) {
      const current = next.canonicalTasks[taskId] || normalizeTask({ id: taskId })
      const task = patchTaskFromUpdate(current, entry.input)
      next = upsertCanonicalTask(next, task)
      round = upsertRoundTask(round, task)
    }
  } else if (entry.name === 'TaskGet') {
    const task = parseTaskGet(entry.input, resultBlock, toolUseResult)
    if (task) {
      next = upsertCanonicalTask(next, task)
      round = upsertRoundTask(round, task)
    }
  } else if (entry.name === 'TaskList') {
    const tasks = parseTaskList(resultBlock, toolUseResult)
    if (tasks) {
      round = { ...round, tasks: {}, taskOrder: [] }
      for (const task of tasks) {
        const current = next.canonicalTasks[task.id]
        const merged = normalizeTask(task, current || {})
        next = upsertCanonicalTask(next, merged)
        round = upsertRoundTask(round, merged)
      }
    }
  }

  return { ...next, rounds: { ...next.rounds, [entry.roundId]: round } }
}

export function getSdkTaskDisplayStatus(task) {
  const status = normalizeStatus(task?.status)
  if (!TERMINAL_STATUSES.has(status) && asList(task?.blockedBy).length > 0) return 'blocked'
  return status
}

export function isSdkTaskDone(task) {
  return TERMINAL_STATUSES.has(normalizeStatus(task?.status))
}

export function getSdkTaskRoundTasks(round) {
  if (!round) return []
  return round.taskOrder.map((id) => round.tasks[id]).filter(Boolean)
}

function taskSubjectOrdinal(task) {
  const match = String(task?.subject || '').trim().match(/^Task\s*#?(\d+)(?:\s*[:：\-–—]|\s|$)/i)
  return match ? match[1] : null
}

function isGeneratedTaskPlaceholder(task) {
  const id = asId(task?.id)
  return Boolean(id) && String(task?.subject || '').trim() === `Task #${id}`
}

function mergeDisplayTask(primary, update) {
  const primaryDone = isSdkTaskDone(primary)
  const updateDone = isSdkTaskDone(update)
  const status = updateDone || !primaryDone && normalizeStatus(update?.status) !== 'pending'
    ? update.status
    : primary.status
  return {
    ...primary,
    status,
    blocks: mergeUnique(primary?.blocks, update?.blocks),
    blockedBy: mergeUnique(primary?.blockedBy, update?.blockedBy),
  }
}

// A TaskUpdate can be replayed before the matching TaskCreate result exposes
// its canonical id. In that ordering the tracker temporarily creates a
// generated "Task #N" placeholder beside the richer "Task N: subject" row.
// Coalesce that placeholder into the richer row for Composer presentation so
// status changes survive without double-counting the same logical task.
function coalesceComposerTaskPlaceholders(tasks) {
  const result = [...tasks]
  for (let index = result.length - 1; index >= 0; index -= 1) {
    const placeholder = result[index]
    if (!isGeneratedTaskPlaceholder(placeholder)) continue
    const ordinal = asId(placeholder.id)
    const primaryIndex = result.findIndex((task, candidateIndex) => (
      candidateIndex !== index
      && !isGeneratedTaskPlaceholder(task)
      && taskSubjectOrdinal(task) === ordinal
    ))
    if (primaryIndex < 0) continue
    result[primaryIndex] = mergeDisplayTask(result[primaryIndex], placeholder)
    result.splice(index, 1)
  }
  return result
}

export function getSdkTaskComposerTasks(tracker) {
  if (!tracker) return []
  const byTaskId = new Map()
  for (const roundId of getSdkTaskComposerRoundIds(tracker)) {
    const round = tracker.rounds[roundId]
    for (const snapshot of getSdkTaskRoundTasks(round)) {
      const key = snapshot.provisional ? snapshot._key : snapshot.id
      const task = snapshot.provisional
        ? snapshot
        : (tracker.canonicalTasks[snapshot.id] || snapshot)
      if (byTaskId.has(key)) byTaskId.delete(key)
      byTaskId.set(key, task)
    }
  }
  return coalesceComposerTaskPlaceholders([...byTaskId.values()])
}

export function getSdkTaskComposerRoundIds(tracker) {
  if (!tracker) return []
  return tracker.roundOrder.filter((roundId) => {
    const round = tracker.rounds[roundId]
    if (!round?.hasTaskEvents) return false
    if (roundId === tracker.currentRoundId) return true
    return roundHasOpenCanonicalTask(tracker, roundId)
  })
}

export function getSdkTaskProgress(tasks) {
  const list = Array.isArray(tasks) ? tasks : []
  return {
    done: list.filter(isSdkTaskDone).length,
    total: list.length,
  }
}

export function hasSdkTaskTrackerRounds(tracker) {
  return Boolean(tracker?.roundOrder?.some((id) => tracker.rounds[id]?.hasTaskEvents))
}
