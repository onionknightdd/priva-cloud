import { useEffect, useRef, useState } from 'react'
import { animate, utils } from 'animejs'
import useReducedMotion from './useReducedMotion'
import { EASE_OUT } from './tokens'

// Tweens a numeric stat to its new value (approved I3/T1 spec): ≤500ms
// ease-out, retargets mid-flight, INSTANT on first paint and under reduced
// motion. Returns the display number — apply your formatter to the result:
//
//   const shown = useAnimatedNumber(bytesUsed)
//   …{formatBytes(shown)}…
export function useAnimatedNumber(target, { duration = 400, decimals = 0 } = {}) {
  const numeric = Number.isFinite(Number(target)) ? Number(target) : 0
  const reducedMotion = useReducedMotion()
  const stRef = useRef(null)
  if (stRef.current === null) stRef.current = { v: numeric, anim: null, first: true }
  const [display, setDisplay] = useState(numeric)

  useEffect(() => {
    const st = stRef.current
    if (st.first) {
      // First paint: snap (no 0→value roll on page load).
      st.first = false
      st.v = numeric
      return
    }
    if (st.v === numeric) return
    st.anim?.cancel()
    if (reducedMotion) {
      st.v = numeric
      setDisplay(numeric)
      return
    }
    st.anim = animate(st, {
      v: numeric,
      duration,
      ease: EASE_OUT,
      onUpdate: () => setDisplay(utils.round(st.v, decimals)),
      onComplete: () => setDisplay(numeric),
    })
  }, [numeric, reducedMotion, duration, decimals])

  useEffect(() => () => { stRef.current.anim?.cancel() }, [])

  return display
}

export default useAnimatedNumber
