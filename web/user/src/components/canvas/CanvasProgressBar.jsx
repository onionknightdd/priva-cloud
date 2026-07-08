import { useLayoutEffect, useRef } from 'react'
import { animate } from 'animejs'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { useStatusColorTween } from '@shared/motion/useStatusSettle'
import useTaskStore from '../../stores/taskStore'

export default function CanvasProgressBar() {
  const tasks = useTaskStore((s) => s.tasks)
  const todos = useTaskStore((s) => s.todos)
  const reducedMotion = useReducedMotion()
  const fillRef = useRef(null)
  const prevPctRef = useRef(null)

  const taskTotal = Object.keys(tasks).length
  const taskCompleted = Object.values(tasks).filter(
    (t) => t.status === 'success' || t.status === 'completed'
  ).length
  const hasError = Object.values(tasks).some((t) => t.status === 'error')

  const todoTotal = todos ? todos.length : 0
  const todoCompleted = todos ? todos.filter((t) => t.status === 'completed').length : 0

  const total = taskTotal + todoTotal
  const completed = taskCompleted + todoCompleted
  const pct = total === 0 ? 0 : (completed / total) * 100

  // T3: green→red blends over 150ms when a task errors mid-run.
  const fillColor = hasError ? 'var(--red)' : 'var(--green)'
  const colorTweenRef = useStatusColorTween(fillColor, { property: 'backgroundColor' })

  // T8: the fill width is an engine tween (400ms timing preserved) that
  // retargets smoothly when updates land mid-flight; width is written
  // imperatively so re-renders never snap it.
  useLayoutEffect(() => {
    const el = fillRef.current
    if (!el) {
      prevPctRef.current = null
      return
    }
    if (prevPctRef.current === null || reducedMotion) {
      el.style.width = `${pct}%`
      prevPctRef.current = pct
      return
    }
    if (prevPctRef.current === pct) return
    prevPctRef.current = pct
    animate(el, { width: `${pct}%`, duration: 400, ease: 'out(2)' })
  }, [pct, total, reducedMotion])

  if (total === 0) return null

  return (
    <div
      className="flex-shrink-0"
      style={{
        height: 2,
        background: 'var(--bg-elevated)',
      }}
    >
      <div
        ref={(el) => {
          fillRef.current = el
          colorTweenRef.current = el
        }}
        style={{
          height: '100%',
          background: fillColor,
        }}
      />
    </div>
  )
}
