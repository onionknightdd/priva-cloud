import { useCallback, useRef, useState } from 'react'

// Minimal AnimatePresence replacement: keeps a component mounted while its
// exit animation plays.
//
//   const { mounted, onExited } = usePresence(open)
//   ...
//   useLayoutEffect(() => {
//     if (!mounted) return
//     if (open) { /* play enter */ } else { animate(el, { ..., onComplete: onExited }) }
//   }, [open, mounted])
//   if (!mounted) return null
//
// - Mounts via a render-phase latch (no extra effect tick — the enter frame is
//   painted with the element already in the tree).
// - `onExited` is stable and re-checks the LATEST open value, so a rapid
//   close→reopen never unmounts a panel that is meant to be visible, and a
//   stale onComplete from a replaced exit animation is a safe no-op.
// - StrictMode-inert: no effects, no cleanups.
export function usePresence(open) {
  const [mounted, setMounted] = useState(open)
  const openRef = useRef(open)
  openRef.current = open

  // Render-phase latch: opening mounts immediately (same render pass).
  if (open && !mounted) setMounted(true)

  const onExited = useCallback(() => {
    if (!openRef.current) setMounted(false)
  }, [])

  return { mounted, onExited }
}

export default usePresence
