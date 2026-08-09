import { useEffect, useId, useMemo, useState } from 'react'
import {
  Bot,
  Check,
  ChevronRight,
  FileDiff,
  FilePen,
  FileText,
  FileX2,
  FolderTree,
  Image as ImageIcon,
  Loader,
  Paperclip,
  RotateCcw,
  X,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import useOverlayTransition from '@shared/motion/useOverlayTransition'
import useChatStore from '../../stores/chatStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useTaskStore from '../../stores/taskStore'
import useUiStore from '@shared/stores/uiStore'
import ImageLightbox from '../shared/ImageLightbox'
import { toProjectRelativePath } from '../../utils/toolPresentation'
import {
  fileOpLineStats,
  summarizeCanvasChanges,
  uniqueConversationAgents,
  uniqueCanvasFiles,
  uniqueConversationSources,
} from '../../utils/sessionSummary'

export const SESSION_SUMMARY_RAIL_WIDTH = 320
export const SESSION_SUMMARY_OVERLAY_WIDTH = `min(${SESSION_SUMMARY_RAIL_WIDTH}px, 45%)`
// The content stage ends at the card's left edge. Keep the card width stable
// while its right edge follows the header's canvas shortcut; the fallback
// preserves the original 12px inset before measuring.
export const SESSION_SUMMARY_LAYOUT_WIDTH = `calc(${SESSION_SUMMARY_OVERLAY_WIDTH} - 24px + var(--session-summary-card-right-inset, 12px))`
export const SESSION_SUMMARY_ENTER_DURATION = 260
export const SESSION_SUMMARY_EXIT_DURATION = 300

function CountSummary({ count, label, added = 0, removed = 0, showFileCount = true, fontSize = 11 }) {
  const showCount = showFileCount && count > 0
  if (!showCount && added <= 0 && removed <= 0) return null

  return (
    <span
      className="inline-flex items-center gap-1 flex-shrink-0"
      style={{
        color: 'var(--text-secondary)',
        fontFamily: 'var(--font-code)',
        fontSize,
        lineHeight: '16px',
        fontVariantNumeric: 'tabular-nums',
      }}
    >
      {showCount && <span>{count}</span>}
      {showCount && <span style={{ color: 'var(--text-dim)', fontFamily: 'var(--font-ui)' }}>{label}</span>}
      {added > 0 && <span style={{ color: 'var(--green)' }}>+{added}</span>}
      {removed > 0 && <span style={{ color: 'var(--red)' }}>-{removed}</span>}
    </span>
  )
}

function SummarySection({ icon: Icon, title, open, onOpen, onToggle, summary, children }) {
  const bodyId = useId()
  const [hovered, setHovered] = useState(false)

  return (
    <section style={{ minWidth: 0 }}>
      <div
        onMouseEnter={() => setHovered(true)}
        onMouseLeave={() => setHovered(false)}
        className="flex items-center w-full min-w-0"
        style={{
          height: 38,
          background: hovered ? 'var(--bg-elevated)' : 'transparent',
          color: 'var(--text-primary)',
          transition: 'background 150ms ease, color 150ms ease',
        }}
      >
        <button
          type="button"
          onClick={onOpen}
          className="flex items-center gap-2 flex-1 min-w-0"
          style={{
            alignSelf: 'stretch',
            padding: '0 4px 0 12px',
            background: 'transparent',
            border: 'none',
            color: 'inherit',
            cursor: 'pointer',
            textAlign: 'left',
          }}
        >
          <Icon size={13} strokeWidth={1.5} style={{ color: 'var(--text-primary)', flexShrink: 0 }} />
          <span className="flex-1 truncate font-normal" style={{ fontSize: 13 }}>{title}</span>
          {summary}
        </button>
        <button
          type="button"
          aria-expanded={open}
          aria-controls={bodyId}
          onClick={onToggle}
          className="inline-flex items-center justify-center flex-shrink-0"
          style={{
            alignSelf: 'stretch',
            width: 32,
            padding: 0,
            background: 'transparent',
            border: 'none',
            color: 'var(--text-primary)',
            cursor: 'pointer',
          }}
        >
          <AnimatedChevron
            open={open}
            style={{ color: 'var(--text-primary)', transform: `rotate(${open ? 90 : 0}deg)` }}
          >
            <ChevronRight size={13} strokeWidth={1.5} />
          </AnimatedChevron>
        </button>
      </div>
      <AnimatedCollapse open={open} id={bodyId}>
        {children}
      </AnimatedCollapse>
    </section>
  )
}

function GroupLabel({ children }) {
  return (
    <div
      className="flex items-center font-semibold"
      style={{
        height: 34,
        padding: '0 12px',
        color: 'var(--text-dim)',
        fontSize: 13,
      }}
    >
      {children}
    </div>
  )
}

function GroupDivider() {
  return (
    <div
      aria-hidden="true"
      style={{ height: 1, margin: '8px 12px 0', background: 'var(--border-subtle)' }}
    />
  )
}

function EmptySection({ children }) {
  return (
    <div
      style={{
        padding: '12px',
        color: 'var(--text-dim)',
        fontSize: 12,
        textAlign: 'center',
      }}
    >
      {children}
    </div>
  )
}

function CanvasFileRow({ file, cwd, deletedLabel, onOpen }) {
  const missing = file.missing === true
  const Icon = missing ? FileX2 : FileText
  const path = toProjectRelativePath(file.filePath, cwd, file.relativePath || file.relative_path)
    || file.name
    || file.filePath

  return (
    <button
      type="button"
      onClick={() => onOpen(file)}
      className="flex items-center gap-2 min-w-0"
      title={file.filePath}
      style={{
        minHeight: 34,
        padding: '5px 10px',
        borderLeft: `2px solid ${missing ? 'var(--red)' : 'var(--status-idle)'}`,
        borderTop: '1px solid var(--border-subtle)',
        borderRight: 'none',
        borderBottom: 'none',
        color: missing ? 'var(--red)' : 'var(--text-secondary)',
        background: 'transparent',
        cursor: 'pointer',
        textAlign: 'left',
        width: '100%',
        transition: 'background 150ms ease',
      }}
      onMouseEnter={(event) => { event.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(event) => { event.currentTarget.style.background = 'transparent' }}
    >
      <Icon size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
      <div
        className="flex-1 min-w-0 truncate"
        style={{
          color: missing ? 'var(--text-dim)' : 'var(--text-primary)',
          fontFamily: 'var(--font-code)',
          fontSize: 11,
          lineHeight: '15px',
          textDecoration: missing ? 'line-through' : 'none',
          textDecorationThickness: missing ? '1px' : undefined,
        }}
      >
        {path}
      </div>
      {missing && (
        <span
          className="uppercase flex-shrink-0"
          style={{ color: 'var(--red)', fontSize: 10, fontWeight: 600, letterSpacing: '0.06em' }}
        >
          {deletedLabel}
        </span>
      )}
    </button>
  )
}

function SourceRow({ source, onOpen }) {
  const Icon = source.kind === 'image' ? ImageIcon : Paperclip
  const clickable = !!source.path || (source.kind === 'image' && !!source.src)
  const content = (
    <>
      <Icon size={14} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
      <span
        className="truncate"
        style={{ fontFamily: 'var(--font-code)', fontSize: 11, lineHeight: '15px' }}
      >
        {source.label}
      </span>
    </>
  )

  if (!clickable) {
    return (
      <div
        className="flex items-center gap-2 min-w-0"
        title={source.path || source.label}
        style={{ minHeight: 32, padding: '4px 12px', color: 'var(--text-secondary)' }}
      >
        {content}
      </div>
    )
  }

  return (
    <button
      type="button"
      className="flex items-center gap-2 min-w-0 w-full"
      title={source.label}
      onClick={() => onOpen(source)}
      style={{
        minHeight: 32,
        padding: '4px 12px',
        background: 'transparent',
        border: 'none',
        color: 'var(--text-secondary)',
        cursor: 'pointer',
        textAlign: 'left',
        transition: 'background 150ms ease, color 150ms ease',
      }}
      onMouseEnter={(event) => {
        event.currentTarget.style.background = 'var(--bg-elevated)'
        event.currentTarget.style.color = 'var(--text-primary)'
      }}
      onMouseLeave={(event) => {
        event.currentTarget.style.background = 'transparent'
        event.currentTarget.style.color = 'var(--text-secondary)'
      }}
    >
      {content}
    </button>
  )
}

function AgentSummaryRow({ agents, onOpen, onOpenAgent }) {
  const { t } = useTranslation()
  const [hovered, setHovered] = useState(false)
  const count = agents.length
  const visibleCount = Math.min(count, 5)
  const overflowCount = Math.max(0, count - visibleCount)

  return (
    <div
      className="flex items-center gap-2 min-w-0"
      style={{
        minHeight: 38,
        padding: '4px 12px',
        color: 'var(--text-primary)',
        background: hovered ? 'var(--bg-elevated)' : 'transparent',
        transition: 'background 150ms ease',
      }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {visibleCount > 0 && (
        <span className="inline-flex items-center flex-shrink-0">
          {agents.slice(0, visibleCount).map((agent, index) => (
            <button
              key={agent.id || index}
              type="button"
              onClick={() => onOpenAgent(agent)}
              className="inline-flex items-center justify-center flex-shrink-0"
              style={{
                width: 17,
                height: 17,
                marginLeft: index === 0 ? 0 : -5,
                background: 'var(--bg-surface)',
                border: 'none',
                padding: 0,
                outline: '1px solid var(--bg-surface)',
                position: 'relative',
                zIndex: index + 1,
                color: 'var(--text-primary)',
                cursor: 'pointer',
              }}
            >
              <Bot size={17} strokeWidth={1.5} />
            </button>
          ))}
          {overflowCount > 0 && (
            <button
              type="button"
              onClick={onOpen}
              style={{
                marginLeft: 4,
                padding: 0,
                background: 'transparent',
                border: 'none',
                fontSize: 13,
                color: 'var(--text-secondary)',
                cursor: 'pointer',
              }}
            >
              ... +{overflowCount}
            </button>
          )}
        </span>
      )}
      <button
        type="button"
        onClick={onOpen}
        className="flex-1 min-w-0 truncate font-normal"
        style={{
          padding: 0,
          background: 'transparent',
          border: 'none',
          color: 'inherit',
          cursor: 'pointer',
          fontSize: 13,
          textAlign: 'left',
        }}
      >
        {t('chat.sessionSummary.agentsRun', { count })}
      </button>
    </div>
  )
}

function OperationStatusIcon({ status, reverted }) {
  if (reverted) return <RotateCcw size={12} strokeWidth={1.5} />
  if (status === 'running' || status === 'pending') {
    return <Loader size={12} strokeWidth={1.5} className="icon-running" />
  }
  if (status === 'error') return <X size={12} strokeWidth={1.5} />
  return <Check size={12} strokeWidth={1.5} />
}

function operationAccent(status, reverted) {
  if (reverted) return 'var(--purple)'
  if (status === 'running' || status === 'pending') return 'var(--purple)'
  if (status === 'error') return 'var(--red)'
  return 'var(--green)'
}

function ChangeOperationRow({ op, cwd, reverted, revertedLabel, onOpen }) {
  const type = String(op.type || '').toLowerCase()
  const isWrite = type === 'write'
  const Icon = isWrite ? FileText : FilePen
  const stats = fileOpLineStats(op)
  const accent = operationAccent(op.status, reverted)
  const path = toProjectRelativePath(op.filePath, cwd, op.relativePath) || op.filePath || '(untitled)'

  return (
    <button
      type="button"
      onClick={() => onOpen(op)}
      className="flex items-center gap-2 min-w-0"
      title={op.filePath}
      style={{
        minHeight: 36,
        padding: '5px 10px',
        borderLeft: `2px solid ${accent}`,
        borderTop: '1px solid var(--border-subtle)',
        borderRight: 'none',
        borderBottom: 'none',
        background: 'transparent',
        color: 'var(--text-primary)',
        cursor: 'pointer',
        opacity: reverted ? 0.6 : 1,
        textAlign: 'left',
        width: '100%',
        transition: 'background 150ms ease',
      }}
      onMouseEnter={(event) => { event.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(event) => { event.currentTarget.style.background = 'transparent' }}
    >
      <Icon size={14} strokeWidth={1.5} style={{ color: isWrite ? 'var(--cyan)' : 'var(--orange)', flexShrink: 0 }} />
      <div className="flex items-center gap-1 flex-1 min-w-0 overflow-hidden">
        <div
          className="truncate"
          style={{ color: 'var(--text-secondary)', fontFamily: 'var(--font-code)', fontSize: 11, lineHeight: '15px' }}
        >
          {path}
        </div>
        {reverted && (
          <span
            className="uppercase truncate flex-shrink-0"
            style={{ color: 'var(--purple)', fontSize: 10, fontWeight: 600, letterSpacing: '0.06em' }}
          >
            {revertedLabel}
          </span>
        )}
      </div>
      <div
        className="inline-flex items-center gap-1 flex-shrink-0"
        style={{ color: accent, fontFamily: 'var(--font-code)', fontSize: 10, fontVariantNumeric: 'tabular-nums' }}
      >
        {stats.added > 0 && <span style={{ color: 'var(--green)' }}>+{stats.added}</span>}
        {stats.removed > 0 && <span style={{ color: 'var(--red)' }}>-{stats.removed}</span>}
        <OperationStatusIcon status={op.status} reverted={reverted} />
      </div>
    </button>
  )
}

function SessionSummaryCard({ open, cardId, cwd, topOffset = 0, onSourceOpen }) {
  const { t } = useTranslation()
  const sessionId = useChatStore((state) => state.sessionId)
  const rewindMarker = useChatStore((state) => state.rewindMarker)
  const messages = useChatStore((state) => state.messages)
  const subagentContent = useChatStore((state) => state.subagentContent)
  const tabs = useFileBrowserStore((state) => state.tabs)
  const openFile = useFileBrowserStore((state) => state.openFile)
  const fileOps = useFileOpsStore((state) => state.fileOps)
  const setSelectedFileOpId = useFileOpsStore((state) => state.setSelectedFileOpId)
  const focusSubagent = useTaskStore((state) => state.focusSubagent)
  const openCanvasTab = useUiStore((state) => state.openCanvasTab)
  const [filesOpen, setFilesOpen] = useState(false)
  const [changesOpen, setChangesOpen] = useState(false)
  const { mounted, panelRef } = useOverlayTransition({
    open,
    variant: 'cornerScale',
    duration: SESSION_SUMMARY_ENTER_DURATION,
    exitDuration: SESSION_SUMMARY_EXIT_DURATION,
  })

  const files = useMemo(() => uniqueCanvasFiles(tabs), [tabs])
  const sources = useMemo(() => uniqueConversationSources(messages), [messages])
  const agents = useMemo(
    () => uniqueConversationAgents(messages, subagentContent),
    [messages, subagentContent],
  )
  const revertedIds = rewindMarker?.revertedToolUseIds || []
  const revertedSet = useMemo(() => new Set(revertedIds), [revertedIds])
  const changes = useMemo(
    () => summarizeCanvasChanges(fileOps, revertedIds),
    [fileOps, revertedIds],
  )

  const openFilesCanvas = () => openCanvasTab('file-browser')
  const openChangesCanvas = () => openCanvasTab('changes')
  const openTasksCanvas = () => openCanvasTab('tasks')
  const openCanvasFile = (file) => {
    openFile(file)
    openFilesCanvas()
  }
  const openChange = (op) => {
    setSelectedFileOpId(op.id)
    openChangesCanvas()
  }
  const openAgent = (agent) => {
    if (agent?.id) focusSubagent(agent.id)
    openTasksCanvas()
  }
  const openSource = (source) => {
    if (source.path) {
      openFile({
        filePath: source.path,
        name: source.label,
        source: 'conversation-source',
      })
      openFilesCanvas()
      return
    }
    onSourceOpen(source)
  }

  useEffect(() => {
    setFilesOpen(false)
    setChangesOpen(false)
  }, [sessionId])

  if (!mounted) return null

  return (
    <div
      id={cardId}
      ref={panelRef}
      role="region"
      aria-label={t('chat.sessionSummary.title')}
      style={{
        position: 'absolute',
        top: topOffset + 12,
        left: 'calc(24px - var(--session-summary-card-right-inset, 12px))',
        right: 'var(--session-summary-card-right-inset, 12px)',
        maxHeight: `calc(100% - ${topOffset + 24}px)`,
        display: 'flex',
        flexDirection: 'column',
        minWidth: 0,
        overflow: 'hidden',
        background: 'var(--bg-surface)',
        border: '0.5px solid var(--border-subtle)',
        borderRadius: 12,
        boxShadow: '0 2px 6px color-mix(in srgb, var(--text-primary) 8%, transparent)',
        color: 'var(--text-primary)',
        transformOrigin: 'top right',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      <div className="overflow-y-auto overflow-x-hidden" style={{ minHeight: 0 }}>
        <GroupLabel>{t('chat.sessionSummary.title')}</GroupLabel>
        <SummarySection
          icon={FolderTree}
          title={t('chat.sessionSummary.files')}
          open={filesOpen}
          onOpen={openFilesCanvas}
          onToggle={() => setFilesOpen((value) => !value)}
          summary={(
            <CountSummary
              count={files.length}
              label={t('chat.sessionSummary.fileUnit', { count: files.length })}
            />
          )}
        >
          {files.length > 0 ? files.map((file) => (
            <CanvasFileRow
              key={file.filePath}
              file={file}
              cwd={cwd}
              deletedLabel={t('chat.sessionSummary.deleted')}
              onOpen={openCanvasFile}
            />
          )) : (
            <EmptySection>{t('chat.sessionSummary.noFiles')}</EmptySection>
          )}
        </SummarySection>

        <SummarySection
          icon={FileDiff}
          title={t('chat.sessionSummary.changes')}
          open={changesOpen}
          onOpen={openChangesCanvas}
          onToggle={() => setChangesOpen((value) => !value)}
          summary={(
            <CountSummary
              count={changes.fileCount}
              label={t('chat.sessionSummary.fileUnit', { count: changes.fileCount })}
              added={changes.added}
              removed={changes.removed}
              showFileCount={false}
              fontSize={13}
            />
          )}
        >
          {changes.operations.length > 0 ? changes.operations.map((op) => (
            <ChangeOperationRow
              key={op.id}
              op={op}
              cwd={cwd}
              reverted={revertedSet.has(op.id)}
              revertedLabel={t('chat.sessionSummary.reverted')}
              onOpen={openChange}
            />
          )) : (
            <EmptySection>{t('chat.sessionSummary.noChanges')}</EmptySection>
          )}
        </SummarySection>

        {agents.length > 0 && (
          <>
            <GroupDivider />
            <GroupLabel>{t('chat.sessionSummary.subagents')}</GroupLabel>
            <AgentSummaryRow agents={agents} onOpen={openTasksCanvas} onOpenAgent={openAgent} />
          </>
        )}

        <GroupDivider />
        <GroupLabel>{t('chat.sessionSummary.sources')}</GroupLabel>
        {sources.length > 0 ? sources.map((source) => (
          <SourceRow key={source.key} source={source} onOpen={openSource} />
        )) : (
          <EmptySection>{t('chat.sessionSummary.noSources')}</EmptySection>
        )}
      </div>
    </div>
  )
}

export default function SessionSummaryOverlay({ open, cardId, cwd, topOffset = 0 }) {
  const [lightboxSource, setLightboxSource] = useState(null)

  useEffect(() => {
    if (!open) setLightboxSource(null)
  }, [open])

  return (
    <aside
      aria-hidden={!open}
      style={{
        position: 'absolute',
        top: 0,
        right: 0,
        bottom: 0,
        width: SESSION_SUMMARY_OVERLAY_WIDTH,
        minWidth: 0,
        minHeight: 0,
        pointerEvents: 'none',
        zIndex: 40,
      }}
    >
      <SessionSummaryCard
        open={open}
        cardId={cardId}
        cwd={cwd}
        topOffset={topOffset}
        onSourceOpen={setLightboxSource}
      />
      <ImageLightbox
        src={lightboxSource?.src}
        alt={lightboxSource?.label}
        onClose={() => setLightboxSource(null)}
      />
    </aside>
  )
}
