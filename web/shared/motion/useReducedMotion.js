import { useSyncExternalStore } from 'react'

// Drop-in replacement for framer-motion's useReducedMotion.
// Subscribes to the OS/browser 'prefers-reduced-motion: reduce' media query and
// updates live when the user flips the setting.
const QUERY = '(prefers-reduced-motion: reduce)'

const mql = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  ? window.matchMedia(QUERY)
  : null

const subscribe = (onChange) => {
  if (!mql) return () => {}
  mql.addEventListener('change', onChange)
  return () => mql.removeEventListener('change', onChange)
}

const getSnapshot = () => !!mql && mql.matches
const getServerSnapshot = () => false

export function useReducedMotion() {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}

export default useReducedMotion
