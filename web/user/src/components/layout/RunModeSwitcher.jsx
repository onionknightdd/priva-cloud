import { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { animate } from 'animejs'
import { CircleQuestionMark, CodeXml, MessageSquareShare, X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { DURATION, EASE_TAB } from '@shared/motion/tokens'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { useFlipPosition } from '@shared/motion/useFlipPosition'
import { useSlidingUnderline } from '@shared/motion/useSlidingUnderline'
import useChatStore from '../../stores/chatStore'
import useSettingsStore from '../../stores/settingsStore'
import { effectiveRunMode, isRunModeLocked } from '../../utils/runMode'
import MarkdownRenderer from '../markdown/MarkdownRenderer'
import useOverlayTransition from '@shared/motion/useOverlayTransition'

function RunModeHelpDialog({ open, onClose, triggerRef }) {
  const { t } = useTranslation()
  const closeRef = useRef(null)
  const wasOpenRef = useRef(false)
  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'scale' })

  useEffect(() => {
    if (!open) return undefined
    const frame = window.requestAnimationFrame(() => closeRef.current?.focus())
    const onKeyDown = (event) => {
      if (event.key === 'Escape') {
        event.preventDefault()
        onClose()
        return
      }
      if (event.key !== 'Tab' || !panelRef.current) return
      const focusable = [...panelRef.current.querySelectorAll(
        'button:not([disabled]), [href], input:not([disabled]), [tabindex]:not([tabindex="-1"])',
      )]
      if (focusable.length === 0) return
      const first = focusable[0]
      const last = focusable[focusable.length - 1]
      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault()
        last.focus()
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault()
        first.focus()
      }
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      window.cancelAnimationFrame(frame)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [onClose, open, panelRef])

  useEffect(() => {
    if (wasOpenRef.current && !open) triggerRef.current?.focus()
    wasOpenRef.current = open
  }, [open, triggerRef])

  if (!mounted) return null
  const modal = (
    <div
      ref={backdropRef}
      className="fixed inset-0 flex items-center justify-center"
      style={{
        zIndex: 1200,
        padding: 16,
        background: 'var(--bg-overlay)',
        backdropFilter: 'blur(4px)',
      }}
      onMouseDown={(event) => { if (event.target === event.currentTarget) onClose() }}
    >
      <section
        ref={panelRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="run-mode-help-title"
        className="flex flex-col min-w-0 overflow-hidden"
        style={{
          width: 'min(720px, calc(100vw - 32px))',
          maxHeight: 'calc(100vh - 32px)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-strong)',
          borderRadius: 4,
        }}
      >
        <header className="flex items-center justify-between gap-3 px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <div className="flex items-center gap-2 min-w-0">
            <CircleQuestionMark size={16} strokeWidth={1.5} />
            <h2 id="run-mode-help-title" className="font-semibold truncate" style={{ margin: 0, color: 'var(--text-primary)', fontSize: 14 }}>
              {t('sidebar.runModeHelpTitle')}
            </h2>
          </div>
          <button
            ref={closeRef}
            type="button"
            onClick={onClose}
            aria-label={t('sidebar.runModeHelpClose')}
            className="run-mode-help-close inline-flex items-center justify-center flex-shrink-0"
            style={{
              width: 28,
              height: 28,
              background: 'transparent',
              border: 'none',
              color: 'var(--text-dim)',
              cursor: 'pointer',
              transition: 'color 150ms ease, background 150ms ease',
            }}
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </header>
        <div className="px-4 py-4 overflow-y-auto min-w-0" style={{ color: 'var(--text-secondary)' }}>
          <MarkdownRenderer
            content={`${t('sidebar.runModeHelpMarkdown')}\n\n${t('sidebar.runModeHelpTokenNote')}`}
            streaming={false}
            resolveInlineFiles={false}
          />
        </div>
      </section>
    </div>
  )
  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}

export default function RunModeSwitcher() {
  const { t } = useTranslation()
  const [helpOpen, setHelpOpen] = useState(false)
  const helpButtonRef = useRef(null)
  const activeIconRef = useRef(null)
  const iconAnimationRef = useRef(null)
  const agentLabelRef = useRef(null)
  const codeLabelRef = useRef(null)
  const chatRunMode = useChatStore((state) => state.runMode)
  const runModeLocked = useChatStore((state) => state.runModeLocked)
  const sessionId = useChatStore((state) => state.sessionId)
  const setRunMode = useChatStore((state) => state.setRunMode)
  const draftRunMode = useSettingsStore((state) => state.draftRunMode)
  const setDraftRunMode = useSettingsStore((state) => state.setDraftRunMode)
  const activeMode = effectiveRunMode({ runMode: chatRunMode }, draftRunMode)
  const locked = isRunModeLocked({ runModeLocked, sessionId })
  const previousModeRef = useRef(activeMode)
  const reducedMotion = useReducedMotion()
  const selectionMotion = useSlidingUnderline(activeMode)
  useFlipPosition(agentLabelRef, { duration: DURATION.canvas, ease: EASE_TAB, disabled: reducedMotion })
  useFlipPosition(codeLabelRef, { duration: DURATION.canvas, ease: EASE_TAB, disabled: reducedMotion })

  useLayoutEffect(() => {
    const previousMode = previousModeRef.current
    previousModeRef.current = activeMode
    const icon = activeIconRef.current

    iconAnimationRef.current?.cancel()
    iconAnimationRef.current = null
    if (!icon) return undefined

    const settle = () => {
      icon.style.opacity = ''
      icon.style.transform = ''
    }
    if (previousMode === activeMode || reducedMotion) {
      settle()
      return undefined
    }

    icon.style.opacity = '0'
    icon.style.transform = `translateX(${activeMode === 'agent' ? -6 : 6}px)`
    iconAnimationRef.current = animate(icon, {
      opacity: 1,
      translateX: '0px',
      duration: DURATION.canvas,
      ease: EASE_TAB,
      onComplete: () => {
        settle()
        iconAnimationRef.current = null
      },
    })

    return () => {
      iconAnimationRef.current?.cancel()
      iconAnimationRef.current = null
    }
  }, [activeMode, reducedMotion])

  const selectMode = (mode) => {
    if (locked || mode === activeMode) return
    setRunMode(mode)
    setDraftRunMode(mode)
  }

  return (
    <>
      <div
        className="run-mode-switcher flex items-center gap-2 px-4 py-2 flex-shrink-0 min-w-0"
      >
        <div
          role="tablist"
          aria-label={t('sidebar.runModeLabel')}
          aria-describedby={locked ? 'run-mode-locked-hint' : undefined}
          className="run-mode-tabs relative inline-flex items-center flex-shrink-0 min-w-0"
          style={{
            height: 34,
            padding: 3,
            background: 'var(--bg-elevated)',
            border: 'none',
            borderRadius: 10,
            opacity: locked ? 0.55 : 1,
            transition: 'opacity 150ms ease',
          }}
        >
          <div
            ref={selectionMotion.indicatorRef}
            aria-hidden="true"
            style={{
              position: 'absolute',
              top: 3,
              bottom: 3,
              background: 'var(--bg-surface)',
              borderRadius: 8,
              pointerEvents: 'none',
              zIndex: 0,
              opacity: 0,
            }}
          />
          {['agent', 'code'].map((mode) => {
            const active = activeMode === mode
            return (
              <button
                key={mode}
                type="button"
                role="tab"
                aria-selected={active}
                disabled={locked}
                ref={selectionMotion.setItemRef(mode)}
                onClick={() => selectMode(mode)}
                className="run-mode-tab inline-flex items-center justify-center gap-2 h-full px-2 font-normal whitespace-nowrap"
                style={{
                  position: 'relative',
                  width: 80,
                  minWidth: 80,
                  fontSize: 14,
                  background: 'transparent',
                  border: 'none',
                  color: locked ? 'var(--text-dim)' : 'var(--text-primary)',
                  WebkitTextFillColor: locked ? 'var(--text-dim)' : 'var(--text-primary)',
                  opacity: 1,
                  zIndex: 1,
                  cursor: locked ? 'default' : 'pointer',
                  transition: 'color 150ms ease',
                }}
                title={locked ? t('sidebar.runModeLocked') : undefined}
              >
                {active && (
                  <span ref={activeIconRef} className="inline-flex items-center justify-center flex-shrink-0" aria-hidden="true">
                    {mode === 'agent'
                      ? <MessageSquareShare size={15} strokeWidth={1.5} />
                      : <CodeXml size={15} strokeWidth={1.5} />}
                  </span>
                )}
                <span ref={mode === 'agent' ? agentLabelRef : codeLabelRef} className="inline-block">
                  {mode === 'agent' ? t('sidebar.runModeAgent') : t('sidebar.runModeCode')}
                </span>
              </button>
            )
          })}
        </div>
        <span id="run-mode-locked-hint" className="hidden">{locked ? t('sidebar.runModeLocked') : ''}</span>
        <button
          ref={helpButtonRef}
          type="button"
          aria-label={t('sidebar.runModeHelpLabel')}
          aria-haspopup="dialog"
          aria-expanded={helpOpen}
          title={t('sidebar.runModeHelpLabel')}
          onClick={() => setHelpOpen(true)}
          className="run-mode-help-button inline-flex items-center justify-center flex-shrink-0"
          style={{
            width: 28,
            height: 28,
            background: 'transparent',
            border: '1px solid transparent',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            transition: 'color 150ms ease, background 150ms ease, border-color 150ms ease',
          }}
        >
          <CircleQuestionMark size={14} strokeWidth={1.5} />
        </button>
      </div>
      <RunModeHelpDialog open={helpOpen} onClose={() => setHelpOpen(false)} triggerRef={helpButtonRef} />
    </>
  )
}
