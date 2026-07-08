import { animate, svg, utils, stagger } from 'animejs'

// One-shot stroke trace for an SVG diagram (approved T4 spec): every stroked
// path/line/polyline draws tip-to-tail with a small stagger, total ≤500ms.
// Call once on first data-ready mount (guard with a ref); never on re-polls.
// Callers must skip entirely under reduced motion.
export function traceSvgStrokes(rootOrEls, { duration = 300, stepMs = 40, selector = 'path, line, polyline' } = {}) {
  if (!rootOrEls) return null
  const els = Array.isArray(rootOrEls)
    ? rootOrEls.filter(Boolean)
    : Array.from(rootOrEls.querySelectorAll(selector))
  if (!els.length) return null
  // Cap the stagger so long edge lists still finish inside the 500ms budget.
  const step = Math.min(stepMs, Math.max(1, Math.floor((500 - duration) / els.length)))
  const drawables = svg.createDrawable(els)
  utils.set(drawables, { draw: '0 0' })
  return animate(drawables, {
    draw: '0 1',
    duration,
    delay: stagger(step),
    ease: 'out(2)',
  })
}

export default traceSvgStrokes
