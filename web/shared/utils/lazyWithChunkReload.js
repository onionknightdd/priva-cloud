import { lazy } from 'react'

const RELOAD_KEY = 'priva:chunk-reload-attempted-at'
const RELOAD_WINDOW_MS = 5 * 60 * 1000

function getSessionItem(key) {
  try {
    return window.sessionStorage.getItem(key)
  } catch {
    return null
  }
}

function setSessionItem(key, value) {
  try {
    window.sessionStorage.setItem(key, value)
    return true
  } catch {
    return false
  }
}

function removeSessionItem(key) {
  try {
    window.sessionStorage.removeItem(key)
  } catch {
    // ignore storage failures; the import result is still authoritative
  }
}

export function isChunkLoadError(error) {
  const name = String(error?.name || '')
  const message = String(error?.message || error || '')
  return (
    name === 'ChunkLoadError' ||
    message.includes('Failed to fetch dynamically imported module') ||
    message.includes('error loading dynamically imported module') ||
    message.includes('Importing a module script failed') ||
    message.includes('Loading chunk') ||
    message.includes('dynamically imported module')
  )
}

function shouldReloadForChunkError() {
  if (typeof window === 'undefined') return false

  const now = Date.now()
  const previous = Number(getSessionItem(RELOAD_KEY))
  if (Number.isFinite(previous) && now - previous < RELOAD_WINDOW_MS) {
    return false
  }

  return setSessionItem(RELOAD_KEY, String(now))
}

export default function lazyWithChunkReload(loader) {
  return lazy(() => (
    loader()
      .then((module) => {
        removeSessionItem(RELOAD_KEY)
        return module
      })
      .catch((error) => {
        if (isChunkLoadError(error) && shouldReloadForChunkError()) {
          window.location.reload()
          return new Promise(() => {})
        }
        throw error
      })
  ))
}
