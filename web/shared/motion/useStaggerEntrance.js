import { useCallback, useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import useReducedMotion from './useReducedMotion'
import { EASE_OUT } from './tokens'

// New-rows-only list entrance (approved M3 spec): rows the list has not seen
// before fade + rise in (opacity 0→1, translateY 8px→0, 200ms), staggered
// ~30ms apiece capped to a ≤250ms window. Rows present on the very first
// commit, re-sorted/filtered rows, and virtualizer-recycled rows never
// animate (seen-set gate — same idea as MessageList's mountedCountRef).
//
//   const entranceRef = useStaggerEntrance()
//   …rows.map((row) => <div key={row.id} ref={entranceRef(row.id)}>…</div>)
//
// In virtualized lists attach the ref to the INNER content div, never the
// absolutely-positioned row shell.
export function useStaggerEntrance({ duration = 200, rise = 8, stepMs = 30, windowMs = 250 } = {}) {
  const seenRef = useRef(new Set())
  const firstCommitRef = useRef(true)
  const batchRef = useRef({ t: 0, count: 0 })
  const reducedMotion = useReducedMotion()
  const reducedRef = useRef(reducedMotion)
  reducedRef.current = reducedMotion

  useLayoutEffect(() => {
    firstCommitRef.current = false
  }, [])

  // Stable factory so memo'd rows can take it as a prop without re-rendering.
  return useCallback((key) => (el) => {
    if (!el || seenRef.current.has(key)) return
    seenRef.current.add(key)
    if (firstCommitRef.current || reducedRef.current) return
    // Rows mounting within the same ~frame share one stagger batch.
    const now = performance.now()
    if (now - batchRef.current.t > 50) batchRef.current = { t: now, count: 0 }
    const delay = Math.min(batchRef.current.count * stepMs, windowMs)
    batchRef.current.count += 1
    el.style.opacity = '0'
    el.style.transform = `translateY(${rise}px)`
    animate(el, { opacity: 1, translateY: '0px', duration, delay, ease: EASE_OUT })
  }, [duration, rise, stepMs, windowMs])
}

export default useStaggerEntrance
