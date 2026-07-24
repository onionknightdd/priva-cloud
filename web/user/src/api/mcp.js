import { sandboxRead, sandboxReadPost, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// Project servers are keyed by (cwd, name); global servers ignore cwd. The cwd
// rides as a query param on the item endpoints.
const scopeQuery = (cwd) => (cwd ? `?cwd=${encodeURIComponent(cwd)}` : '')

// sandboxRead: the aggregate list (full url + absolute cwd per entry, across every
// project workdir + global) grows unbounded past the ~8KB EPP cap as servers accumulate.
export const listMcpServers = () => sandboxRead('/resource/mcp/')

// sandboxRead: a single detail ships the full headers list — a long header value
// (big bearer token / base64) or many headers can cross the ~8KB EPP cap.
export const getMcpServerDetail = (level, name, cwd) =>
  sandboxRead(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`)

// sandboxRead: capabilities ship every tool's input_schema — routinely past the ~8KB EPP cap.
export const getMcpServerCapabilities = (level, name, cwd) =>
  sandboxRead(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}/capabilities${scopeQuery(cwd)}`)

export const createMcpServer = (data) => sandboxPost('/resource/mcp/', data)

export const updateMcpServer = (level, name, cwd, data) =>
  sandboxPut(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`, data)

export const deleteMcpServer = (level, name, cwd) =>
  sandboxDelete(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}${scopeQuery(cwd)}`)

// sandboxReadPost: validate replies with every discovered tool's input_schema — the
// same >8KB payload that put capabilities on the safe lane. POST, but the reply is large.
export const validateMcpServer = (data) => sandboxReadPost('/resource/mcp/validate', data)

// sandboxReadPost: a tool test reply carries the tool's full output (docs/search results),
// which can far exceed the ~8KB EPP cap.
export const validateMcpTool = (data) => sandboxReadPost('/resource/mcp/validate/tool', data)
