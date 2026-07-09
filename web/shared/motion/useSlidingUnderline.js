import { useCallback, useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import { DUR_MIGRATION, EASE_TAB } from './tokens'
import { useReducedMotion } from './useReducedMotion'

export function useSlidingUnderline(activeKey) {
  const indicatorRef = useRef(null)
  const itemRefs = useRef(new Map())
  const animationRef = useRef(null)
  const measuredRef = useRef(false)
  const lastRectRef = useRef(null)
  const reducedMotion = useReducedMotion()

  const setItemRef = useCallback((key) => (node) => {
    if (node) itemRefs.current.set(key, node)
    else itemRefs.current.delete(key)
  }, [])

  useLayoutEffect(() => {
    const indicator = indicatorRef.current
    if (!indicator) return

    const activeEl = itemRefs.current.get(activeKey)
    if (!activeEl) {
      indicator.style.opacity = '0'
      measuredRef.current = false
      lastRectRef.current = null
      animationRef.current?.cancel()
      animationRef.current = null
      return
    }

    const next = {
      left: activeEl.offsetLeft,
      width: activeEl.offsetWidth,
    }
    const previous = lastRectRef.current
    if (
      measuredRef.current &&
      previous &&
      Math.abs(previous.left - next.left) < 0.5 &&
      Math.abs(previous.width - next.width) < 0.5
    ) {
      indicator.style.opacity = '1'
      return
    }

    animationRef.current?.cancel()
    indicator.style.opacity = '1'

    if (!measuredRef.current || reducedMotion) {
      indicator.style.left = `${next.left}px`
      indicator.style.width = `${next.width}px`
      measuredRef.current = true
      lastRectRef.current = next
      return
    }

    animationRef.current = animate(indicator, {
      left: `${next.left}px`,
      width: `${next.width}px`,
      duration: DUR_MIGRATION.tabSlide,
      ease: EASE_TAB,
      onComplete: () => {
        animationRef.current = null
      },
    })
    lastRectRef.current = next
  })

  useLayoutEffect(() => () => {
    animationRef.current?.cancel()
  }, [])

  return { indicatorRef, setItemRef }
}
