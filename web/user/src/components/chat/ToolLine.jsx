import { useEffect, useId, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate, eases } from 'animejs'
import {
  Check,
  Copy,
  ExternalLink,
  FilePen,
  FilePlus,
  FileText,
  Globe,
  Radio,
  ScrollText,
  Search,
  Send,
  Terminal,
  Wrench,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import { copyTextToClipboard } from '@shared/utils/clipboard'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import useUiStore from '@shared/stores/uiStore'
import useChatStore from '../../stores/chatStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useSidebarStore from '../../stores/sidebarStore'
import useTaskStore from '../../stores/taskStore'
import { tweenScrollIntoView } from '@shared/motion/tweenScroll'
import {
  formatDuration,
  getToolPresentation,
} from '../../utils/toolPresentation'

const TOOL_ICONS = {
  Bash: Terminal,
  Read: FileText,
  Write: FilePlus,
  Edit: FilePen,
  Grep: Search,
  Glob: Search,
  WebFetch: Globe,
  WebSearch: Globe,
  delegate_to_openclaw: Send,
  Monitor: Radio,
  Skill: ScrollText,
}

function ToolIcon({ icon: Icon, running, size }) {
  const sweepRef = useRef(null)
  const innerRef = useRef(null)
  const reduceMotion = useReducedMotion()

  useLayoutEffect(() => {
    const sweep = sweepRef.current
    const inner = innerRef.current
    if (!running || reduceMotion || !sweep || !inner) return undefined

    const progress = { x: -5 }
    const motion = animate(progress, {
      x: size + 5,
      duration: 1600,
      loop: true,
      ease: eases.linear,
      onUpdate: () => {
        sweep.style.transform = `translateX(${progress.x}px)`
        inner.style.transform = `translateX(${-progress.x}px)`
      },
    })
    return () => motion.cancel()
  }, [reduceMotion, running, size])

  if (!running || reduceMotion) {
    return <Icon size={size} strokeWidth={1.5} style={{ color: 'currentColor', flexShrink: 0 }} />
  }

  return (
    <span className="tool-line-icon-shimmer" style={{ width: size, height: size }} aria-hidden="true">
      <Icon size={size} strokeWidth={1.5} className="tool-line-icon-base" />
      <span ref={sweepRef} className="tool-line-icon-sweep" style={{ width: 5, height: size }}>
        <span ref={innerRef} className="tool-line-icon-sweep-inner" style={{ width: size, height: size }}>
          <Icon size={size} strokeWidth={1.5} />
        </span>
      </span>
    </span>
  )
}

function HoverCopyButton({ content, label = 'Copy' }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => () => window.clearTimeout(timerRef.current), [])

  if (!content) return null
  return (
    <button
      type="button"
      className="tool-line-action"
      aria-label={label}
      title={label}
      onClick={async (event) => {
        event.stopPropagation()
        const didCopy = await copyTextToClipboard(content)
        if (!didCopy) return
        setCopied(true)
        window.clearTimeout(timerRef.current)
        timerRef.current = window.setTimeout(() => setCopied(false), 800)
      }}
      style={{ color: copied ? 'var(--green)' : undefined }}
    >
      {copied
        ? <Check size={14} strokeWidth={1.5} />
        : <Copy size={14} strokeWidth={1.5} />}
    </button>
  )
}

function DetailCopyButton({ content, label }) {
  const [copied, setCopied] = useState(false)
  const timerRef = useRef(null)

  useEffect(() => () => window.clearTimeout(timerRef.current), [])

  return (
    <button
      type="button"
      className="tool-detail-copy"
      aria-label={`Copy ${label}`}
      title={`Copy ${label}`}
      onClick={async (event) => {
        event.stopPropagation()
        const didCopy = await copyTextToClipboard(content)
        if (!didCopy) return
        setCopied(true)
        window.clearTimeout(timerRef.current)
        timerRef.current = window.setTimeout(() => setCopied(false), 800)
      }}
      style={{ color: copied ? 'var(--green)' : 'var(--text-dim)' }}
    >
      {copied
        ? <Check size={14} strokeWidth={1.5} />
        : <Copy size={14} strokeWidth={1.5} />}
    </button>
  )
}

function DiffContent({ rows }) {
  return (
    <div className="tool-diff" role="code">
      {rows.map((row, index) => {
        const color = row.kind === 'add'
          ? 'var(--green)'
          : row.kind === 'remove'
            ? 'var(--red)'
            : 'var(--text-dim)'
        return (
          <div className="tool-diff-row" key={`${row.kind}-${row.oldNum}-${row.newNum}-${index}`}>
            <span className="tool-diff-line-number">{row.oldNum ?? ''}</span>
            <span className="tool-diff-line-number">{row.newNum ?? ''}</span>
            <span className="tool-diff-text" style={{ color }}>{row.text || ' '}</span>
          </div>
        )
      })}
    </div>
  )
}

function ToolDetails({ sections, error }) {
  return (
    <div className="tool-detail-block">
      {sections.map((section, index) => (
        <section
          className="tool-detail-section"
          key={`${section.label}-${index}`}
          style={index > 0 ? { borderTop: '1px solid var(--border)' } : undefined}
        >
          <div className="tool-detail-label" style={{ color: error && section.label === 'OUTPUT' ? 'var(--red)' : undefined }}>
            {section.label}
          </div>
          <DetailCopyButton content={section.copyText} label={section.label} />
          {section.type === 'diff' ? (
            <DiffContent rows={section.rows} />
          ) : (
            <pre
              className="tool-detail-code"
              style={{ color: error && section.label === 'OUTPUT' ? 'var(--red)' : undefined }}
            >
              {section.content}
            </pre>
          )}
        </section>
      ))}
    </div>
  )
}

function statusText(t, presentation, reverted) {
  if (reverted) return t('toolCall.status.reverted', { defaultValue: 'reverted' })
  if (presentation.isError) return t('toolCall.status.failed', { defaultValue: 'failed' })
  if (!presentation.stoppedStatus) return ''
  return t(`toolCall.status.${presentation.stoppedStatus}`, {
    defaultValue: presentation.stoppedStatus,
  })
}

export default function ToolLine({
  block,
  op = null,
  kind = null,
  reverted = false,
  compact = false,
  livePreview = false,
}) {
  const { t } = useTranslation()
  const bodyId = useId()
  const sessionId = useChatStore((state) => state.sessionId)
  const cwdDraft = useChatStore((state) => state.cwdDraft)
  const sidebarSessions = useSidebarStore((state) => state.sessions)
  const sidebarActiveCwd = useSidebarStore((state) => state.activeCwd)
  const activeSession = sidebarSessions.find((session) => (
    session.sessionId === sessionId || session.id === sessionId
  ))
  const cwd = activeSession?.cwd || cwdDraft || sidebarActiveCwd || ''
  const presentation = useMemo(
    () => getToolPresentation(block, { op, cwd, kind }),
    [block, cwd, kind, op],
  )
  const sections = useMemo(() => {
    const next = []
    if (presentation.diffRows.length) {
      next.push({
        label: 'DIFF',
        type: 'diff',
        rows: presentation.diffRows,
        copyText: presentation.diffRows.map((row) => row.text).join('\n'),
      })
      if (presentation.isError && presentation.outputText) {
        next.push({ label: 'OUTPUT', type: 'code', content: presentation.outputText, copyText: presentation.outputText })
      }
      return next
    }
    if (presentation.inputText) {
      next.push({ label: 'INPUT', type: 'code', content: presentation.inputText, copyText: presentation.inputText })
    }
    if (presentation.outputText) {
      next.push({ label: 'OUTPUT', type: 'code', content: presentation.outputText, copyText: presentation.outputText })
    }
    return next
  }, [presentation])
  const hasDetails = !livePreview && sections.length > 0
  const [isOpen, setIsOpen] = useState(() => Boolean(presentation.isError && hasDetails))
  const previousErrorRef = useRef(presentation.isError)
  const rowRef = useRef(null)
  const activeTaskId = useTaskStore((state) => state.activeTaskId)
  const setActiveTaskId = useTaskStore((state) => state.setActiveTaskId)
  const showCanvas = useUiStore((state) => state.showCanvas)
  const setActiveCanvasTab = useUiStore((state) => state.setActiveCanvasTab)
  const setSelectedFileOpId = useFileOpsStore((state) => state.setSelectedFileOpId)
  const openFile = useFileBrowserStore((state) => state.openFile)
  const isActive = Boolean(block?.id && activeTaskId === block.id)

  useEffect(() => {
    if (presentation.isError && !previousErrorRef.current && hasDetails) setIsOpen(true)
    previousErrorRef.current = presentation.isError
  }, [hasDetails, presentation.isError])

  useEffect(() => {
    if (isActive && rowRef.current) tweenScrollIntoView(rowRef.current, { block: 'center', flash: false })
  }, [isActive])

  const openExternal = (event) => {
    event.stopPropagation()
    if (!presentation.fullPath) return
    const fileName = presentation.fullPath.split(/[\\/]/).filter(Boolean).at(-1) || presentation.fullPath
    if (['Write', 'Edit'].includes(presentation.displayName) && op?.id) {
      showCanvas()
      setActiveCanvasTab('changes')
      setSelectedFileOpId(op.id)
      return
    }
    openFile({
      filePath: presentation.fullPath,
      name: fileName,
      mimeType: presentation.opLike?.mimeType,
      extension: presentation.opLike?.extension,
      size: presentation.opLike?.size,
      source: presentation.displayName,
    })
    showCanvas()
    setActiveCanvasTab('file-browser')
  }

  const toggleDetails = () => {
    if (!hasDetails) return
    setIsOpen((open) => !open)
    if (block?.id && activeTaskId !== block.id) setActiveTaskId(block.id)
  }

  const Icon = TOOL_ICONS[presentation.displayName]
    || TOOL_ICONS[block?.name]
    || Wrench
  const suffix = statusText(t, presentation, reverted)
  const duration = presentation.isRunning && presentation.startTime
    ? null
    : formatDuration(presentation.duration)
  const rowTitle = [
    presentation.rawName,
    presentation.fullPath || presentation.summary,
    suffix,
  ].filter(Boolean).join(' · ')
  const iconSize = compact ? 11 : 12

  return (
    <div
      ref={rowRef}
      className={`tool-line${compact ? ' tool-line-compact' : ''}${presentation.isRunning ? ' is-running' : ''}${presentation.isError ? ' is-error' : ''}${reverted ? ' is-reverted' : ''}${isActive ? ' is-active' : ''}`}
      data-tool-card
      data-tool-use-id={block?.id}
    >
      <div
        className="tool-line-row"
        role={hasDetails ? 'button' : undefined}
        tabIndex={hasDetails ? 0 : undefined}
        aria-expanded={hasDetails ? isOpen : undefined}
        aria-controls={hasDetails ? bodyId : undefined}
        title={rowTitle}
        onClick={toggleDetails}
        onKeyDown={(event) => {
          if (!hasDetails || (event.key !== 'Enter' && event.key !== ' ')) return
          event.preventDefault()
          toggleDetails()
        }}
        style={{ cursor: hasDetails ? 'pointer' : 'default' }}
      >
        <ToolIcon icon={Icon} running={presentation.isRunning} size={iconSize} />

        <span className={`tool-line-label${presentation.summary ? ' has-summary' : ''}${presentation.isRunning ? ' is-shimmering' : ''}`}>
          {presentation.isRunning ? (
            <span className="tool-line-shimmer-copy">
              <span className="tool-line-name">{presentation.name}</span>
              {presentation.summary && (
                <span className={presentation.summaryIsCode ? 'tool-line-summary is-code' : 'tool-line-summary'}>
                  <span className="tool-line-separator">·</span> {presentation.summary}
                </span>
              )}
            </span>
          ) : (
            <>
              <span className="tool-line-name">{presentation.name}</span>
              {presentation.summary && (
                <span className={presentation.summaryIsCode ? 'tool-line-summary is-code' : 'tool-line-summary'}>
                  <span className="tool-line-separator">·</span> {presentation.summary}
                </span>
              )}
            </>
          )}
        </span>

        {!presentation.isRunning && !presentation.isError && presentation.diffStats.added > 0 && (
          <span className="tool-line-delta" style={{ color: 'var(--green)' }}>+{presentation.diffStats.added}</span>
        )}
        {!presentation.isRunning && !presentation.isError && presentation.diffStats.removed > 0 && (
          <span className="tool-line-delta" style={{ color: 'var(--red)' }}>−{presentation.diffStats.removed}</span>
        )}

        <span className="tool-line-actions">
          <HoverCopyButton content={presentation.copyValue} label="Copy" />
          {presentation.fullPath && (
            <button
              type="button"
              className="tool-line-action"
              aria-label={`Open ${presentation.fullPath}`}
              title={`Open ${presentation.fullPath}`}
              onClick={openExternal}
            >
              <ExternalLink size={14} strokeWidth={1.5} />
            </button>
          )}
        </span>

        {presentation.isRunning && presentation.startTime && (
          <LiveDuration startTime={presentation.startTime} />
        )}
        {duration && <span className="tool-line-duration">· {duration}</span>}
        {suffix && <span className="tool-line-status">· {suffix}</span>}
      </div>

      {hasDetails && (
        <AnimatedCollapse
          open={isOpen}
          id={bodyId}
          animateHeight={false}
          keepMounted
          deferContentOnClose
        >
          {() => (
            <div className="tool-line-details-tree">
              <span aria-hidden="true" className="chat-branch-connector">└─</span>
              <ToolDetails sections={sections} error={presentation.isError} />
            </div>
          )}
        </AnimatedCollapse>
      )}
    </div>
  )
}

function LiveDuration({ startTime }) {
  const [now, setNow] = useState(() => Date.now())

  useEffect(() => {
    const update = () => setNow(Date.now())
    const timer = window.setInterval(update, 100)
    return () => window.clearInterval(timer)
  }, [startTime])

  return <span className="tool-line-duration">· {formatDuration(Math.max(0, now - startTime))}</span>
}
