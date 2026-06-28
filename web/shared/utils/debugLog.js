// Developer-mode console logging for the API layer.
//
// The flags live in localStorage (written by Settings → Advanced → Developer
// Mode) so the plain-JS API layer can read them WITHOUT importing a React/
// Zustand store — keeping `web/shared` free of any `web/user` coupling. Logging
// is active only when BOTH the master Developer Mode and the Debug Logging
// switch are on, so turning Developer Mode off silences everything at once.
import safeStorage from './safeStorage'

export const DEVELOPER_MODE_KEY = 'priva-developer-mode'
export const DEBUG_LOGGING_KEY = 'priva-debug-mode'

export function isDebugLoggingOn() {
  return (
    safeStorage.getItem(DEVELOPER_MODE_KEY) === '1' &&
    safeStorage.getItem(DEBUG_LOGGING_KEY) === '1'
  )
}

// Console chip styles. Hex literals (not CSS variables) are required here: this
// is console.log %c styling, not DOM styling — it can't resolve CSS variables.
// Values mirror the design tokens: --blue, --green, --text-secondary, --text-inverse.
const CHIP = {
  send: 'background:#58a6ff;color:#0d1117;padding:1px 6px;border-radius:3px;font-weight:600',
  recv: 'background:#3fb950;color:#0d1117;padding:1px 6px;border-radius:3px;font-weight:600',
}
const LABEL_STYLE = 'color:#8b949e'

/**
 * Log one API event to the browser console when debug logging is on; no-op otherwise.
 * @param {'send'|'recv'} direction  'send' = outgoing message, 'recv' = chunk from backend
 * @param {string} label             short, human-readable summary (e.g. "POST /api/sandbox/agent/run")
 * @param {*} [payload]              the message/chunk body (logged as a live, inspectable object)
 */
export function debugLog(direction, label, payload) {
  if (!isDebugLoggingOn()) return
  const chip = CHIP[direction] || CHIP.recv
  const tag = direction === 'send' ? 'API ▶ SEND' : 'API ◀ RECV'
  try {
    if (payload === undefined) {
      console.log(`%c${tag}%c ${label}`, chip, LABEL_STYLE)
    } else {
      console.log(`%c${tag}%c ${label}`, chip, LABEL_STYLE, payload)
    }
  } catch {
    // console unavailable — debug logging must never break the app
  }
}
