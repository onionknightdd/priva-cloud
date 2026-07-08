import { useLayoutEffect, useRef } from 'react'
import { animate, svg, utils } from 'animejs'
import { useReducedMotion } from '../../motion/useReducedMotion'
import { EASE_OUT } from '../../motion/tokens'

// One-shot stroke-draw icon (approved spec: draw only, no scale). On mount the
// stroke draws tip-to-tail over ~300ms, then it is a plain currentColor icon —
// drop-in visual match for lucide's <Check/> / <X/> at strokeWidth 1.5.
//
//   {copied ? <DrawIcon name="check" size={14} /> : <Copy size={14} …/>}
//
// Reduced motion: renders the finished icon, no animation.
const PATHS = {
  check: ['M4 12l5 5L20 6'],
  copy: [
    'M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2',
    'M10 8h8c1.1 0 2 .9 2 2v8c0 1.1-.9 2-2 2h-8c-1.1 0-2-.9-2-2v-8c0-1.1.9-2 2-2Z',
  ],
  x: ['M18 6 6 18', 'M6 6l12 12'],
}

export default function DrawIcon({
  name = 'check',
  size = 14,
  strokeWidth = 1.5,
  className,
  style,
  duration = 300,
  delay = 0,
  mode = 'draw',
  // Pass false to render the finished icon statically — e.g. a status chip
  // that mounted already-resolved (history, virtualizer recycle) instead of
  // resolving live.
  draw = true,
}) {
  const rootRef = useRef(null)
  const reducedMotion = useReducedMotion()
  const reducedRef = useRef(reducedMotion)
  reducedRef.current = reducedMotion
  const drawRef = useRef(draw) // mount-time decision

  useLayoutEffect(() => {
    if (!drawRef.current || reducedRef.current) return undefined
    const paths = rootRef.current?.querySelectorAll('path')
    if (!paths || paths.length === 0) return undefined
    const drawables = svg.createDrawable(paths)
    const erasing = mode === 'erase'
    // Pre-paint hidden/full stroke (utils.set is synchronous), then draw or erase it.
    utils.set(drawables, { draw: erasing ? '0 1' : '0 0' })
    const step = drawables.length > 1 ? duration * 0.22 : 0
    const anim = animate(drawables, {
      draw: erasing ? '1 1' : '0 1',
      duration,
      ease: EASE_OUT,
      delay: step || delay ? (_, i) => delay + i * step : 0,
    })
    return () => anim.cancel()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  const d = PATHS[name] || PATHS.check
  return (
    <svg
      ref={rootRef}
      className={className}
      style={{ display: 'block', flexShrink: 0, ...style }}
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={strokeWidth}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {d.map((path) => <path key={path} d={path} />)}
    </svg>
  )
}
