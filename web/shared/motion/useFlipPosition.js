import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import { DURATION, EASE_SPRING } from './tokens'

// framer-motion `layout="position"` equivalent (FLIP): when a re-render moves
// an element to a new layout position, it visually slides from where it was.
//
//   const ref = useRef(null)
//   useFlipPosition(ref, { duration, ease, disabled })
//   return <div ref={ref}>…</div>
//
// Mechanics:
// - Measures the LAYOUT position via offsetLeft/offsetTop — immune to both
//   scrolling and in-flight transforms, so unrelated re-renders (typing,
//   polling) never restart a slide: if layout didn't move, nothing happens.
// - When layout DID move, the current in-flight translate (read from the
//   computed transform matrix) is folded into the inverted delta, so an
//   interrupted slide continues from its current visual position — no jump.
// - Pre-writes the inverted translate synchronously in the layout effect
//   (before paint), then animates to identity. Transform is written in
//   anime's own vocabulary (translateX/translateY) so composition works.
// - Deliberately NO cleanup-cancel: a no-deps cleanup runs on every commit
//   and would freeze in-flight slides. Replaced tweens are handled by
//   anime's default composition ('replace').
// - StrictMode-safe: the doubled layout effect measures twice with zero
//   layout delta in between → epsilon-skips.
export function useFlipPosition(ref, { duration = DURATION.panel, ease = EASE_SPRING, disabled = false } = {}) {
  const lastPosRef = useRef(null)
  const optsRef = useRef({ duration, ease, disabled })
  optsRef.current = { duration, ease, disabled }

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) {
      lastPosRef.current = null
      return
    }
    const layout = { left: el.offsetLeft, top: el.offsetTop }
    const prev = lastPosRef.current
    lastPosRef.current = layout
    const { duration: dur, ease: es, disabled: off } = optsRef.current
    if (off || !prev) return

    const ldx = prev.left - layout.left
    const ldy = prev.top - layout.top
    // Layout didn't move → leave any in-flight slide alone.
    if (Math.abs(ldx) < 0.5 && Math.abs(ldy) < 0.5) return

    // Fold in the current in-flight translate so interruptions stay seamless.
    let tx = 0
    let ty = 0
    if (el.style.transform) {
      try {
        const m = new DOMMatrixReadOnly(getComputedStyle(el).transform)
        tx = m.m41
        ty = m.m42
      } catch { /* treat as untranslated */ }
    }
    const dx = ldx + tx
    const dy = ldy + ty

    // Invert (pre-paint), then play to identity.
    el.style.transform = `translateX(${dx}px) translateY(${dy}px)`
    animate(el, { translateX: 0, translateY: 0, duration: dur, ease: es })
  })
}

export default useFlipPosition
