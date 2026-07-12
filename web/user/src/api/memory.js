import { sandboxGet, sandboxRead, sandboxPut, sandboxDelete } from '@shared/api/client'

// CLAUDE.md scopes (User + each project workdir), with existence + size.
export const fetchMemoryList = () => sandboxGet('/memory/list')

// Read one scope's CLAUDE.md. Content can be large -> sandboxRead lane.
export const fetchMemoryContent = (scope = 'user', cwd = null) => {
  const params = new URLSearchParams({ scope })
  if (cwd) params.set('cwd', cwd)
  return sandboxRead(`/memory/content?${params}`)
}

// Write one scope's CLAUDE.md.
export const updateMemoryContent = (content, scope = 'user', cwd = null) => {
  const params = new URLSearchParams({ scope })
  if (cwd) params.set('cwd', cwd)
  return sandboxPut(`/memory/content?${params}`, { content })
}

// --- Auto memory (Claude-written) ---
// The memory files Claude Code keeps for itself per project. Browse / edit /
// delete existing files + a per-project on/off toggle. No create (Claude authors).

export const fetchAutoMemoryList = () => sandboxGet('/memory/auto/list')

export const fetchAutoMemoryFile = (cwd, name) => {
  const params = new URLSearchParams({ cwd, name })
  return sandboxRead(`/memory/auto/content?${params}`)
}

export const updateAutoMemoryFile = (cwd, name, content) => {
  const params = new URLSearchParams({ cwd, name })
  return sandboxPut(`/memory/auto/content?${params}`, { content })
}

export const deleteAutoMemoryFile = (cwd, name) => {
  const params = new URLSearchParams({ cwd, name })
  return sandboxDelete(`/memory/auto/content?${params}`)
}

export const setAutoMemoryEnabled = (cwd, enabled) => {
  const params = new URLSearchParams({ cwd })
  return sandboxPut(`/memory/auto/enabled?${params}`, { enabled })
}
