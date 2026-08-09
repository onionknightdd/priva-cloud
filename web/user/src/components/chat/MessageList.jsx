import { useRef, useEffect, useState, useCallback, useMemo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { RotateCcw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import useUiStore from '@shared/stores/uiStore'
import useSidebarStore from '../../stores/sidebarStore'
import MessageBubble from './MessageBubble'
import CompactBoundary from './CompactBoundary'
import TaskNotificationCard from './TaskNotificationCard'
import JumpToLatest from './JumpToLatest'
import { useSSE } from '../../hooks/useSSE'
import { rewindFiles, forkSession } from '../../api/sessions'
import { openSession } from '../../session/openSession'

// The centered reading column, replicated per virtual row so layout matches the
// pre-virtualization single-column wrapper exactly.
const ROW_COLUMN_STYLE = {
  width: 'auto',
  maxWidth: 'none',
  marginLeft: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
  marginRight: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
  transition: 'margin-left var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1)), margin-right var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
}
const SUMMARY_AWARE_STAGE_STYLE = {
  width: 'calc(100% - var(--session-summary-layout-width, 0px))',
  minWidth: 0,
  transition: 'width var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
}

export default function MessageList() {
  const { t } = useTranslation()
  const messages = useChatStore((s) => s.messages)
  const sessionId = useChatStore((s) => s.sessionId)
  const cwdDraft = useChatStore((s) => s.cwdDraft)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const showRecapBoundaryFade = useChatStore((s) => Boolean(s.recap) && !s.recapDismissed && !s.isStreaming)
  const enableFileCheckpointing = useChatStore((s) => s.enableFileCheckpointing)
  const findCheckpointForAssistant = useChatStore((s) => s.findCheckpointForAssistant)
  const rewindMarker = useChatStore((s) => s.rewindMarker)
  const setRewindMarker = useChatStore((s) => s.setRewindMarker)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const sidebarSessions = useSidebarStore((s) => s.sessions)
  const sidebarActiveCwd = useSidebarStore((s) => s.activeCwd)
  const refreshSessions = useSidebarStore((s) => s.fetchSessions)
  const { sendAnswer } = useSSE()
  const bottomRef = useRef(null)
  const containerRef = useRef(null)
  const mountedCountRef = useRef(messages.length)
  const lastSessionRef = useRef(sessionId)
  const isNearBottomRef = useRef(true)
  const scrollFrameRef = useRef(null)
  const initialPinFrameRef = useRef(null)
  const initialPinSessionRef = useRef(null)
  const initialPinningRef = useRef(false)
  const activeSession = sidebarSessions.find((session) => (
    session.sessionId === sessionId || session.id === sessionId
  ))
  const filePreviewCwd = activeSession?.cwd || cwdDraft || sidebarActiveCwd || ''

  // Reset mounted count when conversation changes
  if (sessionId !== lastSessionRef.current) {
    lastSessionRef.current = sessionId
    mountedCountRef.current = messages.length
  }
  const [showJump, setShowJump] = useState(false)

  const prefersReducedMotion = useCallback(() => (
    typeof window !== 'undefined' &&
    window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
  ), [])

  const handleRewind = useCallback(async (idx) => {
    const uuid = findCheckpointForAssistant(idx)
    if (!uuid) {
      showConfirmDialog({
        title: t('confirm.noCheckpointTitle'),
        message: t('confirm.noCheckpointMessage'),
        confirmLabel: t('confirm.ok'),
      })
      return
    }
    if (!sessionId) return
    showConfirmDialog({
      title: t('confirm.rewindTitle'),
      message: t('confirm.rewindMessage'),
      requireText: 'rewind',
      danger: true,
      confirmLabel: t('confirm.rewindConfirm'),
      onConfirm: async () => {
        try {
          await rewindFiles(sessionId, uuid)
          const msgs = useChatStore.getState().messages
          const targetIdx = msgs.findIndex((m) => m.uuid === uuid)
          const revertedIds = []
          if (targetIdx >= 0) {
            const fileWriters = new Set(['Write', 'Edit', 'MultiEdit', 'NotebookEdit'])
            for (let i = targetIdx; i < msgs.length; i++) {
              const content = msgs[i]?.content
              if (!Array.isArray(content)) continue
              for (const block of content) {
                if (block?.type === 'tool_use' && fileWriters.has(block.name) && block.id) {
                  revertedIds.push(block.id)
                } else if (block?.type === 'file_ref' && block.fileOpId) {
                  revertedIds.push(block.fileOpId)
                }
              }
            }
          }
          setRewindMarker({
            checkpointUuid: uuid,
            rewindTs: Date.now(),
            revertedToolUseIds: Array.from(new Set(revertedIds)),
          })
        } catch (e) {
          showConfirmDialog({
            title: t('confirm.rewindFailed'),
            message: String(e?.message || e),
            confirmLabel: t('confirm.ok'),
          })
        }
      },
    })
  }, [findCheckpointForAssistant, sessionId, showConfirmDialog, setRewindMarker])

  const handleFork = useCallback(async (idx) => {
    if (!sessionId) return
    const assistantMsg = messages[idx]
    // Best-effort: fork up to the user message preceding this assistant message
    const targetUuid = findCheckpointForAssistant(idx) || assistantMsg?.uuid || null
    showConfirmDialog({
      title: t('confirm.forkTitle'),
      message: t('confirm.forkMessage'),
      confirmLabel: t('confirm.forkConfirm'),
      onConfirm: async () => {
        try {
          const { new_session_id } = await forkSession(sessionId, targetUuid)
          // The fork is a NEW session — openSession hydrates it into its own
          // runtime and swaps to it; the original stays retained (and keeps
          // streaming if it was live).
          await openSession(new_session_id, { forkParentId: sessionId })
          refreshSessions()
          setTimeout(() => document.querySelector('.chat-textarea')?.focus(), 0)
        } catch (e) {
          showConfirmDialog({
            title: t('confirm.forkFailed'),
            message: String(e?.message || e),
            confirmLabel: t('confirm.ok'),
          })
        }
      },
    })
  }, [sessionId, messages, findCheckpointForAssistant, refreshSessions, showConfirmDialog, t])

  const revertedIdSet = useMemo(
    () => new Set(rewindMarker?.revertedToolUseIds || []),
    [rewindMarker]
  )

  // Separate system compact messages from chat messages to keep stable indices for MessageBubble
  const renderItems = useMemo(() => {
    const chatMessages = []
    const compactInserts = [] // { beforeIndex, msg }
    for (let i = 0; i < messages.length; i++) {
      if (messages[i].role === 'system' && messages[i].type === 'compact') {
        compactInserts.push({ beforeIndex: chatMessages.length, msg: messages[i] })
      } else {
        chatMessages.push({ msg: messages[i], originalIndex: i })
      }
    }

    const lastAssistantIndex = [...chatMessages].reverse().findIndex((e) => e.msg.role === 'assistant')
    const latestAssistantChatIndex = lastAssistantIndex === -1
      ? -1
      : chatMessages.length - 1 - lastAssistantIndex
    const lastUserIndex = [...chatMessages].reverse().findIndex((e) => e.msg.role === 'user')
    const activeUserChatIndex = lastUserIndex === -1
      ? -1
      : chatMessages.length - 1 - lastUserIndex

    // Build render list: interleave compact boundaries at correct positions
    const items = []
    let compactPtr = 0
    for (let ci = 0; ci <= chatMessages.length; ci++) {
      while (compactPtr < compactInserts.length && compactInserts[compactPtr].beforeIndex === ci) {
        const compactMsg = compactInserts[compactPtr].msg
        items.push({ type: 'compact', msg: compactMsg, key: `compact-${compactMsg.timestamp}` })
        compactPtr++
      }
      if (ci < chatMessages.length) {
        const entry = chatMessages[ci]
        if (rewindMarker && entry.msg.uuid && entry.msg.uuid === rewindMarker.checkpointUuid) {
          items.push({ type: 'rewind_divider', rewindTs: rewindMarker.rewindTs, key: `rewind-${rewindMarker.rewindTs}` })
        }
        items.push({
          type: 'chat',
          key: entry.msg._cid || `idx-${ci}`,
          msg: entry.msg,
          chatIndex: ci,
          originalIndex: entry.originalIndex,
          isLastAssistant: isStreaming && entry.msg.role === 'assistant' && ci === chatMessages.length - 1,
          isLatestAssistantMessage: entry.msg.role === 'assistant' && ci === latestAssistantChatIndex,
          responseStreaming: isStreaming && ci > activeUserChatIndex,
        })
      }
    }
    return items
  }, [messages, isStreaming, rewindMarker])

  // Above-viewport re-measures (async images/diagrams) are compensated by the
  // virtualizer's default scroll adjustment, so the reading position holds.
  const virtualizer = useVirtualizer({
    count: renderItems.length,
    getScrollElement: () => containerRef.current,
    estimateSize: () => 120,
    overscan: 6,
    paddingStart: 24,
    getItemKey: (index) => renderItems[index].key,
    // Chat semantics: stay glued to the live edge. anchorTo 'end' compensates
    // item resizes while at the bottom (the streaming tail) and follows rows
    // appended while there; the threshold matches the 80px near-bottom gate.
    anchorTo: 'end',
    followOnAppend: true,
    scrollEndThreshold: 80,
  })

  const totalSize = virtualizer.getTotalSize()

  // Scroll to the live edge THROUGH the virtualizer, never raw el.scrollTo():
  // scrollToEnd keeps the internal scroll offset in sync (so first-measurement
  // adjustments can't tug the jump back from a stale base) and its reconcile
  // loop retries until estimated row sizes settle at the true bottom.
  const scrollToBottom = useCallback((behavior = 'smooth') => {
    const el = containerRef.current
    if (!el) {
      bottomRef.current?.scrollIntoView({ behavior: 'auto' })
      return
    }
    const requestedBehavior = behavior === 'auto' || behavior === 'smooth' ? behavior : 'smooth'
    const distance = el.scrollHeight - el.scrollTop - el.clientHeight
    // Smooth only for short hops — animating across thousands of estimated
    // pixels fights measurement and looks janky anyway.
    const useSmooth = !prefersReducedMotion()
      && requestedBehavior === 'smooth'
      && distance < el.clientHeight * 2
    virtualizer.scrollToEnd({ behavior: useSmooth ? 'smooth' : 'auto' })
  }, [virtualizer, prefersReducedMotion])

  const cancelInitialBottomPin = useCallback(() => {
    initialPinningRef.current = false
    if (initialPinFrameRef.current != null) {
      window.cancelAnimationFrame(initialPinFrameRef.current)
      initialPinFrameRef.current = null
    }
  }, [])

  const runInitialBottomPin = useCallback(() => {
    cancelInitialBottomPin()
    initialPinningRef.current = true
    isNearBottomRef.current = true
    setShowJump(false)

    const deadline = performance.now() + 1600
    let lastTotal = -1
    let stableFrames = 0

    const tick = () => {
      if (!initialPinningRef.current) return
      const el = containerRef.current
      if (el) {
        virtualizer.scrollToEnd({ behavior: 'auto' })

        const distance = Math.max(0, el.scrollHeight - el.scrollTop - el.clientHeight)
        const nextTotal = virtualizer.getTotalSize()
        const totalStable = Math.abs(nextTotal - lastTotal) < 1
        stableFrames = distance <= 2 && totalStable ? stableFrames + 1 : 0
        lastTotal = nextTotal

        isNearBottomRef.current = true
        setShowJump(false)

        if (stableFrames >= 4 || performance.now() >= deadline) {
          initialPinningRef.current = false
          initialPinFrameRef.current = null
          return
        }
      }

      initialPinFrameRef.current = window.requestAnimationFrame(tick)
    }

    initialPinFrameRef.current = window.requestAnimationFrame(tick)
  }, [cancelInitialBottomPin, virtualizer])

  const scheduleScrollToBottom = useCallback((behavior = 'smooth') => {
    if (scrollFrameRef.current != null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
    }
    scrollFrameRef.current = window.requestAnimationFrame(() => {
      scrollFrameRef.current = null
      scrollToBottom(behavior)
    })
  }, [scrollToBottom])

  // Auto-scroll only while the viewer is already near the live edge. During
  // streaming we pin instantly to avoid repeated smooth-scroll animations.
  useEffect(() => {
    if (!isNearBottomRef.current) return
    scheduleScrollToBottom(isStreaming ? 'auto' : 'smooth')
  }, [messages, isStreaming, scheduleScrollToBottom])

  // Opening or switching a session always lands at the live edge — the
  // previous session's scroll position and near-bottom state must not leak in.
  useEffect(() => {
    isNearBottomRef.current = true
    scheduleScrollToBottom('auto')
  }, [sessionId, scheduleScrollToBottom])

  // A long recovered transcript starts with estimated row heights. Keep the
  // viewport pinned until the virtualizer has measured the real tail rows.
  useEffect(() => {
    if (!sessionId || renderItems.length === 0) return
    if (initialPinSessionRef.current === sessionId) return
    initialPinSessionRef.current = sessionId
    runInitialBottomPin()
  }, [sessionId, renderItems.length, runInitialBottomPin])

  // Measurement can land behind content growth (images finishing near the
  // bottom) — re-pin whenever the measured height changes while near the edge.
  useEffect(() => {
    if (!isNearBottomRef.current) return
    scheduleScrollToBottom('auto')
  }, [totalSize, scheduleScrollToBottom])

  // Track scroll position
  useEffect(() => {
    const el = containerRef.current
    if (!el) return
    const onScroll = () => {
      if (initialPinningRef.current) {
        isNearBottomRef.current = true
        setShowJump(false)
        return
      }
      const threshold = 80
      const isNearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < threshold
      isNearBottomRef.current = isNearBottom
      setShowJump(!isNearBottom)
    }
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [])

  useEffect(() => {
    const el = containerRef.current
    if (!el) return undefined
    const cancelOnUserIntent = () => {
      if (initialPinningRef.current) cancelInitialBottomPin()
    }
    el.addEventListener('wheel', cancelOnUserIntent, { passive: true })
    el.addEventListener('touchstart', cancelOnUserIntent, { passive: true })
    el.addEventListener('pointerdown', cancelOnUserIntent)
    return () => {
      el.removeEventListener('wheel', cancelOnUserIntent)
      el.removeEventListener('touchstart', cancelOnUserIntent)
      el.removeEventListener('pointerdown', cancelOnUserIntent)
    }
  }, [cancelInitialBottomPin])

  useEffect(() => () => {
    if (scrollFrameRef.current != null) {
      window.cancelAnimationFrame(scrollFrameRef.current)
    }
    cancelInitialBottomPin()
  }, [cancelInitialBottomPin])

  // Empty state handled by ChatPanel
  if (messages.length === 0) return null

  return (
    <div className="flex-1 relative overflow-hidden" style={{ background: 'var(--bg-base)' }}>
      <div
        ref={containerRef}
        className="absolute inset-0 overflow-y-auto overflow-x-hidden"
        // Native scroll anchoring double-compensates against the virtualizer's
        // own scroll adjustments and can strand the viewport mid-transcript.
        style={{ overflowAnchor: 'none' }}
      >
        <div style={{ height: totalSize, position: 'relative', width: '100%' }}>
          {virtualizer.getVirtualItems().map((vi) => {
            const item = renderItems[vi.index]
            let row
            if (item.type === 'compact') {
              row = (
                <div className="overflow-hidden chat-message-in" style={ROW_COLUMN_STYLE}>
                  <CompactBoundary message={item.msg} />
                </div>
              )
            } else if (item.type === 'rewind_divider') {
              row = (
                <div className="overflow-hidden" style={ROW_COLUMN_STYLE}>
                  <RewindDivider rewindTs={item.rewindTs} />
                </div>
              )
            } else if (item.msg.role === 'system' && item.msg.type === 'task_notification') {
              // CLI-injected <task-notification> (a background Workflow / Bash
              // finished and re-invoked the model) → slim system card, not a
              // raw-XML user bubble. The summary streams as the next assistant turn.
              const animClass = item.originalIndex >= mountedCountRef.current ? ' chat-message-in' : ''
              row = (
                <div className={`overflow-hidden${animClass}`} style={ROW_COLUMN_STYLE}>
                  <TaskNotificationCard notif={item.msg.notif} />
                </div>
              )
            } else {
              // The entry animation lives on this inner div: the outer wrapper's
              // transform is the virtualizer's positioner and must stay untouched.
              const animClass = item.originalIndex >= mountedCountRef.current ? ' chat-message-in' : ''
              row = (
                <div
                  className={animClass.trim()}
                  style={{
                    ...ROW_COLUMN_STYLE,
                    overflow: item.msg.role === 'user' ? 'visible' : 'hidden',
                  }}
                >
                  <MessageBubble
                    message={item.msg}
                    isStreaming={item.isLastAssistant}
                    responseStreaming={item.responseStreaming}
                    isLatestAssistantMessage={item.isLatestAssistantMessage}
                    latestAssistantRefreshKey={messages.length}
                    filePreviewCwd={filePreviewCwd}
                    onSendAnswer={sendAnswer}
                    assistantIndex={item.originalIndex}
                    onRewind={handleRewind}
                    onFork={handleFork}
                    showCheckpointActions={enableFileCheckpointing && !!sessionId}
                    revertedToolUseIds={revertedIdSet}
                  />
                </div>
              )
            }
            return (
              <div
                key={vi.key}
                data-index={vi.index}
                ref={virtualizer.measureElement}
                // `top` positioning (not transform) so rows create no stacking
                // context — popovers/toolbars inside bubbles keep painting above
                // later rows, exactly as in the unvirtualized flow.
                style={{
                  position: 'absolute',
                  top: vi.start,
                  left: 0,
                  width: '100%',
                }}
              >
                <div style={SUMMARY_AWARE_STAGE_STYLE}>
                  {row}
                </div>
              </div>
            )
          })}
        </div>
        <div ref={bottomRef} />
      </div>

      {showRecapBoundaryFade && (
        /* Soften the boundary between the scrolling transcript and the pinned
           recap area without taking up layout space. */
        <div
          aria-hidden="true"
          style={{
            position: 'absolute',
            left: 0,
            right: 'var(--session-summary-layout-width, 0px)',
            bottom: 0,
            height: 24,
            background: 'linear-gradient(to bottom, transparent, var(--bg-base))',
            pointerEvents: 'none',
            transition: 'right var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
            zIndex: 10,
          }}
        />
      )}

      {/* Jump to latest */}
      {showJump && (
        <div
          style={{
            position: 'absolute',
            left: 0,
            right: 'var(--session-summary-layout-width, 0px)',
            bottom: 12,
            height: 0,
            pointerEvents: 'none',
            transition: 'right var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
            zIndex: 50,
          }}
        >
          <JumpToLatest
            onClick={() => {
              // An explicit jump means "follow the live edge again" — flag it so the
              // re-pin effect finishes the landing once row measurements settle.
              isNearBottomRef.current = true
              scrollToBottom('smooth')
            }}
            style={{
              position: 'absolute',
              bottom: 0,
              left: '50%',
              transform: 'translateX(-50%)',
              pointerEvents: 'auto',
            }}
          />
        </div>
      )}

    </div>
  )
}

function fmtTime(ts) {
  if (!ts) return ''
  const d = new Date(ts)
  const hh = String(d.getHours()).padStart(2, '0')
  const mm = String(d.getMinutes()).padStart(2, '0')
  return `${hh}:${mm}`
}

function RewindDivider({ rewindTs }) {
  const { t } = useTranslation()
  return (
    <div className="flex items-center gap-3 my-2 px-4">
      <div className="flex-1" style={{ borderTop: '1px dashed var(--border-strong)' }} />
      <span
        className="text-xs font-bold uppercase flex items-center gap-1"
        style={{ color: 'var(--purple)', letterSpacing: '0.06em', whiteSpace: 'nowrap' }}
      >
        <RotateCcw size={12} strokeWidth={1.5} />
        {t('rewind.divider', { time: fmtTime(rewindTs) })}
      </span>
      <div className="flex-1" style={{ borderTop: '1px dashed var(--border-strong)' }} />
    </div>
  )
}
