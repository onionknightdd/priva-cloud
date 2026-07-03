import { sandboxGet, sandboxRead, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

export const listMcpServers = () => sandboxGet('/resource/mcp/')

export const getMcpServerDetail = (level, name) =>
  sandboxGet(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}`)

// sandboxRead: capabilities ship every tool's input_schema — routinely past the ~8KB EPP cap.
export const getMcpServerCapabilities = (level, name) =>
  sandboxRead(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}/capabilities`)

export const createMcpServer = (data) => sandboxPost('/resource/mcp/', data)

export const updateMcpServer = (level, name, data) =>
  sandboxPut(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}`, data)

export const deleteMcpServer = (level, name) =>
  sandboxDelete(`/resource/mcp/${encodeURIComponent(level)}/${encodeURIComponent(name)}`)

export const validateMcpServer = (data) => sandboxPost('/resource/mcp/validate', data)

export const validateMcpTool = (data) => sandboxPost('/resource/mcp/validate/tool', data)
