import { sandboxGet, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// Slash-commands (custom commands) — list + CRUD across User/Project scopes.
export const fetchCommands = () => sandboxGet('/commands/list')

const scoped = (name, scope, cwd) => {
  const params = new URLSearchParams({ scope })
  if (cwd) params.set('cwd', cwd)
  return `/commands/${encodeURIComponent(name)}?${params}`
}

export const fetchCommand = (scope, cwd, name) => sandboxGet(scoped(name, scope, cwd))
export const createCommand = (body) => sandboxPost('/commands/', body)
export const updateCommand = (scope, cwd, name, body) => sandboxPut(scoped(name, scope, cwd), body)
export const deleteCommand = (scope, cwd, name) => sandboxDelete(scoped(name, scope, cwd))
