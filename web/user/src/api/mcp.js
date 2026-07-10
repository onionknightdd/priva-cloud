import { sandboxGet, sandboxRead, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// Project servers are keyed by (cwd, name); global servers ignore cwd. The cwd
// rides as a query param on the item endpoints.
const scopeQuery = (cwd) => (cwd ? `?cwd=${encodeURIComponent(cwd)}` : '')

export const listMcpServers = () => sandboxGet('/resource/mcp/')

export const getMcpServerDetail = (level, name, cwd) =>
  sandboxGet(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`)

// sandboxRead: capabilities ship every tool's input_schema — routinely past the ~8KB EPP cap.
export const getMcpServerCapabilities = (level, name, cwd) =>
  sandboxRead(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}/capabilities${scopeQuery(cwd)}`)

export const createMcpServer = (data) => sandboxPost('/resource/mcp/', data)

export const updateMcpServer = (level, name, cwd, data) =>
  sandboxPut(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`, data)

export const deleteMcpServer = (level, name, cwd) =>
  sandboxDelete(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`)

export const validateMcpServer = (data) => sandboxPost('/resource/mcp/validate', data)

export const validateMcpTool = (data) => sandboxPost('/resource/mcp/validate/tool', data)
