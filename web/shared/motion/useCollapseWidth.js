import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import usePresence from './usePresence'
import useReducedMotion from './useReducedMotion'
import { DURATION, EASE_SPRING, EASE_TAB } from './tokens'

// Show/hide envelope for IN-LAYOUT side panes (canvas panel, preview drawer):
// the pane's width animates 0 ↔ width so surrounding layout is pushed
// smoothly both ways, then the pane unmounts.
//
//   const { mounted, rootRef } = useCollapseWidth({ open, width })
//   if (!mounted) return null
//   return (
//     <div ref={rootRef} style={{ width, overflow: 'hidden', ... }}>
//       <div style={{ width, height: '100%' }}>…content at full width…</div>
//     </div>
//   )
//
// The inner wrapper keeps content at its final width so nothing reflows while
// the outer width animates — the pane is revealed, not squished.
//
// `width` is deliberately NOT an effect dependency: live drag-resizes write
// width through React's style prop (1:1, no easing); the animator only runs
// on open/close transitions and reads the width current at that moment.
// Exit runs on EASE_TAB, slightly longer than the enter — the entrance spring
// front-loads ~80% of its progress into the first 40ms, so a collapse played
// on it reads as a hard cut (same fix as the overlay exits).
export function useCollapseWidth({
  open,
  width,
  duration = DURATION.canvas,
  ease = EASE_SPRING,
  exitDuration = 280,
  exitEase = EASE_TAB,
}) {
  const { mounted, onExited } = usePresence(open)
  const reducedMotion = useReducedMotion()
  const rootRef = useRef(null)
  const enteredRef = useRef(false)
  const widthRef = useRef(width)
  widthRef.current = width

  useLayoutEffect(() => {
    if (!mounted) {
      enteredRef.current = false
      return
    }
    const el = rootRef.current
    if (!el) {
      if (!open) onExited()
      return
    }
    const w = widthRef.current
    if (reducedMotion) {
      if (open) {
        enteredRef.current = true
        el.style.width = `${w}px`
      } else {
        onExited()
      }
      return
    }
    if (open) {
      if (!enteredRef.current) el.style.width = '0px' // pre-paint collapsed
      enteredRef.current = true
      animate(el, { width: `${w}px`, duration, ease })
    } else {
      animate(el, { width: '0px', duration: exitDuration, ease: exitEase, onComplete: onExited })
    }
  }, [open, mounted, reducedMotion, duration, ease, exitDuration, exitEase, onExited])

  return { mounted, rootRef }
}

export default useCollapseWidth
