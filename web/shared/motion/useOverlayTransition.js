import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import usePresence from './usePresence'
import useReducedMotion from './useReducedMotion'
import { DURATION, EASE_SPRING, EASE_TAB } from './tokens'

// Enter/exit envelope for modals, drawers and banners (M2).
// Exit mirrors enter's values but not its timing: the entrance spring
// front-loads ~80% of its progress into the first 40ms, so an exit played on
// it is over in 2-3 frames and reads as a hard cut. Exits run on EASE_TAB and
// ~25% longer than the enter (user-tuned), which spreads the fade across a
// clearly visible window.
//
//   const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'scale' })
//   if (!mounted) return null
//   return (
//     <div ref={backdropRef} style={{ position: 'fixed', inset: 0, ... }}>
//       <div ref={panelRef} ...>…</div>
//     </div>
//   )
//
// Variants (durations ms, enter/exit):
//   scale       — center modals: panel opacity 0↔1 + scale .98↔1, 200/250
//   cornerScale — anchored cards: panel opacity 0↔1 + scale .92↔1, 200/250
//   drawer      — right drawers: panel translateX +100%↔0, 220/280
//   slide       — banners: panel opacity 0↔1 + translateY -8px↔0, 200/250
// Backdrop (optional ref): opacity 0↔1 over the same duration. Blur stays a
// constant backdrop-filter on the layer — only its opacity animates.
//
// Notes:
// - Enter pre-paints the from-state with direct style writes (layout effect,
//   before paint) ONLY on a fresh mount; a reopen mid-exit retargets from the
//   current values (to-only tweens + anime composition 'replace') — no jump.
// - Exit completion calls usePresence's onExited, which re-checks the latest
//   open flag, so a reopen racing a stale onComplete never unmounts the panel.
// - Reduced motion: no animation either way — natural styles on open, instant
//   unmount on close.
// - Keeping the subtree mounted ~200ms after close means the consumer must
//   SNAPSHOT any store data that is nulled on close (render from the
//   snapshot, or the exit frame crashes).
const VARIANTS = {
  scale: {
    duration: DURATION.panel,
    exitDuration: 250,
    preEnter: (el) => {
      el.style.opacity = '0'
      el.style.transform = 'scale(0.98)'
    },
    enter: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.98 },
  },
  cornerScale: {
    duration: DURATION.panel,
    exitDuration: 250,
    preEnter: (el) => {
      el.style.opacity = '0'
      el.style.transform = 'scale(0.92)'
    },
    enter: { opacity: 1, scale: 1 },
    exit: { opacity: 0, scale: 0.92 },
  },
  drawer: {
    duration: DURATION.canvas,
    exitDuration: 280,
    preEnter: (el) => {
      el.style.transform = 'translateX(100%)'
    },
    enter: { translateX: '0%' },
    exit: { translateX: '100%' },
  },
  slide: {
    duration: DURATION.panel,
    exitDuration: 250,
    preEnter: (el) => {
      el.style.opacity = '0'
      el.style.transform = 'translateY(-8px)'
    },
    enter: { opacity: 1, translateY: '0px' },
    exit: { opacity: 0, translateY: '-8px' },
  },
}

// exitCollapse: for IN-FLOW panels (banners) whose siblings would otherwise
// snap when the panel unmounts — the exit additionally tweens height to 0 so
// the space closes with the fade; a reopen mid-exit tweens it back.
export function useOverlayTransition({ open, variant = 'scale', duration, exitDuration, exitCollapse = false } = {}) {
  const { mounted, onExited } = usePresence(open)
  const reducedMotion = useReducedMotion()
  const panelRef = useRef(null)
  const backdropRef = useRef(null)
  const enteredRef = useRef(false)
  const naturalHeightRef = useRef(null)

  useLayoutEffect(() => {
    if (!mounted) {
      enteredRef.current = false
      return
    }
    const spec = VARIANTS[variant] || VARIANTS.scale
    const dur = duration ?? spec.duration
    // Explicit duration override applies to both legs unless exitDuration is
    // also given; variant defaults keep their own exit timing.
    const exitDur = exitDuration ?? duration ?? spec.exitDuration ?? dur
    const panel = panelRef.current
    const backdrop = backdropRef.current

    if (!panel) {
      // Nothing attached this commit (e.g. an inner guard returned null).
      if (!open) onExited()
      return
    }

    if (reducedMotion) {
      // Natural styles are the final open state; closing is instant.
      if (open) enteredRef.current = true
      else onExited()
      return
    }

    if (open) {
      const enterProps = { ...spec.enter }
      if (!enteredRef.current) {
        // Fresh mount: pre-paint the from-state before first paint.
        spec.preEnter(panel)
        if (backdrop) backdrop.style.opacity = '0'
        if (exitCollapse) naturalHeightRef.current = panel.offsetHeight
      } else if (exitCollapse && panel.style.height) {
        // Reopen mid-exit: grow the collapsing panel back to natural height.
        enterProps.height = `${naturalHeightRef.current}px`
      }
      enteredRef.current = true
      animate(panel, {
        ...enterProps,
        duration: dur,
        ease: EASE_SPRING,
        // Only the overflow clip is cleared: consumers may declare height in
        // their style prop (React owns that inline value), and the settled
        // tween height equals it anyway.
        onComplete: () => {
          if (exitCollapse) panel.style.overflow = ''
        },
      })
      if (backdrop) animate(backdrop, { opacity: 1, duration: dur, ease: EASE_SPRING })
    } else {
      const exitProps = { ...spec.exit }
      if (exitCollapse) {
        if (!panel.style.height) panel.style.height = `${panel.offsetHeight}px`
        panel.style.overflow = 'hidden'
        exitProps.height = '0px'
      }
      animate(panel, { ...exitProps, duration: exitDur, ease: EASE_TAB, onComplete: onExited })
      if (backdrop) animate(backdrop, { opacity: 0, duration: exitDur, ease: EASE_TAB })
    }
    // No cleanup-cancel: retargets are handled by composition 'replace', and a
    // replaced exit's late onComplete is defused inside onExited itself.
  }, [open, mounted, reducedMotion, variant, duration, exitDuration, exitCollapse, onExited])

  return { mounted, panelRef, backdropRef }
}

export default useOverlayTransition
