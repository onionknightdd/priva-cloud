import { useLayoutEffect, useRef, useState } from 'react'
import { animate } from 'animejs'
import useReducedMotion from './useReducedMotion'
import { EASE_SPRING } from './tokens'

// Approved M8 spec: directional wizard-step pane swap. The outgoing step is
// retained in an absolute overlay and slides out (∓16px + fade) while the new
// step slides in (±16px → 0) and the container height eases to fit — 200ms,
// interruptible (a Back press mid-slide simply retargets).
//
//   <StepSlide stepKey={step}>{renderStep(step)}</StepSlide>
//
// Direction is inferred from numeric stepKeys (greater = forward); pass
// `direction={±1}` to override. The exiting layer is pointer-inert and its
// clone lives ≤200ms, so transient duplicate ids/focus are non-issues.
export default function StepSlide({
  stepKey,
  direction,
  duration = 200,
  distance = 16,
  className,
  style,
  children,
}) {
  const reducedMotion = useReducedMotion()
  const containerRef = useRef(null)
  const currentRef = useRef(null)
  const lastHeightRef = useRef(null)
  const prevRef = useRef({ key: stepKey, children })
  const [exiting, setExiting] = useState(null)

  // Render-phase capture of the outgoing step (same latch idea as usePresence).
  let dir = 1
  if (prevRef.current.key !== stepKey) {
    dir = direction ?? ((typeof stepKey === 'number' && typeof prevRef.current.key === 'number' && stepKey < prevRef.current.key) ? -1 : 1)
    if (!reducedMotion) {
      setExiting({ key: prevRef.current.key, children: prevRef.current.children, dir })
    }
    prevRef.current = { key: stepKey, children }
  } else {
    prevRef.current = { key: stepKey, children }
  }
  const dirRef = useRef(1)
  if (exiting) dirRef.current = exiting.dir

  // Enter + height ease, on step change only.
  const firstRef = useRef(true)
  useLayoutEffect(() => {
    const el = currentRef.current
    const box = containerRef.current
    if (firstRef.current) {
      firstRef.current = false
      lastHeightRef.current = el ? el.offsetHeight : null
      return
    }
    if (!el || !box || reducedMotion) {
      lastHeightRef.current = el ? el.offsetHeight : null
      return
    }
    const d = dirRef.current
    const newH = el.offsetHeight
    // Incoming pane: pre-paint offset, play to identity.
    el.style.opacity = '0'
    el.style.transform = `translateX(${distance * d}px)`
    animate(el, { opacity: 1, translateX: '0px', duration, ease: EASE_SPRING })
    // Container height eases old → new, then returns to natural flow.
    if (lastHeightRef.current != null && Math.abs(lastHeightRef.current - newH) > 0.5) {
      box.style.height = `${lastHeightRef.current}px`
      box.style.overflow = 'hidden'
      animate(box, {
        height: `${newH}px`,
        duration,
        ease: EASE_SPRING,
        onComplete: () => {
          box.style.height = ''
          box.style.overflow = ''
        },
      })
    }
    lastHeightRef.current = newH
  }, [stepKey]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div ref={containerRef} className={className} style={{ position: 'relative', ...style }}>
      <div ref={currentRef}>{children}</div>
      {exiting && (
        <ExitingPane
          key={exiting.key}
          dir={exiting.dir}
          duration={duration}
          distance={distance}
          onDone={() => setExiting(null)}
        >
          {exiting.children}
        </ExitingPane>
      )}
    </div>
  )
}

function ExitingPane({ dir, duration, distance, onDone, children }) {
  const ref = useRef(null)
  const onDoneRef = useRef(onDone)
  onDoneRef.current = onDone

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) {
      onDoneRef.current()
      return
    }
    animate(el, {
      opacity: 0,
      translateX: `${-distance * dir}px`,
      duration,
      ease: EASE_SPRING,
      onComplete: () => onDoneRef.current(),
    })
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={ref}
      aria-hidden="true"
      style={{ position: 'absolute', top: 0, left: 0, right: 0, pointerEvents: 'none' }}
    >
      {children}
    </div>
  )
}
