export const GENERATED_TOOL_NAME = 'mcp__FileCanvas__register_file'
export const GENERATED_TOOL_METHOD = 'register_file'
export const GENERATED_TOOL_LABEL = 'FileCanvas'

export function isGeneratedToolName(name) {
  return (
    name === GENERATED_TOOL_NAME ||
    name === GENERATED_TOOL_METHOD
  )
}

export function getToolDisplayName(name) {
  if (isGeneratedToolName(name)) return GENERATED_TOOL_LABEL
  if (name === 'TodoWrite') return 'TODO'
  return name
}

export function getGeneratedInputPaths(input) {
  const rawPaths = Array.isArray(input?.paths) ? input.paths : []
  const seen = new Set()
  const normalized = []

  for (const value of rawPaths) {
    if (typeof value !== 'string') continue
    const path = value.trim()
    if (!path || seen.has(path)) continue
    seen.add(path)
    normalized.push(path)
  }

  return normalized
}

export function buildGeneratedFileOpId(toolUseId, index) {
  return `${toolUseId}::generated::${index}`
}
