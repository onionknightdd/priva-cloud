import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate, spring } from 'animejs'
import { useReducedMotion } from '@shared/motion/useReducedMotion'

const DEFAULT_PLACES = [10000, 1000, 100, 10, 1]
const DEFAULT_FONT_FAMILY = "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace"

// framer parity: useSpring({ mass: 0.8, stiffness: 75, damping: 15 }).
// anime clamps spring mass to ≥ 1, so every param is scaled ×1.25 — the spring
// ODE is scale-invariant, damping ratio ζ ≈ 0.968 is preserved exactly.
const SPRING_MASS = 1
const SPRING_STIFFNESS = 93.75
const SPRING_DAMPING = 18.75
// Normalized initial velocity (progress/sec) is |vel/delta|-derived; clamp
// against degenerate tiny-delta retargets.
const MAX_NORM_VELOCITY = 50

function normalizeValue(value) {
  const numeric = Number(value)
  if (!Number.isFinite(numeric)) return 0
  return Math.max(0, Math.floor(numeric))
}

function buildPlaces(value, minDigits = 1) {
  const safeValue = normalizeValue(value)
  const digits = Math.max(String(safeValue).length, minDigits)
  return Array.from({ length: digits }, (_, index) => 10 ** (digits - index - 1))
}

function Digit({ value, place, height, width }) {
  const target = Math.floor(normalizeValue(value) / place)
  const reducedMotion = useReducedMotion()
  const spansRef = useRef([])
  // Persistent per-digit spring state. `v` is the animated quotient (NOT mod
  // 10 — the glyph mapping does the modulo, exactly like the framer version).
  const stRef = useRef(null)
  if (stRef.current === null) {
    stRef.current = { v: target, vel: 0, lastV: target, lastT: 0, height, anim: null }
  }

  useLayoutEffect(() => {
    const st = stRef.current
    st.height = height

    // Modular shortest-path glyph positioning (unchanged formula). Written
    // imperatively — transforms are never declared in JSX style, so React
    // re-renders can't clobber the animator's writes.
    const applyGlyphs = () => {
      const placeValue = ((st.v % 10) + 10) % 10
      for (let number = 0; number < 10; number++) {
        const el = spansRef.current[number]
        if (!el) continue
        const offset = (10 + number - placeValue) % 10
        let y = offset * st.height
        if (offset > 5) y -= 10 * st.height
        el.style.transform = `translateY(${y}px)`
      }
    }

    applyGlyphs() // idempotent: positions glyphs on mount / height change

    if (st.v === target) return // fresh mount, or already settled there

    st.anim?.cancel()

    if (reducedMotion) {
      st.v = target
      st.vel = 0
      applyGlyphs()
      return
    }

    // Velocity continuity across retargets: anime springs are precomputed
    // curves that reset velocity, so we track units/sec ourselves per frame
    // and hand the next spring its normalized (progress/sec) equivalent —
    // this is what framer's useSpring.set() did natively.
    const delta = target - st.v
    let vNorm = st.vel / delta
    if (!Number.isFinite(vNorm)) vNorm = 0
    vNorm = Math.max(-MAX_NORM_VELOCITY, Math.min(MAX_NORM_VELOCITY, vNorm))

    st.lastV = st.v
    st.lastT = performance.now()
    st.anim = animate(st, {
      v: target,
      ease: spring({
        mass: SPRING_MASS,
        stiffness: SPRING_STIFFNESS,
        damping: SPRING_DAMPING,
        velocity: vNorm,
      }),
      onUpdate: () => {
        const now = performance.now()
        const dt = (now - st.lastT) / 1000
        if (dt > 0) {
          st.vel = (st.v - st.lastV) / dt
          st.lastV = st.v
          st.lastT = now
        }
        applyGlyphs()
      },
      onComplete: () => {
        // Anime's spring can finish one frame after the last update callback.
        // Snap the internal value and glyph positions together so the Summary
        // does not visibly pause on a stale final digit.
        st.v = target
        st.vel = 0
        st.lastV = target
        st.lastT = performance.now()
        applyGlyphs()
        st.anim = null
      },
    })
  }, [target, height, reducedMotion])

  // Cancel the in-flight spring on unmount only.
  useEffect(() => () => { stRef.current.anim?.cancel() }, [])

  return (
    <span
      aria-hidden="true"
      style={{
        position: 'relative',
        overflow: 'hidden',
        height,
        width,
        flexShrink: 0,
      }}
    >
      {Array.from({ length: 10 }, (_, number) => (
        <span
          key={number}
          ref={(el) => { spansRef.current[number] = el }}
          style={{
            position: 'absolute',
            inset: 0,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
          }}
        >
          {number}
        </span>
      ))}
    </span>
  )
}

export function RollingInteger({
  value,
  minDigits = 1,
  height = 12,
  width,
  color = 'currentColor',
  fontFamily = DEFAULT_FONT_FAMILY,
  fontSize = height,
  fontWeight = 600,
  verticalAlign = 'middle',
}) {
  const places = useMemo(() => buildPlaces(value, minDigits), [value, minDigits])
  return (
    <Odometer
      value={value}
      places={places}
      height={height}
      width={width}
      color={color}
      fontFamily={fontFamily}
      fontSize={fontSize}
      fontWeight={fontWeight}
      verticalAlign={verticalAlign}
    />
  )
}

export function RollingText({
  text,
  height = 12,
  color = 'currentColor',
  fontFamily = DEFAULT_FONT_FAMILY,
  fontSize = height,
  fontWeight = 600,
  verticalAlign = 'middle',
  whiteSpace = 'pre-wrap',
}) {
  const parts = String(text ?? '').split(/(\d+)/g)
  return (
    <>
      {parts.map((part, index) => {
        if (!part) return null
        if (/^\d+$/.test(part)) {
          return (
            <RollingInteger
              key={`number-${index}`}
              value={Number(part)}
              height={height}
              color={color}
              fontFamily={fontFamily}
              fontSize={fontSize}
              fontWeight={fontWeight}
              verticalAlign={verticalAlign}
            />
          )
        }
        return <span key={`text-${index}`} style={{ whiteSpace }}>{part}</span>
      })}
    </>
  )
}

export default function Odometer({
  value,
  places = DEFAULT_PLACES,
  height = 64,
  width,
  color = 'currentColor',
  fontFamily = DEFAULT_FONT_FAMILY,
  fontSize,
  fontWeight = 600,
  verticalAlign = 'middle',
}) {
  const safeValue = normalizeValue(value)
  const digitWidth = width ?? height * 0.62
  const resolvedFontSize = fontSize ?? height * 0.85

  return (
    <span
      aria-label={String(safeValue)}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        height,
        color,
        fontFamily,
        fontSize: resolvedFontSize,
        fontWeight,
        lineHeight: `${height}px`,
        fontVariantNumeric: 'tabular-nums',
        overflow: 'hidden',
        verticalAlign,
      }}
    >
      {places.map((place) => (
        <Digit
          key={place}
          value={safeValue}
          place={place}
          height={height}
          width={digitWidth}
        />
      ))}
    </span>
  )
}

export function OdometerDemo() {
  const [value, setValue] = useState(0)
  const [auto, setAuto] = useState(false)

  useEffect(() => {
    if (!auto) return undefined
    const id = setInterval(() => {
      setValue((current) => (current + Math.floor(Math.random() * 1400) + 1) % 100000)
    }, 600)
    return () => clearInterval(id)
  }, [auto])

  const buttonStyle = {
    border: '1px solid var(--border)',
    borderRadius: 4,
    background: 'var(--bg-surface)',
    color: 'var(--text-primary)',
    cursor: 'pointer',
    padding: '6px 10px',
    transition: 'background 150ms ease, border-color 150ms ease',
  }

  return (
    <div style={{ display: 'grid', gap: 12, color: 'var(--text-primary)' }}>
      <Odometer value={value} color="var(--orange)" />
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
        <button type="button" style={buttonStyle} onClick={() => setValue(Math.floor(Math.random() * 100000))}>
          Random
        </button>
        <button type="button" style={buttonStyle} onClick={() => setValue((current) => (current + 1) % 100000)}>
          +1
        </button>
        <button type="button" style={buttonStyle} onClick={() => setValue((current) => (current + 1000) % 100000)}>
          +1000
        </button>
        <button type="button" style={buttonStyle} onClick={() => setAuto((current) => !current)}>
          Auto {auto ? 'On' : 'Off'}
        </button>
      </div>
    </div>
  )
}
