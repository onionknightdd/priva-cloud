import { animate } from 'animejs'
import { EASE_IN_OUT, EASE_OUT } from './tokens'

// Approved M14 + I7 spec: programmatic scrolls become ONE retargeted
// deterministic tween per container (~300ms focus jumps / ≤120ms keyboard
// steps), cancelled instantly by user wheel/touch input; focus jumps can end
// with a one-shot 300ms background sweep on the target row.

const activeScrolls = new WeakMap() // container → { anim, cleanup }

function prefersReducedMotion() {
  return typeof window !== 'undefined'
    && typeof window.matchMedia === 'function'
    && window.matchMedia('(prefers-reduced-motion: reduce)').matches
}

export function tweenScroll(container, targetTop, { duration = 300 } = {}) {
  if (!container) return
  const max = Math.max(0, container.scrollHeight - container.clientHeight)
  const to = Math.max(0, Math.min(targetTop, max))
  const prev = activeScrolls.get(container)
  prev?.anim?.cancel()
  prev?.cleanup?.()
  if (prefersReducedMotion()) {
    container.scrollTop = to
    return
  }
  // Animate a proxy — scrollTop is a raw element property, not a style.
  const st = { v: container.scrollTop }
  let entry
  const cleanup = () => {
    container.removeEventListener('wheel', onUserScroll)
    container.removeEventListener('touchmove', onUserScroll)
    if (activeScrolls.get(container) === entry) activeScrolls.delete(container)
  }
  // The user's own scroll wins instantly.
  const onUserScroll = () => {
    entry.anim.cancel()
    cleanup()
  }
  const anim = animate(st, {
    v: to,
    duration,
    ease: EASE_IN_OUT,
    onUpdate: () => { container.scrollTop = st.v },
    onComplete: cleanup,
  })
  entry = { anim, cleanup }
  container.addEventListener('wheel', onUserScroll, { passive: true })
  container.addEventListener('touchmove', onUserScroll, { passive: true })
  activeScrolls.set(container, entry)
}

export function getScrollParent(el) {
  let p = el?.parentElement
  while (p) {
    const s = getComputedStyle(p)
    if (/(auto|scroll)/.test(s.overflowY)) return p
    p = p.parentElement
  }
  return document.scrollingElement || document.documentElement
}

// Deterministic replacement for el.scrollIntoView({ behavior: 'smooth' }).
export function tweenScrollIntoView(el, { block = 'center', duration = 300, flash = false } = {}) {
  if (!el) return
  const container = getScrollParent(el)
  const cr = container.getBoundingClientRect()
  const er = el.getBoundingClientRect()
  let target = container.scrollTop + (er.top - cr.top)
  if (block === 'center') target -= (container.clientHeight - er.height) / 2
  tweenScroll(container, target, { duration })
  if (flash) flashRow(el)
}

// One-shot arrival flash: bg sweeps var(--bg-elevated) → transparent, 300ms.
export function flashRow(el, { duration = 300 } = {}) {
  if (!el || prefersReducedMotion()) return
  const doc = el.ownerDocument.documentElement
  const raw = getComputedStyle(doc).getPropertyValue('--bg-elevated').trim()
  const m = /^#([0-9a-f]{6})$/i.exec(raw)
  if (!m) return
  const r = parseInt(m[1].slice(0, 2), 16)
  const g = parseInt(m[1].slice(2, 4), 16)
  const b = parseInt(m[1].slice(4, 6), 16)
  const original = el.style.backgroundColor
  el.style.backgroundColor = `rgba(${r}, ${g}, ${b}, 1)`
  animate(el, {
    backgroundColor: `rgba(${r}, ${g}, ${b}, 0)`,
    duration,
    ease: EASE_OUT,
    onComplete: () => { el.style.backgroundColor = original },
  })
}

export default tweenScroll
