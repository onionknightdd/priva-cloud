import { useEffect, useRef, useState } from 'react'
import { Copy } from 'lucide-react'
import { copyTextToClipboard } from '../../utils/clipboard'
import DrawIcon from './DrawIcon'

const COPIED_HOLD_MS = 800
const RESTORE_ERASE_MS = 180
const RESTORE_DRAW_MS = 320
const RESTORE_COPY_DELAY_MS = 70
const RESTORE_TOTAL_MS = RESTORE_COPY_DELAY_MS + RESTORE_DRAW_MS + RESTORE_DRAW_MS * 0.22
const ICON_SIZE = 14

const iconStageStyle = {
  position: 'relative',
  width: ICON_SIZE,
  height: ICON_SIZE,
  display: 'block',
  flexShrink: 0,
}

const iconLayerStyle = {
  position: 'absolute',
  inset: 0,
  display: 'block',
}

export default function CopyButton({ content, inline }) {
  const [phase, setPhase] = useState('idle') // idle | copied | restoring
  const holdTimerRef = useRef(null)
  const restoreTimerRef = useRef(null)

  const clearTimers = () => {
    if (holdTimerRef.current) window.clearTimeout(holdTimerRef.current)
    if (restoreTimerRef.current) window.clearTimeout(restoreTimerRef.current)
    holdTimerRef.current = null
    restoreTimerRef.current = null
  }

  useEffect(() => () => clearTimers(), [])

  const copied = phase === 'copied'

  return (
    <button
      type="button"
      className={inline ? undefined : 'copy-btn'}
      onClick={async () => {
        const didCopy = await copyTextToClipboard(content)
        if (!didCopy) return
        clearTimers()
        setPhase('copied')
        holdTimerRef.current = window.setTimeout(() => {
          setPhase('restoring')
          restoreTimerRef.current = window.setTimeout(() => {
            setPhase('idle')
            restoreTimerRef.current = null
          }, RESTORE_TOTAL_MS)
          holdTimerRef.current = null
        }, COPIED_HOLD_MS)
      }}
      style={{
        position: inline ? 'relative' : 'absolute',
        top: inline ? undefined : 8,
        right: inline ? undefined : 8,
        width: 18,
        height: 18,
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        verticalAlign: 'middle',
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        padding: '2px',
        boxSizing: 'border-box',
        lineHeight: 0,
        overflow: 'hidden',
        color: copied ? 'var(--green)' : 'var(--text-dim)',
        transition: 'color 150ms ease',
        opacity: inline ? 1 : undefined,
      }}
    >
      <span style={iconStageStyle}>
        {phase === 'copied'
          ? (
            <DrawIcon
              key="check-draw"
              name="check"
              size={ICON_SIZE}
              strokeWidth={1.5}
              style={iconLayerStyle}
            />
          )
          : phase === 'restoring'
            ? (
              <>
                <DrawIcon
                  key="check-erase"
                  name="check"
                  size={ICON_SIZE}
                  strokeWidth={1.5}
                  duration={RESTORE_ERASE_MS}
                  mode="erase"
                  style={{ ...iconLayerStyle, color: 'var(--green)' }}
                />
                <DrawIcon
                  key="copy-restore"
                  name="copy"
                  size={ICON_SIZE}
                  strokeWidth={1.5}
                  duration={RESTORE_DRAW_MS}
                  delay={RESTORE_COPY_DELAY_MS}
                  style={{ ...iconLayerStyle, color: 'var(--text-dim)' }}
                />
              </>
            )
            : (
              <Copy
                size={ICON_SIZE}
                strokeWidth={1.5}
                style={{ display: 'block', flexShrink: 0 }}
              />
            )}
      </span>
    </button>
  )
}
