import { sandboxGet, sandboxRead, sandboxPost, sandboxPut, sandboxDelete, getAuthHeaders } from '@shared/api/client'

const BASE_URL = '/api/sandbox'

// Subagents are keyed by (scope, cwd, name). scope 'user' or 'project'; cwd only
// meaningful for project scope. These ride as query params on the item endpoints.
const scopeQuery = (scope, cwd) => {
  const params = new URLSearchParams()
  if (scope) params.set('scope', scope)
  if (cwd) params.set('cwd', cwd)
  const s = params.toString()
  return s ? `?${s}` : ''
}

export const fetchAgents = () => sandboxGet('/subagents/list')

// sandboxRead: detail carries the agent's full prompt body — can exceed the ~8KB EPP cap.
export const fetchAgent = (scope, cwd, name) =>
  sandboxRead(`/subagents/${encodeURIComponent(name)}${scopeQuery(scope, cwd)}`)

export const fetchCatalog = () => sandboxGet('/subagents/catalog')

export const createAgent = (body) => sandboxPost('/subagents/', body)

export const updateAgent = (scope, cwd, name, body) =>
  sandboxPut(`/subagents/${encodeURIComponent(name)}${scopeQuery(scope, cwd)}`, body)

export const deleteAgent = (scope, cwd, name) =>
  sandboxDelete(`/subagents/${encodeURIComponent(name)}${scopeQuery(scope, cwd)}`)

/**
 * Stream a one-shot test run against the named agent. Returns { abort } to cancel.
 */
export function streamAgentTest(scope, cwd, name, prompt, onEvent, onComplete) {
  const controller = new AbortController()

  const run = async () => {
    const res = await fetch(
      `${BASE_URL}/subagents/${encodeURIComponent(name)}/test/stream${scopeQuery(scope, cwd)}`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        body: JSON.stringify({ prompt }),
        signal: controller.signal,
      }
    )

    if (!res.ok) {
      if (res.status === 401) {
        window.dispatchEvent(new Event('auth:unauthorized'))
      }
      const text = await res.text()
      throw new Error(`SSE error ${res.status}: ${text}`)
    }

    const reader = res.body.getReader()
    const decoder = new TextDecoder()
    let buffer = ''
    let currentEvent = null

    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buffer += decoder.decode(value, { stream: true })

      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ') && currentEvent) {
          try {
            const data = JSON.parse(line.slice(6))
            onEvent(currentEvent, data)
          } catch {
            /* skip malformed JSON */
          }
          currentEvent = null
        } else if (line === '') {
          currentEvent = null
        }
      }
    }
  }

  run()
    .then(() => onComplete?.())
    .catch((err) => {
      if (err.name !== 'AbortError') {
        onEvent('error', { message: err.message })
      }
      onComplete?.()
    })

  return { abort: () => controller.abort() }
}
