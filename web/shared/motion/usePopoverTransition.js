import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import usePresence from './usePresence'
import useReducedMotion from './useReducedMotion'
import { DURATION, EASE_SPRING, EASE_TAB } from './tokens'

// Canonical popover/menu envelope (approved M7 spec) — mirrors the shared
// Dropdown exactly: opacity + 4px translate, 200ms spring in. The exit runs
// on EASE_TAB and 25% longer — the entrance spring front-loads ~80% of its
// progress into the first 40ms, so a dismissal played on it reads as a hard
// cut. For conditionally-rendered menus; keeps the menu mounted through its
// exit.
//
//   const { mounted, popRef } = usePopoverTransition({ open, placement: 'bottom' })
//   {mounted && <div ref={popRef} style={{ position: 'absolute', ... }}>…</div>}
//
// placement 'bottom' = menu below its trigger (enters dropping from -4px);
// 'top' = menu above (enters rising from +4px). 'right'/'left' are for
// side submenus. Reduced motion: instant.
export function usePopoverTransition({
  open,
  placement = 'bottom',
  distance = 4,
  duration = DURATION.panel,
  exitDuration,
} = {}) {
  const exitDur = exitDuration ?? Math.round(duration * 1.25)
  const { mounted, onExited } = usePresence(open)
  const reducedMotion = useReducedMotion()
  const popRef = useRef(null)
  const enteredRef = useRef(false)
  const horizontal = placement === 'left' || placement === 'right'
  const translateProp = horizontal ? 'translateX' : 'translateY'
  const fromOffset = placement === 'top' || placement === 'left' ? distance : -distance

  useLayoutEffect(() => {
    if (!mounted) {
      enteredRef.current = false
      return
    }
    const el = popRef.current
    if (!el) {
      if (!open) onExited()
      return
    }
    if (reducedMotion) {
      if (open) enteredRef.current = true
      else onExited()
      return
    }
    if (open) {
      if (!enteredRef.current) {
        el.style.opacity = '0'
        el.style.transform = `${translateProp}(${fromOffset}px)`
      }
      enteredRef.current = true
      animate(el, { opacity: 1, [translateProp]: '0px', duration, ease: EASE_SPRING })
    } else {
      animate(el, {
        opacity: 0,
        [translateProp]: `${fromOffset}px`,
        duration: exitDur,
        ease: EASE_TAB,
        onComplete: onExited,
      })
    }
  }, [open, mounted, reducedMotion, translateProp, fromOffset, duration, exitDur, onExited])

  return { mounted, popRef }
}

export default usePopoverTransition
