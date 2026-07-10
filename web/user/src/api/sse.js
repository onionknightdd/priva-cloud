import { getAuthHeaders } from '@shared/api/client'
import useConnectionStore from '../stores/connectionStore'
import { getToken } from '@shared/api/tokenStore'
import { wsProtocols } from '@shared/api/wsAuth'
import { debugLog } from '@shared/utils/debugLog'
import i18n from '@shared/i18n'

const BASE_URL = '/api/sandbox'

const RECONNECT_BACKOFF = [1, 2, 4, 8, 16] // seconds — max 5 attempts
const PROTOCOL_AUTH_CLOSE_CODES = new Set([1000, 1001, 4000, 4001])

// Shared WS engine for both entry modes:
//   init   — start a new agent run (sends the init frame with the message)
//   attach — join a run already executing on the backend (replay + follow)
//
// Reconnects prefer an `attach` frame with the last seen event seq (lossless
// resume against the run registry); when the backend predates the registry
// (no seq on events), init-mode falls back to the legacy re-init.
//
// `surfaceConnUi` gates the global connection banner: only the stream whose
// session is on screen paints reconnecting/disconnected state — background
// sessions reconnect silently.
function openAgentWS({ mode, message, sessionId, runId, sinceSeq = 0, onEvent, permissionMode, onComplete, model, attachments, mcpServers, images, trace, enableFileCheckpointing = false, cwd = null, addDirs = null, surfaceConnUi = null }) {
  let ws = null
  let userAborted = false
  let completed = false
  let reconnectAttempt = 0
  let reconnectTimer = null
  let activeSessionId = sessionId
  let activeRunId = runId || null
  let lastSeq = mode === 'attach' ? (sinceSeq || 0) : null // null = legacy backend (no seq seen)

  const connUi = () => (surfaceConnUi ? surfaceConnUi() : true)
  const marks = {
    connected: () => { if (connUi()) useConnectionStore.getState().markConnected() },
    reconnecting: (info) => { if (connUi()) useConnectionStore.getState().markReconnecting(info) },
    disconnected: (info) => { if (connUi()) useConnectionStore.getState().markDisconnected(info) },
  }

  // Single outgoing-frame choke point so debug logging (Settings → Advanced →
  // Developer Mode) covers every WS send: init, attach, queue, queue_cancel,
  // permission_response, abort.
  const wsSend = (frame) => {
    debugLog('send', `WS ▶ ${frame.type}`, frame)
    ws.send(JSON.stringify(frame))
  }

  const sendInit = () => {
    const token = getToken()
    const init = { type: 'init', message }
    if (trace?.tabId) init.client_tab_id = trace.tabId
    if (token) init.token = token
    if (activeSessionId) init.session_id = activeSessionId
    if (permissionMode) init.permission_mode = permissionMode
    if (model) init.model = model
    if (attachments && attachments.length > 0) init.attachments = attachments
    if (images && images.length > 0) init.images = images
    if (mcpServers !== undefined) init.mcp_servers = mcpServers
    if (enableFileCheckpointing) init.enable_file_checkpointing = true
    // cwd: honored for NEW sessions only (locked on resume — backend ignores it).
    // add_dirs: the run's --add-dir set; omit to recover the session's stored set.
    if (cwd) init.cwd = cwd
    if (addDirs && addDirs.length > 0) init.add_dirs = addDirs
    // WebUI can resolve prompts (permission card / AskUserQuestion), so opt in
    // to synchronous feedback. The API default is false (non-interactive safe).
    init.enable_permission_feedback = true
    wsSend(init)
  }

  const sendAttach = () => {
    const token = getToken()
    const frame = { type: 'attach', since_seq: lastSeq || 0 }
    if (token) frame.token = token
    if (activeSessionId) frame.session_id = activeSessionId
    if (activeRunId) frame.run_id = activeRunId
    if (trace?.tabId) frame.client_tab_id = trace.tabId
    wsSend(frame)
  }

  const finalize = () => {
    if (completed) return
    completed = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (onComplete) onComplete()
  }

  const scheduleReconnect = (closeCode) => {
    if (reconnectAttempt >= RECONNECT_BACKOFF.length) {
      marks.disconnected({ code: closeCode })
      onEvent('error', { message: 'Connection lost — please refresh the page.' })
      finalize()
      return
    }
    reconnectAttempt += 1
    const delay = RECONNECT_BACKOFF[reconnectAttempt - 1]
    marks.reconnecting({
      attempt: reconnectAttempt,
      maxAttempts: RECONNECT_BACKOFF.length,
      delaySeconds: delay,
      code: closeCode,
    })
    reconnectTimer = setTimeout(connect, delay * 1000)
  }

  const connect = () => {
    reconnectTimer = null
    const isReconnect = reconnectAttempt > 0
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
    // The edge (agentgateway ext_proc EPP) authenticates the WS on the UPGRADE
    // request, which carries no body — so the token rides the
    // `Sec-WebSocket-Protocol` handshake header (see wsAuth.js), not the URL.
    const wsUrl = `${protocol}//${window.location.host}/api/sandbox/agent/ws/run`
    ws = new WebSocket(wsUrl, wsProtocols())

    ws.onopen = () => {
      // Mark connected on every successful (re)open. The first open transitions
      // out of `disconnected`/`reconnecting` cleanly.
      marks.connected()
      reconnectAttempt = 0
      if (mode === 'attach') {
        sendAttach()
      } else if (isReconnect && lastSeq !== null) {
        // Registry backend: rejoin the SAME run losslessly instead of
        // re-initing (which would double-resume the session).
        sendAttach()
      } else {
        sendInit()
      }
    }

    ws.onmessage = (evt) => {
      try {
        const { event, data, seq } = JSON.parse(evt.data)
        if (typeof seq === 'number' && seq > (lastSeq || 0)) lastSeq = seq
        if (event === 'keepalive') return
        debugLog('recv', `WS ◀ ${event}`, data)
        // Track run/session identity so a reconnect can re-attach to the run.
        if (event === 'result' && data?.session_id) activeSessionId = data.session_id
        if (event === 'system' && data?.subtype === 'init' && data?.data?.session_id) {
          activeSessionId = data.data.session_id
        }
        if (event === 'stream_init' && data?.stream_id) activeRunId = data.stream_id
        if (event === 'attach_ok') {
          if (data?.session_id) activeSessionId = data.session_id
          if (data?.run_id) activeRunId = data.run_id
        }
        onEvent(event, data)
      } catch {
        // skip malformed JSON
      }
    }

    ws.onclose = (evt) => {
      if (userAborted || completed) {
        finalize()
        return
      }
      if (evt.code === 4001) {
        window.dispatchEvent(new Event('auth:unauthorized'))
        finalize()
        return
      }
      // Clean / protocol / auth closes — terminate.
      if (PROTOCOL_AUTH_CLOSE_CODES.has(evt.code)) {
        marks.connected()
        finalize()
        return
      }
      // Server-error close (4500): surface as fatal and don't reconnect.
      if (evt.code === 4500) {
        marks.disconnected({ code: evt.code })
        onEvent('stream_error', {
          code: 'ServerError',
          message: 'Server error — please try again.',
          fatal: true,
        })
        finalize()
        return
      }
      // 1009 (message too big): reconnecting would just resend the same
      // oversized payload — fail fast with a clear, actionable error.
      if (evt.code === 1009) {
        onEvent('stream_error', {
          code: 'MessageTooLarge',
          message: i18n.t('connection.messageTooLarge'),
          fatal: true,
        })
        finalize()
        return
      }
      // 1006 (abnormal), 1008 (policy), 4xxx custom — try to reconnect.
      scheduleReconnect(evt.code)
    }

    ws.onerror = () => {
      // Errors precede onclose; let onclose drive the reconnect/finalize flow.
    }
  }

  const sendPermission = (requestId, decision, msg, updatedInput) => {
    if (ws && ws.readyState === WebSocket.OPEN) {
      const frame = { type: 'permission_response', request_id: requestId, decision }
      if (msg) frame.message = msg
      if (updatedInput) frame.updated_input = updatedInput
      wsSend(frame)
    }
  }

  const sendQueue = ({ id, text, attachments, images }) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    const frame = { type: 'queue', id, text }
    if (attachments && attachments.length > 0) frame.attachments = attachments
    if (images && images.length > 0) frame.images = images
    wsSend(frame)
    return true
  }

  const sendQueueCancel = (id) => {
    if (!ws || ws.readyState !== WebSocket.OPEN) return false
    wsSend({ type: 'queue_cancel', id })
    return true
  }

  const abort = () => {
    userAborted = true
    if (reconnectTimer) {
      clearTimeout(reconnectTimer)
      reconnectTimer = null
    }
    if (ws && ws.readyState === WebSocket.OPEN) {
      try {
        wsSend({ type: 'abort' })
      } catch {
        // ignore send errors on closing socket
      }
    }
    if (ws) ws.close()
    finalize()
  }

  connect()

  return { abort, sendPermission, sendQueue, sendQueueCancel }
}

/**
 * WebSocket-based streaming client.
 * Returns { abort, sendPermission, sendQueue, sendQueueCancel } — call abort() to cancel the stream.
 *
 * On unexpected close (non-clean, non-auth), auto-reconnects with backoff
 * [1, 2, 4, 8, 16] seconds for up to 5 attempts. Reconnects re-attach to
 * the same run (registry backend) or re-init with the same session_id.
 */
export function streamAgentRunWS(message, sessionId, onEvent, permissionMode, onComplete, model, attachments, mcpServers, images, trace, enableFileCheckpointing = false, cwd = null, addDirs = null, surfaceConnUi = null) {
  return openAgentWS({
    mode: 'init',
    message, sessionId, onEvent, permissionMode, onComplete, model, attachments,
    mcpServers, images, trace, enableFileCheckpointing, cwd, addDirs, surfaceConnUi,
  })
}

/**
 * Attach to a run already executing on the backend (page refresh / another
 * device). The server replays buffered events from `sinceSeq`, then follows
 * live. Same return surface as streamAgentRunWS — abort() cancels the RUN.
 */
export function attachAgentRunWS(sessionId, sinceSeq, onEvent, onComplete, trace, surfaceConnUi = null) {
  return openAgentWS({
    mode: 'attach',
    sessionId, sinceSeq, onEvent, onComplete, trace, surfaceConnUi,
  })
}

/**
 * POST-based SSE client.
 * Returns { abort } — call abort() to cancel the stream.
 */
export function streamAgentRun(message, sessionId, onEvent, permissionMode, onComplete, model, attachments, mcpServers, images, enableFileCheckpointing = false, cwd = null, addDirs = null) {
  const controller = new AbortController()

  const run = async () => {
    const body = { message, session_id: sessionId }
    if (permissionMode) {
      body.permission_mode = permissionMode
    }
    if (model) {
      body.model = model
    }
    if (attachments && attachments.length > 0) {
      body.attachments = attachments
    }
    if (images && images.length > 0) {
      body.images = images
    }
    if (mcpServers !== undefined) {
      body.mcp_servers = mcpServers
    }
    if (enableFileCheckpointing) {
      body.enable_file_checkpointing = true
    }
    if (cwd) {
      body.cwd = cwd
    }
    if (addDirs && addDirs.length > 0) {
      body.add_dirs = addDirs
    }
    debugLog('send', `SSE ▶ POST ${BASE_URL}/agent/run/stream`, body)
    const res = await fetch(`${BASE_URL}/agent/run/stream`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify(body),
      signal: controller.signal,
    })

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

    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })

      // Parse SSE frames
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      let currentEvent = null

      for (const line of lines) {
        if (line.startsWith('event: ')) {
          currentEvent = line.slice(7).trim()
        } else if (line.startsWith('data: ') && currentEvent) {
          try {
            const data = JSON.parse(line.slice(6))
            debugLog('recv', `SSE ◀ ${currentEvent}`, data)
            onEvent(currentEvent, data)
          } catch {
            // skip malformed JSON
          }
          currentEvent = null
        } else if (line === '') {
          currentEvent = null
        }
      }
    }
  }

  run().then(() => {
    if (onComplete) onComplete()
  }).catch((err) => {
    if (err.name !== 'AbortError') {
      onEvent('error', { message: err.message })
    }
    if (onComplete) onComplete()
  })

  return { abort: () => controller.abort() }
}

/**
 * Respond to a permission request.
 */
export async function respondPermission(sessionId, requestId, decision, message, updatedInput) {
  const body = {
    session_id: sessionId,
    request_id: requestId,
    decision,
  }
  if (message) body.message = message
  if (updatedInput) body.updated_input = updatedInput

  const res = await fetch(`${BASE_URL}/agent/permission/respond`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    if (res.status === 401) {
      window.dispatchEvent(new Event('auth:unauthorized'))
    }
    const text = await res.text()
    throw new Error(`Permission respond error ${res.status}: ${text}`)
  }
  return res.json()
}
