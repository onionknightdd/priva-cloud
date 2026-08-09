import { countContentLines, countPatchLines } from './toolRunSummary.js'

const CHANGE_TYPES = new Set(['write', 'edit'])
const AGENT_TYPES = new Set(['Agent', 'Task'])

export function uniqueCanvasFiles(tabs = []) {
  const seen = new Set()
  return (tabs || []).filter((tab) => {
    const sourceTool = String(tab?.sourceTool || tab?.source_tool || '').toLowerCase()
    if (sourceTool === 'write' || sourceTool === 'edit') return false
    const filePath = typeof tab?.filePath === 'string' ? tab.filePath.trim() : ''
    if (!filePath || seen.has(filePath)) return false
    seen.add(filePath)
    return true
  })
}

export function uniqueConversationSources(messages = []) {
  const sources = []
  const indexByIdentity = new Map()
  const indexByLabel = new Map()

  const addSource = ({ label, path = '', kind = 'file', key = '', src = '' }) => {
    const normalizedLabel = typeof label === 'string' ? label.trim() : ''
    const normalizedPath = typeof path === 'string' ? path.trim() : ''
    if (!normalizedLabel && !normalizedPath) return
    const resolvedLabel = normalizedLabel || normalizedPath.split('/').filter(Boolean).pop() || normalizedPath
    const identity = normalizedPath || key || `${kind}:${resolvedLabel}`
    const labelIdentity = `${kind}:${resolvedLabel}`
    const existingIndex = indexByIdentity.get(identity) ?? indexByLabel.get(labelIdentity)
    if (existingIndex != null) {
      if (src && !sources[existingIndex].src) {
        sources[existingIndex] = { ...sources[existingIndex], src }
      }
      return
    }
    const index = sources.length
    indexByIdentity.set(identity, index)
    indexByLabel.set(labelIdentity, index)
    sources.push({ key: identity, label: resolvedLabel, path: normalizedPath, kind, src })
  }

  for (const message of messages || []) {
    if (message?.role !== 'user') continue

    for (const attachment of message.attachments || []) {
      addSource({
        label: attachment?.originalName || attachment?.name,
        path: attachment?.path,
        kind: attachment?.isImage ? 'image' : 'file',
      })
    }

    const blocks = Array.isArray(message.content) ? message.content : []
    for (let index = 0; index < blocks.length; index += 1) {
      const block = blocks[index]
      if (block?.type !== 'image') continue
      const mediaType = block.source?.media_type || block.source?.mediaType || 'image/png'
      const data = block.source?.data
      addSource({
        label: block.filename || `Image ${sources.length + 1}`,
        kind: 'image',
        key: `${message.uuid || message._cid || 'message'}:image:${index}`,
        src: data ? `data:${mediaType};base64,${data}` : (block.src || block.url || ''),
      })
    }
  }

  return sources
}

export function uniqueConversationAgents(messages = [], subagentContent = {}) {
  const agents = []
  const seen = new Set()

  const visit = (blocks, parentKey) => {
    if (!Array.isArray(blocks)) return

    blocks.forEach((block, index) => {
      if (block?.type !== 'tool_use' || !AGENT_TYPES.has(block.name)) return
      const key = block.id || `${parentKey}:${block.name}:${index}`
      if (seen.has(key)) return
      seen.add(key)
      agents.push(block)

      if (block.id) visit(subagentContent[block.id], key)
    })
  }

  for (let index = 0; index < (messages || []).length; index += 1) {
    visit(messages[index]?.content, `message:${messages[index]?.uuid || messages[index]?._cid || index}`)
  }

  return agents
}

export function isCanvasChangeOp(op) {
  return CHANGE_TYPES.has(String(op?.type || '').toLowerCase())
}

export function fileOpLineStats(op) {
  if (!isCanvasChangeOp(op)) return { added: 0, removed: 0 }

  const patchStats = countPatchLines(op?.structuredPatch)
  if (patchStats.added > 0 || patchStats.removed > 0) return patchStats

  const input = op?.input || {}
  if (String(op.type).toLowerCase() === 'write') {
    return {
      added: countContentLines(op?.content ?? input.content),
      removed: 0,
    }
  }

  return {
    added: countContentLines(input.new_string),
    removed: countContentLines(input.old_string),
  }
}

export function summarizeCanvasChanges(fileOps = [], revertedToolUseIds = []) {
  const operations = (fileOps || []).filter(isCanvasChangeOp)
  const revertedIds = new Set(revertedToolUseIds || [])
  const filePaths = new Set()
  let added = 0
  let removed = 0

  for (const op of operations) {
    const filePath = typeof op?.filePath === 'string' ? op.filePath.trim() : ''
    if (filePath) filePaths.add(filePath)

    if (op?.status !== 'success' || revertedIds.has(op?.id)) continue
    const stats = fileOpLineStats(op)
    added += stats.added
    removed += stats.removed
  }

  return {
    operations,
    fileCount: filePaths.size,
    added,
    removed,
  }
}
