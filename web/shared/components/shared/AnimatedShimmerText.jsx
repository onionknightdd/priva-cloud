import { useLayoutEffect, useRef } from 'react'
import { animate, eases } from 'animejs'
import { useReducedMotion } from '../../motion/useReducedMotion'

const SHIMMER_BACKGROUND = `linear-gradient(
  90deg,
  var(--text-dim) 0%,
  var(--text-dim) 30%,
  var(--text-primary) 45%,
  var(--text-primary) 55%,
  var(--text-dim) 70%,
  var(--text-dim) 100%
)`

export default function AnimatedShimmerText({
  children,
  className,
  style,
  duration = 3000,
}) {
  const ref = useRef(null)
  const reduceMotion = useReducedMotion()

  useLayoutEffect(() => {
    const el = ref.current
    if (!el || reduceMotion) return undefined

    const progress = { x: 200 }
    el.style.backgroundPositionX = `${progress.x}%`

    const animation = animate(progress, {
      x: -200,
      duration,
      loop: true,
      ease: eases.linear,
      onUpdate: () => {
        el.style.backgroundPositionX = `${progress.x}%`
      },
    })

    return () => animation.cancel()
  }, [duration, reduceMotion])

  const shimmerStyle = reduceMotion
    ? {
        background: 'none',
        WebkitTextFillColor: 'currentColor',
      }
    : {
        background: SHIMMER_BACKGROUND,
        backgroundSize: '200% 100%',
        backgroundPositionX: '200%',
        WebkitBackgroundClip: 'text',
        WebkitTextFillColor: 'transparent',
        backgroundClip: 'text',
      }

  return (
    <span
      ref={ref}
      className={className}
      style={{
        display: 'inline-block',
        fontSize: 'var(--text-sm)',
        fontWeight: 500,
        ...shimmerStyle,
        ...style,
      }}
    >
      {children}
    </span>
  )
}
