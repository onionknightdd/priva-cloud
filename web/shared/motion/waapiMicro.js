import { waapi } from 'animejs'

// Button micro-feedback via Anime.js' WAAPI bridge (M15).
//
// These transform-only ticks are handed to the browser Web Animations API, so
// they stay compositor-friendly while keeping all motion helpers on Anime.js.
//
// Contract:
// - every effect ≤150ms, fire-and-forget — call in the handler WITHOUT
//   awaiting; never delays the click/pointer handler itself
// - no overshoot past scale(1.08)
// - prefers-reduced-motion: reduce → no-op
// - each helper returns the Anime.js WAAPIAnimation, or null when skipped

const QUERY = '(prefers-reduced-motion: reduce)'

// MediaQueryList.matches is live — capturing the list once is enough to track
// the user flipping the OS setting mid-session.
const mql = typeof window !== 'undefined' && typeof window.matchMedia === 'function'
  ? window.matchMedia(QUERY)
  : null

function canAnimate(el) {
  if (!el || typeof el.animate !== 'function' || typeof waapi?.animate !== 'function') return false
  return !(mql && mql.matches)
}

/** Scale from→1, ease-out — for a button that just appeared. */
export function popIn(el, { from = 0.8, duration = 150 } = {}) {
  if (!canAnimate(el)) return null
  return waapi.animate(el, {
    scale: [from, 1],
    duration,
    ease: 'ease-out',
  })
}

/** Quick down-up press tick (1→to→1). Call on pointerdown/click, don't await. */
export function pressTick(el, { to = 0.94, duration = 120 } = {}) {
  if (!canAnimate(el)) return null
  return waapi.animate(el, {
    scale: [1, to, 1],
    duration,
    ease: 'ease-out',
  })
}

/** One-shot glyph pop 1→to→1 (to stays ≤1.08 — the overshoot ceiling). */
export function glyphPop(el, { to = 1.08, duration = 150 } = {}) {
  if (!canAnimate(el)) return null
  return waapi.animate(el, {
    scale: [1, to, 1],
    duration,
    ease: 'ease-in-out',
  })
}
