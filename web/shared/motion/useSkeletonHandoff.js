import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import usePresence from './usePresence'
import useReducedMotion from './useReducedMotion'
import { EASE_OUT } from './tokens'

// Approved M13 + T9 spec: skeleton → content handoff. When `loading` flips
// false the skeleton lifts out of flow (absolute, at its old box) and fades
// ≤150ms while the real content rises 6px into place underneath — heights
// may differ freely, nothing double-stacks.
//
//   const h = useSkeletonHandoff(loading)
//   <div style={{ position: 'relative' }}>          // positioned ancestor!
//     {h.skeletonMounted && <div ref={h.skeletonRef}><Skeleton …/></div>}
//     {!loading && <div ref={h.contentRef}>{content}</div>}
//   </div>
//
// The shimmer itself is untouched. Reduced motion: instant swap.
export function useSkeletonHandoff(loading, { fadeMs = 150, riseMs = 200, rise = 6 } = {}) {
  const { mounted: skeletonMounted, onExited } = usePresence(loading)
  const reducedMotion = useReducedMotion()
  const skeletonRef = useRef(null)
  const contentRef = useRef(null)
  const contentShownRef = useRef(!loading)

  useLayoutEffect(() => {
    if (loading) {
      contentShownRef.current = false
      return
    }
    // loading just finished —
    const sk = skeletonRef.current
    if (skeletonMounted && sk) {
      if (reducedMotion) {
        onExited()
      } else {
        // Lift the skeleton out of flow at its current box, fade it above
        // the incoming content.
        sk.style.position = 'absolute'
        sk.style.top = `${sk.offsetTop}px`
        sk.style.left = `${sk.offsetLeft}px`
        sk.style.width = `${sk.offsetWidth}px`
        sk.style.pointerEvents = 'none'
        sk.style.zIndex = '1'
        animate(sk, { opacity: 0, duration: fadeMs, ease: EASE_OUT, onComplete: onExited })
      }
    } else if (skeletonMounted) {
      onExited()
    }
    const content = contentRef.current
    if (content && !contentShownRef.current) {
      contentShownRef.current = true
      if (!reducedMotion) {
        content.style.opacity = '0'
        content.style.transform = `translateY(${rise}px)`
        animate(content, { opacity: 1, translateY: '0px', duration: riseMs, ease: EASE_OUT })
      }
    }
  }, [loading, skeletonMounted, reducedMotion, fadeMs, riseMs, rise, onExited])

  return { skeletonMounted, skeletonRef, contentRef }
}

export default useSkeletonHandoff
