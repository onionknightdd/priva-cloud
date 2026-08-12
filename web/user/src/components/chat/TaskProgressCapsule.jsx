import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate } from 'animejs'
import { Circle, CircleCheck, CircleX, LoaderCircle } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import { EASE_OUT } from '@shared/motion/tokens'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import useTaskStore from '../../stores/taskStore'
import {
  getSdkTaskComposerTasks,
  getSdkTaskComposerRoundIds,
  getSdkTaskDisplayStatus,
  getSdkTaskProgress,
  isSdkTaskDone,
} from '../../utils/sdkTaskTracker'

function TaskStatusIcon({ status, size = 16 }) {
  if (status === 'in_progress') {
    return (
      <LoaderCircle
        className="icon-running"
        size={size}
        strokeWidth={1.5}
        style={{ color: 'var(--blue)', flexShrink: 0 }}
      />
    )
  }
  if (status === 'completed' || status === 'deleted') {
    return (
      <CircleCheck
        size={size}
        strokeWidth={1.5}
        style={{ color: status === 'completed' ? 'var(--green)' : 'var(--text-dim)', flexShrink: 0 }}
      />
    )
  }
  if (status === 'blocked') {
    return <CircleX size={size} strokeWidth={1.5} style={{ color: 'var(--red)', flexShrink: 0 }} />
  }
  return <Circle size={size} strokeWidth={1.5} style={{ color: 'var(--text-secondary)', flexShrink: 0 }} />
}

function HoverMarquee({ children }) {
  const viewportRef = useRef(null)
  const textRef = useRef(null)
  const animationRef = useRef(null)
  const [overflow, setOverflow] = useState(0)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const text = textRef.current
    if (!viewport || !text) return undefined

    const measure = () => {
      const nextOverflow = Math.max(0, text.scrollWidth - viewport.clientWidth)
      setOverflow(nextOverflow)
      if (nextOverflow === 0) {
        animationRef.current?.cancel()
        animationRef.current = null
        text.style.transform = 'translateX(0px)'
      }
    }
    measure()

    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    observer.observe(text)
    return () => observer.disconnect()
  }, [children])

  useEffect(() => () => animationRef.current?.cancel(), [])

  const start = () => {
    const text = textRef.current
    if (!text || overflow <= 0 || reducedMotion) return
    animationRef.current?.cancel()
    const distance = Math.max(1, overflow)
    animationRef.current = animate(text, {
      translateX: -distance,
      duration: Math.max(1400, distance * 24),
      ease: 'linear',
      loop: true,
      loopDelay: 3000,
    })
  }

  const reset = () => {
    const text = textRef.current
    if (!text) return
    animationRef.current?.cancel()
    animationRef.current = null
    if (reducedMotion) {
      text.style.transform = 'translateX(0px)'
      return
    }
    animationRef.current = animate(text, {
      translateX: 0,
      duration: 220,
      ease: EASE_OUT,
      onComplete: () => { animationRef.current = null },
    })
  }

  return (
    <span
      ref={viewportRef}
      onMouseEnter={start}
      onMouseLeave={reset}
      style={{
        display: 'block',
        minWidth: 0,
        overflow: 'hidden',
        whiteSpace: 'nowrap',
      }}
    >
      <span
        ref={textRef}
        style={{
          display: 'inline-block',
          minWidth: 'max-content',
          willChange: overflow > 0 ? 'transform' : 'auto',
        }}
      >
        {children}
      </span>
    </span>
  )
}

function currentTaskStep(tasks) {
  if (tasks.length === 0) return 0
  const runningIndex = tasks.findIndex((task) => getSdkTaskDisplayStatus(task) === 'in_progress')
  if (runningIndex >= 0) return runningIndex + 1
  const openIndex = tasks.findIndex((task) => !isSdkTaskDone(task))
  return openIndex >= 0 ? openIndex + 1 : tasks.length
}

export default function TaskProgressCapsule() {
  const { t } = useTranslation()
  const tracker = useTaskStore((state) => state.sdkTaskTracker)
  const [expanded, setExpanded] = useState(false)
  const tasks = useMemo(() => getSdkTaskComposerTasks(tracker), [tracker])
  const progress = useMemo(() => getSdkTaskProgress(tasks), [tasks])
  const step = useMemo(() => currentTaskStep(tasks), [tasks])
  const visible = getSdkTaskComposerRoundIds(tracker).length > 0
  const progressStatus = tasks.length > 0
    ? getSdkTaskDisplayStatus(tasks[Math.max(0, step - 1)])
    : 'pending'

  useEffect(() => {
    if (!visible) setExpanded(false)
  }, [visible])

  if (!visible) return null

  return (
    <div
      className="flex flex-col items-center"
      style={{
        width: '100%',
        minWidth: 0,
        marginBottom: 8,
        boxSizing: 'border-box',
        pointerEvents: 'none',
      }}
    >
      <AnimatedCollapse
        open={expanded}
        style={{
          width: 'fit-content',
          minWidth: 'min(330px, calc(100% - 32px))',
          maxWidth: 'min(360px, calc(100% - 32px))',
        }}
        innerStyle={{ width: '100%' }}
      >
        {() => (
          <div
            style={{
              width: '100%',
              maxHeight: 264,
              marginBottom: 8,
              padding: '9px 14px',
              boxSizing: 'border-box',
              overflowY: 'auto',
              overflowX: 'hidden',
              background: 'var(--bg-surface)',
              border: '1px solid var(--border)',
              borderRadius: 14,
              pointerEvents: 'auto',
            }}
          >
            {tasks.length > 0 ? (
              <div className="flex flex-col" style={{ gap: 7, minWidth: 0 }}>
                {tasks.map((task, index) => {
                  const status = getSdkTaskDisplayStatus(task)
                  const subject = task?.subject || t('chat.taskProgressUntitled')
                  return (
                    <div
                      key={task?._key || task?.id || index}
                      className="flex items-center min-w-0"
                      style={{ gap: 9, color: 'var(--text-secondary)' }}
                    >
                      <TaskStatusIcon status={status} />
                      <div
                        className="min-w-0"
                        style={{
                          flex: 1,
                          minWidth: 0,
                          fontFamily: 'var(--font-ui)',
                          fontSize: 14,
                          fontWeight: 400,
                          lineHeight: '20px',
                        }}
                      >
                        <HoverMarquee>
                          {t('chat.taskProgressTask', { index: index + 1, subject })}
                        </HoverMarquee>
                      </div>
                    </div>
                  )
                })}
              </div>
            ) : (
              <div
                style={{
                  color: 'var(--text-dim)',
                  fontFamily: 'var(--font-ui)',
                  fontSize: 13,
                  lineHeight: '20px',
                }}
              >
                {t('chat.taskProgressEmpty')}
              </div>
            )}
          </div>
        )}
      </AnimatedCollapse>

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        aria-label={expanded ? t('chat.taskProgressCollapse') : t('chat.taskProgressExpand')}
        className="inline-flex items-center justify-center"
        style={{
          minWidth: 0,
          height: 32,
          gap: 7,
          padding: '0 12px',
          boxSizing: 'border-box',
          background: expanded ? 'var(--bg-elevated)' : 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 16,
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontFamily: 'var(--font-ui)',
          fontSize: 12,
          fontWeight: 400,
          lineHeight: '20px',
          transition: 'background 150ms ease, border-color 150ms ease, color 150ms ease',
          pointerEvents: 'auto',
        }}
        onMouseEnter={(event) => {
          event.currentTarget.style.background = 'var(--bg-elevated)'
          event.currentTarget.style.borderColor = 'var(--border-strong)'
          event.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(event) => {
          event.currentTarget.style.background = expanded ? 'var(--bg-elevated)' : 'var(--bg-surface)'
          event.currentTarget.style.borderColor = 'var(--border)'
          event.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        <TaskStatusIcon
          status={progress.done === progress.total && progress.total > 0 ? 'completed' : progressStatus}
          size={14}
        />
        <span>{t('chat.taskProgressStep', { current: step, total: progress.total })}</span>
      </button>
    </div>
  )
}
