import { useEffect, useLayoutEffect, useReducer, useRef } from 'react'
import { animate } from 'animejs'
import useReducedMotion from './useReducedMotion'
import { DURATION, EASE_SPRING, EASE_TAB } from './tokens'

// Approved M10 + I4 spec: list items get a real enter (fade + 8px rise) AND a
// real exit (fade + height→0, removed from the DOM when done) — the height
// collapse is what makes surviving neighbors slide smoothly into place.
//
//   const [lifecycleItems, removeExited] = useListLifecycle(toasts, (t) => t.id)
//   {lifecycleItems.map(({ key, item, present }) => (
//     <LifecycleItem key={key} present={present} onExited={() => removeExited(key)}>
//       <Toast … />
//     </LifecycleItem>
//   ))}
//
// Items removed from the source array are retained (in their old position)
// with present=false until their exit finishes. Rapid re-add of the same key
// mid-exit hands the element back to the enter animation seamlessly.

export function useListLifecycle(items, getKey) {
  const [, force] = useReducer((c) => c + 1, 0)
  const exitingRef = useRef(new Map()) // key → snapshotted item
  const orderRef = useRef([]) // render order of keys, from last render
  const lastItemsRef = useRef(new Map())

  const itemsByKey = new Map()
  for (const item of items) itemsByKey.set(getKey(item), item)

  // Newly-missing keys move to the exiting set (render-phase ref bookkeeping,
  // same latch idea as usePresence). Keys leaving in the same render get
  // consecutive exitIndex values so consumers can stagger a batch removal.
  let exitBatch = 0
  for (const k of orderRef.current) {
    if (!itemsByKey.has(k) && !exitingRef.current.has(k) && lastItemsRef.current.has(k)) {
      exitingRef.current.set(k, { item: lastItemsRef.current.get(k), exitIndex: exitBatch })
      exitBatch += 1
    }
  }
  // A re-added key stops exiting.
  for (const k of itemsByKey.keys()) exitingRef.current.delete(k)

  const out = []
  const seen = new Set()
  for (const k of orderRef.current) {
    if (seen.has(k)) continue
    if (itemsByKey.has(k)) {
      out.push({ key: k, item: itemsByKey.get(k), present: true, exitIndex: 0 })
      seen.add(k)
    } else if (exitingRef.current.has(k)) {
      const ex = exitingRef.current.get(k)
      out.push({ key: k, item: ex.item, present: false, exitIndex: ex.exitIndex })
      seen.add(k)
    }
  }
  for (const item of items) {
    const k = getKey(item)
    if (!seen.has(k)) {
      out.push({ key: k, item, present: true, exitIndex: 0 })
      seen.add(k)
    }
  }

  orderRef.current = out.map((o) => o.key)
  lastItemsRef.current = itemsByKey

  const removeExited = (key) => {
    if (exitingRef.current.delete(key)) force()
  }

  return [out, removeExited]
}

// The animated shell for one list item. Owns overflow so the height collapse
// clips cleanly; put per-item spacing INSIDE (or via marginBottom on this
// shell, passed through style — it collapses along with the height).
export function LifecycleItem({
  present,
  onExited,
  duration = DURATION.panel,
  // Exits are deliberately shorter with a standard curve — the entrance
  // spring's long deceleration tail reads as lag on a disappearing element.
  exitDuration = 160,
  rise = 8,
  axis = 'y', // 'y' = rise up into place, 'x' = slide in from the right
  collapse = 'height', // 'width' for items in a horizontal row (chips)
  enterCollapse = false,
  enterOnMount = true,
  exitDelay = 0,
  className,
  style,
  children,
}) {
  const ref = useRef(null)
  const enteredRef = useRef(!enterOnMount)
  const reducedMotion = useReducedMotion()
  const presentRef = useRef(present)
  presentRef.current = present
  const onExitedRef = useRef(onExited)
  onExitedRef.current = onExited

  // Purge on unmount-while-exiting (e.g. a parent collapse removed the whole
  // row mid-exit) — otherwise the lifecycle map re-emits ghosts next mount.
  useEffect(() => () => {
    if (!presentRef.current) onExitedRef.current()
  }, [])

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) {
      if (!present) onExited()
      return
    }
    const dim = collapse === 'width' ? 'width' : 'height'
    if (present) {
      const fresh = !enteredRef.current
      enteredRef.current = true
      if (reducedMotion) {
        // Undo any in-flight exit styles from a rapid re-add.
        el.style.opacity = ''
        el.style[dim] = ''
        return
      }
      if (fresh) {
        const translateProp = axis === 'x' ? 'translateX' : 'translateY'
        el.style.opacity = '0'
        el.style.transform = `${translateProp}(${rise}px)`
        if (enterCollapse) {
          const natural = dim === 'width' ? el.scrollWidth : el.scrollHeight
          el.style[dim] = '0px'
          animate(el, {
            opacity: 1,
            [dim]: `${natural}px`,
            [translateProp]: '0px',
            duration,
            ease: EASE_SPRING,
            onComplete: () => { if (presentRef.current) el.style[dim] = '' },
          })
        } else {
          animate(el, { opacity: 1, [translateProp]: '0px', duration, ease: EASE_SPRING })
        }
      } else if (el.style[dim] && el.style[dim] !== 'auto') {
        // Re-added mid-exit: grow back from wherever the collapse got to.
        const natural = dim === 'width' ? el.scrollWidth : el.scrollHeight
        animate(el, {
          opacity: 1,
          [dim]: `${natural}px`,
          duration: exitDuration,
          ease: EASE_TAB,
          onComplete: () => { el.style[dim] = '' },
        })
      }
    } else {
      if (reducedMotion) {
        onExited()
        return
      }
      const current = dim === 'width' ? el.offsetWidth : el.offsetHeight
      el.style[dim] = `${current}px` // fix auto → px for the collapse
      animate(el, {
        opacity: 0,
        [dim]: '0px',
        duration: exitDuration,
        delay: exitDelay,
        ease: EASE_TAB,
        onComplete: () => { if (!presentRef.current) onExited() },
      })
    }
  }, [present]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div
      ref={ref}
      className={className}
      style={{
        overflow: 'hidden',
        ...(collapse === 'width' ? { minWidth: 0, flexShrink: 0 } : { minHeight: 0 }),
        ...style,
      }}
    >
      {children}
    </div>
  )
}

export default LifecycleItem
