import useToastStore from '../stores/toastStore'
import { getToken } from './tokenStore'
import { debugLog } from '../utils/debugLog'

const BASE_URL = '/api'
// Agent-runner (per-account pod) runtime API. Distinct from the control-plane BASE_URL
// so one client serves both faces: sandbox* helpers hit /api/sandbox/* (steered to the
// pod by the edge EPP); getJSON/postJSON/... stay on /api/* (control-panel: auth/admin/
// resource-models). Picking the helper at the call site IS the routing decision.
export const SANDBOX_BASE = '/api/sandbox'

export class UnauthorizedError extends Error {
  constructor(message = 'Unauthorized') {
    super(message)
    this.name = 'UnauthorizedError'
  }
}

function getAuthHeaders() {
  const token = getToken()
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

function pushApiToast(status, body) {
  try {
    useToastStore.getState().pushToast({
      level: 'error',
      title: `API error ${status}`,
      body: body ? String(body).slice(0, 400) : undefined,
    })
  } catch { /* toast unavailable */ }
}

const WAKE_RETRY_MAX = 6
const WAKE_RETRY_BASE_MS = 800
// A cold sandbox makes the edge HOLD the request (up to wake_hold_seconds=5s)
// before it answers — so the first 503 is late. Surface "waking" once a request
// has been pending past this threshold, so the user gets feedback immediately
// instead of staring at a blank page for the whole hold. Warm pods answer well
// under this, so it doesn't fire on normal requests.
const WAKE_SLOW_MS = 900
const WAKE_TOAST_ID = 'agent-sandbox-waking'
const READY_TOAST_ID = 'agent-sandbox-ready'

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function notifyWaking() {
  try {
    useToastStore.getState().pushToast({
      id: WAKE_TOAST_ID,          // deduped: refreshed (not stacked) on each retry
      level: 'info',
      title: 'Agent sandbox is waking…',
      dismissAfterMs: 8000,       // backstop; cleared explicitly on ready/failure
    })
  } catch { /* toast unavailable */ }
}

function clearWaking() {
  try { useToastStore.getState().dismissToast(WAKE_TOAST_ID) } catch { /* ignore */ }
}

function notifyReady() {
  try {
    useToastStore.getState().pushToast({
      id: READY_TOAST_ID,
      level: 'success',
      title: 'Agent is ready',
    })
  } catch { /* toast unavailable */ }
}

// The edge EPP returns 503 while the per-account sandbox is cold-starting, and
// it 503s BEFORE the request reaches the pod (so a retry is safe even for POSTs).
// Retry with backoff so cold-start is seamless: show one transient "Agent sandbox
// is waking…" notice while it boots, then an "Agent is ready" confirmation once it
// answers. Only the final response reaches handleAPIResponse.
export async function fetchWithWake(url, init) {
  // Debug logging (Settings → Advanced → Developer Mode): every outgoing request
  // funnels through here, so one log call covers all REST helpers below.
  let _loggedBody
  try { _loggedBody = init?.body ? JSON.parse(init.body) : undefined } catch { _loggedBody = init?.body }
  debugLog('send', `${init?.method || 'GET'} ${url}`, _loggedBody)

  let res
  let waited = false
  const markWaking = () => { waited = true; notifyWaking() }
  // Pre-503 awareness: if the first attempt is still pending past WAKE_SLOW_MS the
  // edge is holding it for a cold sandbox — show "waking" now, not after the hold.
  const slowTimer = setTimeout(markWaking, WAKE_SLOW_MS)
  try {
    for (let attempt = 0; ; attempt++) {
      res = await fetch(url, init)
      if (res.status !== 503 || attempt >= WAKE_RETRY_MAX) break
      markWaking()
      await sleep(WAKE_RETRY_BASE_MS * Math.min(attempt + 1, 5))
    }
  } finally {
    clearTimeout(slowTimer)
  }
  if (waited) {
    clearWaking()
    if (res.status !== 503) notifyReady()  // sandbox woke and answered
  }
  return res
}

export async function handleAPIResponse(res) {
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:unauthorized'))
    const text = await res.text()
    pushApiToast(res.status, text)
    throw new UnauthorizedError(`API error ${res.status}: ${text}`)
  }
  if (!res.ok) {
    const text = await res.text()
    // 503 is the edge EPP's transient "sandbox is waking" signal — never surface
    // it as a hard error toast. The waking/ready toasts (fetchWithWake) cover it,
    // and the request still throws so callers fall back / retry.
    if (res.status !== 503) pushApiToast(res.status, text)
    throw new Error(`API error ${res.status}: ${text}`)
  }
  const data = await res.json()
  debugLog('recv', `${res.status} ${res.url}`, data)
  return data
}

export async function postJSON(path, body) {
  const res = await fetchWithWake(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleAPIResponse(res)
}

export async function getJSON(path) {
  const res = await fetchWithWake(`${BASE_URL}${path}`, {
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

// Wake-free control-plane GET. Use this only for endpoints that are guaranteed to
// stay on Control Panel rather than an InferencePool. A 503 here is a real control-
// plane dependency failure, not the EPP's "pod is waking" signal, so it must neither
// retry nor display the Agent-sandbox waking/ready notices.
export async function controlPlaneGet(path) {
  const url = `${BASE_URL}${path}`
  debugLog('send', `GET ${url}`)
  const res = await fetch(url, { headers: { ...getAuthHeaders() } })
  return handleAPIResponse(res)
}

export async function putJSON(path, body) {
  const res = await fetchWithWake(`${BASE_URL}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleAPIResponse(res)
}

export async function deleteJSON(path) {
  const res = await fetchWithWake(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

// --- Agent-runner (sandbox) variants: identical helpers rooted at SANDBOX_BASE. They
// reuse fetchWithWake, so the cold-start "waking"/"ready" UX applies to pod calls. ---
export async function sandboxPost(path, body) {
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleAPIResponse(res)
}

export async function sandboxGet(path) {
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, {
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

// Large-body-safe GET: rides the control-panel "/" lane (no GIE/EPP ~8KB ext_proc
// truncation), falling back to the direct sandbox lane on 404 (route not deployed)
// or network error. Same signature as sandboxGet(path) — rerouting a >8KB-risk read
// is a drop-in sandboxGet → sandboxRead swap. Plain writes stay on sandboxPost/Put/etc
// (their responses are small), but the EPP truncates the *response* body regardless of
// method — a POST whose reply is large (see sandboxReadPost) truncates just like a GET.
export async function sandboxRead(path) {
  try {
    const res = await fetchWithWake(`/api/cp-proxy${path}`, { headers: { ...getAuthHeaders() } })
    if (res.status !== 404) return handleAPIResponse(res)
  } catch { /* network error → fall back to the direct lane */ }
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, { headers: { ...getAuthHeaders() } })
  return handleAPIResponse(res)
}

// Large-body-safe POST: the sandboxRead cp-proxy lane for POSTs whose *response* can
// cross the ~8KB EPP cap (e.g. MCP validate replies with every tool's input_schema;
// validate/tool replies with the tool's full output). The backend cp-proxy accepts
// POST (control-panel _cp_proxy_post → _proxy_runner_request method="POST"). Same
// 404/network fallback to the direct lane as sandboxRead. Only use for idempotent,
// side-effect-free "reads dressed as POST" — the fallback may re-send the body.
export async function sandboxReadPost(path, body) {
  const init = {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  }
  try {
    const res = await fetchWithWake(`/api/cp-proxy${path}`, init)
    if (res.status !== 404) return handleAPIResponse(res)
  } catch { /* network error → fall back to the direct lane */ }
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, init)
  return handleAPIResponse(res)
}

export async function sandboxPut(path, body) {
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleAPIResponse(res)
}

export async function sandboxDelete(path) {
  const res = await fetchWithWake(`${SANDBOX_BASE}${path}`, {
    method: 'DELETE',
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

export async function agentRun(message, sessionId) {
  return sandboxPost('/agent/run', { message, session_id: sessionId })
}

/**
 * Returns the backend base origin for non-API routes (e.g. /docs).
 * - Direct mount (same origin): returns '' (relative path works)
 * - Separate deploy (dev proxy): detects via a probe to /api and falls back to current origin
 */
export function getBackendOrigin() {
  // In production (direct mount or reverse proxy), relative paths just work
  // In dev, the Vite proxy handles /api but browser navigations to /docs
  // get caught by SPA fallback. We need the actual backend origin.
  if (import.meta.env.DEV) {
    // Dev mode: backend is on the proxy target
    // Read from env or fall back to the known dev default
    return import.meta.env.VITE_BACKEND_URL || 'http://localhost:8081'
  }
  // Production: same origin
  return ''
}

export { getAuthHeaders }
