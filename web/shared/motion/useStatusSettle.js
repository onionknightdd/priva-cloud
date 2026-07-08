import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import useReducedMotion from './useReducedMotion'
import { EASE_OUT, EASE_IN_OUT } from './tokens'

// Approved M9 + T3 spec: the one-shot "resolved" beat when a status flips
// LIVE (never on history mounts — both hooks ignore the mount-time value).

// Scale settle: container pops 0.98 → 1 over ~120ms when statusKey changes.
//   const settleRef = useStatusSettle(status)
//   <span ref={settleRef}>…icon…</span>
export function useStatusSettle(statusKey, { scale = 0.98, duration = 120 } = {}) {
  const ref = useRef(null)
  const prevRef = useRef(statusKey)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    if (statusKey === prevRef.current) return
    prevRef.current = statusKey
    const el = ref.current
    if (!el || reducedMotion) return
    el.style.transform = `scale(${scale})`
    animate(el, { scale: 1, duration, ease: EASE_OUT })
  }, [statusKey, reducedMotion, scale, duration])

  return ref
}

// Resolve a semantic CSS var ('var(--green)' / '--green') to its computed
// color so anime can interpolate it.
export function resolveCssColor(el, color) {
  if (!color) return null
  const m = /^var\((--[\w-]+)\)$/.exec(color.trim())
  const name = m ? m[1] : (color.startsWith('--') ? color : null)
  if (!name) return color // already a literal color
  const doc = el?.ownerDocument || document
  const v = getComputedStyle(doc.documentElement).getPropertyValue(name).trim()
  return v || null
}

// Color tween (T3): when `color` (a semantic var) changes live, the given
// style property blends old → new over 150ms instead of snapping. Works
// against React-declared styles: the old computed color is pre-painted back
// before React's instant write reaches the screen.
//   const borderRef = useStatusColorTween(stateColor, { property: 'borderLeftColor' })
export function useStatusColorTween(color, { property = 'borderLeftColor', duration = 150 } = {}) {
  const ref = useRef(null)
  const prevRef = useRef(color)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    if (color === prevRef.current) return
    const prevColor = prevRef.current
    prevRef.current = color
    const el = ref.current
    if (!el || reducedMotion) return
    const from = resolveCssColor(el, prevColor)
    const to = resolveCssColor(el, color)
    if (!from || !to) return
    el.style[property] = from // pre-paint: undo React's instant swap
    animate(el, { [property]: to, duration, ease: EASE_IN_OUT })
  }, [color, reducedMotion, property, duration])

  return ref
}

export default useStatusSettle
