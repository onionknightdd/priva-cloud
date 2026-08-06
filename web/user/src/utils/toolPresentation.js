import { getToolDisplayName } from './generatedTool.js'

const RUNNING_STATUSES = new Set(['running', 'pending', 'queued', 'in_progress'])
const ERROR_STATUSES = new Set(['error', 'failed'])
const STOPPED_STATUSES = new Set(['cancelled', 'canceled', 'stopped', 'skipped', 'aborted', 'killed'])

function numberOrNull(value) {
  return typeof value === 'number' && Number.isFinite(value) ? value : null
}

export function findFileOp(fileOps, block) {
  if (!Array.isArray(fileOps) || !block) return null
  return fileOps.find((op) => (
    op.id === block.fileOpId
    || op.id === block.id
    || (block.fileOpId && op.toolUseId === block.fileOpId)
    || (block.id && op.toolUseId === block.id)
  )) || null
}

export function makeToolOp(block, op = null) {
  if (op) return op
  const result = block?.result || null
  const toolUseResult = result?.tool_use_result || result?.toolUseResult || null
  return {
    id: block?.fileOpId || block?.id || null,
    type: block?.name?.toLowerCase?.() || '',
    filePath: block?.filePath || block?.input?.file_path || '',
    relativePath: block?.relativePath || block?.relative_path || null,
    status: block?.status,
    startTime: block?.startTime,
    endTime: block?.endTime,
    duration: block?.duration,
    input: block?.input,
    content: block?.input?.content || toolUseResult?.content || toolUseResult?.new_content || null,
    originalFile: toolUseResult?.original_file || toolUseResult?.originalFile || null,
    structuredPatch: toolUseResult?.structured_patch || toolUseResult?.structuredPatch || null,
    resultContent: typeof result?.content === 'string' ? result.content : null,
    toolUseResult,
  }
}

export function getToolStatus(block, op = null) {
  const raw = String(op?.status || block?.status || (block?.type === 'file_ref' ? 'success' : 'running')).toLowerCase()
  if (raw === 'completed' || raw === 'complete' || raw === 'done') return 'success'
  if (ERROR_STATUSES.has(raw)) return 'error'
  if (RUNNING_STATUSES.has(raw)) return 'running'
  return raw
}

export function isToolError(block, op = null) {
  return getToolStatus(block, op) === 'error'
    || Boolean(block?.result?.is_error)
    || Boolean(op?.result?.is_error)
}

export function isToolRunning(block, op = null) {
  return getToolStatus(block, op) === 'running'
}

export function getStoppedStatus(block, op = null) {
  const raw = String(op?.status || block?.status || '').toLowerCase()
  if (!STOPPED_STATUSES.has(raw)) return null
  if (raw === 'canceled') return 'cancelled'
  return raw
}

export function formatDuration(ms) {
  const value = numberOrNull(ms)
  if (value == null || value < 0) return null
  const seconds = Math.round(value / 100) / 10
  if (seconds < 60) return `${seconds} s`

  const wholeSeconds = Math.round(value / 1000)
  const hours = Math.floor(wholeSeconds / 3600)
  const minutes = Math.floor((wholeSeconds % 3600) / 60)
  const remainingSeconds = wholeSeconds % 60
  const parts = []

  if (hours > 0) parts.push(`${hours} h`)
  if (minutes > 0) parts.push(`${minutes} m`)
  if (remainingSeconds > 0 || parts.length === 0) parts.push(`${remainingSeconds} s`)
  return parts.join(' ')
}

export function formatToolValue(value) {
  if (value == null) return ''
  if (typeof value === 'string') return value
  if (Array.isArray(value) && value.length === 0) return ''
  if (typeof value === 'object' && Object.keys(value).length === 0) return ''
  try {
    return JSON.stringify(value, null, 2)
  } catch {
    return String(value)
  }
}

export function formatToolOutput(result) {
  if (!result) return ''
  if (result.content != null) {
    if (typeof result.content === 'string') return result.content
    if (Array.isArray(result.content)) {
      return result.content
        .map((item) => {
          if (typeof item === 'string') return item
          if (typeof item?.text === 'string') return item.text
          return formatToolValue(item)
        })
        .filter(Boolean)
        .join('\n')
    }
    return formatToolValue(result.content)
  }
  return formatToolValue(result)
}

function trimTrailingSlash(path) {
  return String(path || '').replace(/[\\/]+$/, '')
}

export function toProjectRelativePath(filePath, cwd = '', explicitRelativePath = '') {
  const explicit = String(explicitRelativePath || '').trim()
  if (explicit) return explicit.replace(/^\.\//, '')

  const fullPath = String(filePath || '').trim()
  if (!fullPath) return ''
  if (!fullPath.startsWith('/') && !/^[A-Za-z]:[\\/]/.test(fullPath)) {
    return fullPath.replace(/^\.\//, '')
  }

  const root = trimTrailingSlash(cwd)
  if (!root) return fullPath
  const normalizedPath = fullPath.replace(/\\/g, '/')
  const normalizedRoot = root.replace(/\\/g, '/')
  if (normalizedPath === normalizedRoot) return '.'
  if (normalizedPath.startsWith(`${normalizedRoot}/`)) {
    return normalizedPath.slice(normalizedRoot.length + 1)
  }
  return fullPath
}

export function getMcpIdentity(name) {
  const rawName = String(name || '')
  if (!rawName.startsWith('mcp__')) return null
  const parts = rawName.split('__')
  if (parts.length < 3) return null
  return {
    server: parts.slice(1, -1).join('__'),
    method: parts[parts.length - 1],
    rawName,
  }
}

function firstUsefulInput(input) {
  if (!input || typeof input !== 'object') return null
  const priority = ['description', 'query', 'url', 'path', 'file_path', 'pattern', 'prompt', 'command']
  for (const key of priority) {
    const value = input[key]
    if (typeof value === 'string' && value.trim()) return { key, value: value.trim() }
  }
  for (const [key, value] of Object.entries(input)) {
    if (typeof value === 'string' && value.trim()) return { key, value: value.trim() }
    if (typeof value === 'number' || typeof value === 'boolean') return { key, value: String(value) }
  }
  return null
}

function countLines(value) {
  if (typeof value !== 'string' || !value.length) return 0
  const normalized = value.endsWith('\n') ? value.slice(0, -1) : value
  return normalized ? normalized.split('\n').length : 0
}

export function buildDiffRows(block, op = null) {
  const opLike = makeToolOp(block, op)
  const name = block?.name || String(opLike?.type || '').replace(/^./, (char) => char.toUpperCase())
  const patch = opLike?.structuredPatch
  const rows = []

  if (patch && Array.isArray(patch.hunks)) {
    for (const hunk of patch.hunks) {
      let oldLine = numberOrNull(hunk?.oldStart) || 1
      let newLine = numberOrNull(hunk?.newStart) || 1
      for (const rawLine of hunk?.lines || []) {
        if (typeof rawLine !== 'string') continue
        if (rawLine.startsWith('+++') || rawLine.startsWith('---')) continue
        if (rawLine.startsWith('+')) {
          rows.push({ kind: 'add', oldNum: null, newNum: newLine, text: rawLine })
          newLine += 1
        } else if (rawLine.startsWith('-')) {
          rows.push({ kind: 'remove', oldNum: oldLine, newNum: null, text: rawLine })
          oldLine += 1
        } else {
          rows.push({ kind: 'context', oldNum: oldLine, newNum: newLine, text: rawLine || ' ' })
          oldLine += 1
          newLine += 1
        }
      }
    }
    if (rows.length) return rows
  }

  const input = opLike?.input || block?.input || {}
  if (name === 'Edit' || String(opLike?.type).toLowerCase() === 'edit') {
    if (input.old_string == null && input.new_string == null) return []
    const oldString = String(input.old_string || '')
    const newString = String(input.new_string || '')
    const originalFile = opLike?.originalFile
      || opLike?.toolUseResult?.original_file
      || opLike?.toolUseResult?.originalFile
      || opLike?.resultContent
    let startLine = 1
    if (oldString && typeof originalFile === 'string') {
      const index = originalFile.indexOf(oldString)
      if (index >= 0) startLine = originalFile.slice(0, index).split('\n').length
    }
    oldString.split('\n').forEach((line, index) => {
      rows.push({ kind: 'remove', oldNum: startLine + index, newNum: null, text: `-${line}` })
    })
    newString.split('\n').forEach((line, index) => {
      rows.push({ kind: 'add', oldNum: null, newNum: startLine + index, text: `+${line}` })
    })
    return rows
  }

  if (name === 'Write' || String(opLike?.type).toLowerCase() === 'write') {
    const content = opLike?.content || input.content
    if (typeof content !== 'string' || !content.length) return []
    return content.split('\n').map((line, index) => ({
      kind: 'add',
      oldNum: null,
      newNum: index + 1,
      text: `+${line}`,
    }))
  }

  return []
}

export function getDiffStats(rows) {
  return (rows || []).reduce((stats, row) => {
    if (row.kind === 'add') stats.added += 1
    if (row.kind === 'remove') stats.removed += 1
    return stats
  }, { added: 0, removed: 0 })
}

export function getToolPresentation(block, { op = null, cwd = '', kind = null } = {}) {
  const opLike = makeToolOp(block, op)
  const rawName = String(kind || block?.name || opLike?.type || 'Tool')
  const normalizedName = rawName.charAt(0).toUpperCase() + rawName.slice(1)
  const displayName = getToolDisplayName(normalizedName)
  const mcp = getMcpIdentity(block?.name || rawName)
  const input = block?.input || opLike?.input || {}
  const fullPath = opLike?.filePath || block?.filePath || input.file_path || ''
  const relativePath = toProjectRelativePath(
    fullPath,
    cwd,
    opLike?.relativePath || block?.relativePath || block?.relative_path,
  )
  let name = mcp?.server || displayName
  let summary = mcp?.method || ''
  let summaryIsCode = Boolean(mcp)
  let copyValue = ''

  if (['Read', 'Write', 'Edit'].includes(displayName)) {
    summary = relativePath || fullPath || '(untitled)'
    summaryIsCode = true
    copyValue = fullPath || summary
  } else if (displayName === 'Bash') {
    summary = input.description || input.command || ''
    summaryIsCode = !input.description
    copyValue = input.command || ''
  } else if (displayName === 'Glob') {
    summary = [input.pattern, input.path && toProjectRelativePath(input.path, cwd)].filter(Boolean).join(' · ') || '(cwd)'
    summaryIsCode = true
    copyValue = input.pattern || input.path || ''
  } else if (displayName === 'Grep') {
    const pattern = input.pattern ? `“${input.pattern}”` : ''
    summary = [pattern, input.path && toProjectRelativePath(input.path, cwd), input.glob, input.type && `type:${input.type}`]
      .filter(Boolean)
      .join(' · ') || '(cwd)'
    summaryIsCode = true
    copyValue = input.pattern || input.path || ''
  } else if (displayName === 'WebFetch') {
    summary = input.url || input.prompt || ''
    summaryIsCode = Boolean(input.url)
    copyValue = input.url || ''
  } else if (displayName === 'WebSearch') {
    summary = input.query || ''
    copyValue = input.query || ''
  } else if (displayName === 'Skill') {
    summary = input.skill || input.name || input.skill_name || ''
    summaryIsCode = true
  } else if (displayName === 'Monitor') {
    summary = input.description || input.command || ''
    summaryIsCode = !input.description
    copyValue = input.command || ''
  } else {
    const useful = firstUsefulInput(input)
    if (useful) {
      const detail = useful.value
      const prefix = mcp || ['description', 'query', 'url', 'path', 'file_path', 'pattern', 'prompt', 'command'].includes(useful.key)
        ? ''
        : `${useful.key}: `
      const semantic = `${prefix}${detail}`
      summary = summary ? `${summary} · ${semantic}` : semantic
      summaryIsCode = mcp || useful.key !== 'description'
      copyValue = detail
    }
  }

  const diffRows = ['Write', 'Edit'].includes(displayName) ? buildDiffRows(block, opLike) : []
  const diffStats = getDiffStats(diffRows)
  const inputText = formatToolValue(input)
  const outputText = formatToolOutput(block?.result)
    || formatToolValue(opLike?.resultContent)
    || formatToolValue(opLike?.toolUseResult?.error || opLike?.toolUseResult?.message)
  const status = getToolStatus(block, opLike)
  const isError = isToolError(block, opLike)
  const duration = numberOrNull(block?.duration)
    ?? numberOrNull(opLike?.duration)
    ?? (
      numberOrNull(opLike?.endTime) != null && numberOrNull(opLike?.startTime) != null
        ? opLike.endTime - opLike.startTime
        : null
    )

  return {
    name,
    rawName: block?.name || rawName,
    displayName,
    summary,
    summaryIsCode,
    copyValue,
    fullPath,
    relativePath,
    inputText,
    outputText,
    diffRows,
    diffStats,
    status,
    duration,
    startTime: numberOrNull(block?.startTime) ?? numberOrNull(opLike?.startTime),
    isRunning: !isError && isToolRunning(block, opLike),
    isError,
    stoppedStatus: getStoppedStatus(block, opLike),
    opLike,
  }
}

export function getRunMetrics(run, fileOps, now = Date.now(), live = false) {
  const items = (run || []).map((block) => {
    const op = findFileOp(fileOps, block)
    const startTime = numberOrNull(block?.startTime) ?? numberOrNull(op?.startTime)
    const duration = numberOrNull(block?.duration)
      ?? numberOrNull(op?.duration)
      ?? (
        numberOrNull(op?.endTime) != null && startTime != null
          ? op.endTime - startTime
          : null
      )
    const endTime = numberOrNull(block?.endTime)
      ?? numberOrNull(op?.endTime)
      ?? (startTime != null && duration != null ? startTime + duration : null)
    const error = isToolError(block, op)
    return {
      startTime,
      duration,
      endTime,
      error,
      running: !error && isToolRunning(block, op),
    }
  })
  const starts = items
    .map((item) => item.startTime)
    .filter((value) => value != null)
  const startTime = starts.length ? Math.min(...starts) : null
  const hasRunning = items.some((item) => item.running)
  const failed = items.filter((item) => item.error).length
  const durations = items
    .map((item) => item.duration)
    .filter((value) => value != null)
  const ends = items
    .map((item) => item.endTime)
    .filter((value) => value != null)
  const duration = startTime != null && (hasRunning || live)
    ? Math.max(0, now - startTime)
    : (
      startTime != null && ends.length
        ? Math.max(0, Math.max(...ends) - startTime)
        : (durations.length ? durations.reduce((sum, value) => sum + value, 0) : null)
    )

  return {
    count: items.length,
    failed,
    hasRunning,
    duration,
  }
}

export { countLines }
