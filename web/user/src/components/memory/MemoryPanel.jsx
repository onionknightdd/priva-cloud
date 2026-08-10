import { useEffect, useState } from 'react'
import {
  ArrowLeft, UsersRound, FolderGit2, FileText, Brain,
  ChevronRight, ChevronDown, Trash2, Loader,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import ResizeHandle from '@shared/components/shared/ResizeHandle'
import useUiStore from '@shared/stores/uiStore'
import Toggle from '@shared/components/shared/Toggle'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import CopyButton from '@shared/components/shared/CopyButton'
import safeStorage from '@shared/utils/safeStorage'
import { DUR_MIGRATION } from '@shared/motion/tokens'
import useMemoryStore from '../../stores/memoryStore'
import ScriptEditor from '../shared/ScriptEditor'
import MarkdownRenderer from '../markdown/MarkdownRenderer'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

const ROW = {
  padding: '6px 8px', width: '100%', border: 'none', borderRadius: 0,
  cursor: 'pointer', transition: 'background 150ms ease, color 150ms ease',
  background: 'transparent',
}

const VIEWER_WIDTH_KEY = 'memory-viewer-width'
const VIEWER_MIN_WIDTH = 480
const VIEWER_MAX_WIDTH_VW = 0.6

function getViewerWidth() {
  const max = Math.max(VIEWER_MIN_WIDTH, Math.floor(window.innerWidth * VIEWER_MAX_WIDTH_VW))
  return safeStorage.getNumber(VIEWER_WIDTH_KEY, 720, { min: VIEWER_MIN_WIDTH, max })
}
function activeRow(isActive) {
  return {
    ...ROW,
    background: isActive ? 'var(--bg-elevated)' : 'transparent',
    borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
  }
}

// A group header (USER / a project's short cwd) with an optional dim full path.
function SectionLabel({ icon: Icon, iconColor, label, sub, expanded, onToggle }) {
  const Chevron = expanded ? ChevronDown : ChevronRight
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-expanded={expanded}
      className="flex items-center gap-2 w-full text-left"
      style={{ padding: '10px 8px 4px', background: 'transparent', border: 'none', color: 'var(--text-dim)', cursor: 'pointer', transition: 'background 150ms ease, color 150ms ease' }}
      onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.color = 'var(--text-secondary)' }}
      onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-dim)' }}
    >
      <Chevron size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'currentColor' }} />
      {Icon && <Icon size={13} strokeWidth={1.5} style={{ flexShrink: 0, color: iconColor }} />}
      <span
        className="truncate uppercase font-semibold"
        style={{ color: 'currentColor', fontSize: 12, letterSpacing: '0.06em' }}
      >
        {label}
      </span>
      {sub && (
        <span
          className="truncate"
          style={{ color: 'currentColor', fontSize: 12, fontFamily: "'JetBrains Mono', monospace", opacity: 0.7 }}
        >
          {sub}
        </span>
      )}
    </button>
  )
}

// A CLAUDE.md row (user or project scope).
function ClaudeMdRow({ scopeInfo, isActive, onSelect, t }) {
  return (
    <button
      onClick={onSelect}
      className="flex items-center gap-2 text-left"
      style={{ ...activeRow(isActive), padding: '7px 8px 7px 24px' }}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      <img
        src="/file-icons/claude.svg"
        width={14}
        height={14}
        alt=""
        draggable={false}
        style={{ flexShrink: 0, display: 'block' }}
      />
      <div className="flex flex-col min-w-0 flex-1">
        <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 10 }}>CLAUDE.md</span>
        {!scopeInfo?.exists && (
          <span className="truncate" style={{ color: 'var(--text-dim)', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>
            {t('memory.notCreated')}
          </span>
        )}
      </div>
    </button>
  )
}

// One auto-memory file (Claude-written); hover reveals a delete affordance.
function FileRow({ file, isActive, onSelect, onDelete, t }) {
  const [hover, setHover] = useState(false)
  return (
    <div
      className="flex items-center"
      style={{ ...activeRow(isActive), padding: 0, paddingLeft: 34 }}
      onMouseEnter={(e) => { setHover(true); if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { setHover(false); if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      <button
        onClick={onSelect}
        className="flex items-center gap-2 text-left flex-1 min-w-0"
        style={{ background: 'transparent', border: 'none', padding: '7px 4px 7px 6px', cursor: 'pointer' }}
      >
        <FileText size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: file.is_index ? 'var(--cyan)' : 'var(--text-dim)' }} />
        <div className="min-w-0 flex-1">
          <span className="truncate flex items-center gap-1" style={{ color: 'var(--text-primary)', fontSize: 12 }}>
            {file.name}
            {file.is_index && (
              <span className="uppercase" style={{ color: 'var(--cyan)', fontSize: 9, letterSpacing: '0.06em', border: '1px solid var(--border)', borderRadius: 2, padding: '0 3px' }}>
                {t('memory.autoIndex')}
              </span>
            )}
          </span>
        </div>
      </button>
      <button
        onClick={onDelete}
        title={t('memory.deleteFile')}
        className="inline-flex items-center justify-center flex-shrink-0"
        style={{ width: 26, height: 26, marginRight: 4, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-dim)', cursor: 'pointer', opacity: hover ? 1 : 0, transition: 'color 150ms ease, opacity 150ms ease' }}
        onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
        onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
      >
        <Trash2 size={14} strokeWidth={1.5} />
      </button>
    </div>
  )
}

// The per-project Auto memory folder: expandable, with an ON/OFF toggle that
// writes autoMemoryEnabled into {cwd}/.claude/settings.json.
function AutoFolder({ project, expanded, onToggleExpand, onToggleEnabled, selection, onSelectFile, onDeleteFile, t }) {
  const Chevron = expanded ? ChevronDown : ChevronRight
  const count = project.files.length
  return (
    <div className="flex flex-col">
      <div className="flex items-center" style={{ ...ROW, cursor: 'default', paddingLeft: 24, paddingRight: 6 }}>
        <button
          onClick={onToggleExpand}
          className="flex items-center gap-2 text-left flex-1 min-w-0"
          style={{ background: 'transparent', border: 'none', padding: 0, cursor: 'pointer' }}
        >
          <Chevron size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
          <Brain size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--purple)' }} />
          <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 11 }}>{t('memory.autoMemory')}</span>
          <span style={{ color: 'var(--text-dim)', fontSize: 10, fontFamily: "'JetBrains Mono', monospace" }}>{count > 0 ? count : ''}</span>
        </button>
        <Toggle
          size="xs"
          checked={project.enabled}
          onChange={(v) => onToggleEnabled(project.cwd, v)}
          ariaLabel={`${t('memory.autoMemory')} — ${shortCwd(project.cwd)}`}
        />
      </div>
      <AnimatedCollapse
        open={expanded}
        heightDuration={DUR_MIGRATION.accordionModeB}
        opacityDuration={DUR_MIGRATION.accordionModeB}
      >
        {count === 0 ? (
          <div style={{ paddingLeft: 40, paddingTop: 2, paddingBottom: 4, color: 'var(--text-dim)', fontSize: 11 }}>
            {t('memory.autoEmpty')}
          </div>
        ) : (
          project.files.map((f) => (
            <FileRow
              key={f.name}
              file={f}
              isActive={selection?.kind === 'auto' && selection.cwd === project.cwd && selection.name === f.name}
              onSelect={() => onSelectFile(project.cwd, f.name)}
              onDelete={() => onDeleteFile(project, f)}
              t={t}
            />
          ))
        )}
      </AnimatedCollapse>
    </div>
  )
}

// Memory editor. Left column is a scope-first tree: USER (its CLAUDE.md) then
// each project workdir (its CLAUDE.md + an Auto memory folder of Claude-written
// files). The right column edits whatever is selected — a CLAUDE.md scope or a
// single auto-memory file. The CLI picks up edits on the next run — no restart.
export default function MemoryPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const scopes = useMemoryStore((s) => s.scopes)
  const autoProjects = useMemoryStore((s) => s.autoProjects)
  const expanded = useMemoryStore((s) => s.expanded)
  const loadingList = useMemoryStore((s) => s.loadingList)
  const selection = useMemoryStore((s) => s.selection)
  const content = useMemoryStore((s) => s.content)
  const savedContent = useMemoryStore((s) => s.savedContent)
  const contentLoading = useMemoryStore((s) => s.contentLoading)
  const saving = useMemoryStore((s) => s.saving)
  const error = useMemoryStore((s) => s.error)
  const loadList = useMemoryStore((s) => s.loadList)
  const selectClaude = useMemoryStore((s) => s.selectClaude)
  const selectAuto = useMemoryStore((s) => s.selectAuto)
  const toggleExpand = useMemoryStore((s) => s.toggleExpand)
  const toggleAuto = useMemoryStore((s) => s.toggleAuto)
  const deleteAutoFile = useMemoryStore((s) => s.deleteAutoFile)
  const setContent = useMemoryStore((s) => s.setContent)
  const save = useMemoryStore((s) => s.save)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const [expandedScopes, setExpandedScopes] = useState({})
  const [viewerWidth, setViewerWidthState] = useState(getViewerWidth)
  const [viewMode, setViewMode] = useState('preview')

  useEffect(() => { loadList() }, [loadList])

  const maxViewerWidth = Math.max(VIEWER_MIN_WIDTH, Math.floor(window.innerWidth * VIEWER_MAX_WIDTH_VW))
  const setViewerWidth = (width) => {
    setViewerWidthState(width)
    safeStorage.setItem(VIEWER_WIDTH_KEY, String(width))
  }
  const { dragging: viewerDragging, onMouseDown: onViewerResizeDown } = useResizable({
    initial: viewerWidth, min: VIEWER_MIN_WIDTH, max: maxViewerWidth, direction: 'left', onResize: setViewerWidth,
  })

  const dirty = content !== savedContent
  const userScope = scopes.find((s) => s.scope === 'user')
  const projectScopes = scopes.filter((s) => s.scope === 'project')
  const autoByCwd = new Map(autoProjects.map((p) => [p.cwd, p]))
  const isScopeExpanded = (key) => expandedScopes[key] !== false
  const toggleScope = (key) => setExpandedScopes((current) => ({ ...current, [key]: current[key] === false }))

  // Resolve the active document's absolute path for the editor header.
  let activePath = ''
  if (selection?.kind === 'claude') {
    activePath = scopes.find((s) => s.scope === selection.scope && (s.cwd || null) === (selection.cwd || null))?.path || ''
  } else if (selection?.kind === 'auto') {
    const p = autoByCwd.get(selection.cwd)
    activePath = p?.files.find((f) => f.name === selection.name)?.path || (p ? `${p.memory_dir}/${selection.name}` : '')
  }
  const activeFileName = selection?.kind === 'auto' ? selection.name : 'CLAUDE.md'

  const confirmDelete = (project, file) => {
    showConfirmDialog({
      title: t('memory.deleteFileTitle'),
      message: t('memory.deleteFileMessage', { name: file.name }),
      confirmLabel: t('memory.deleteFileConfirm'),
      danger: true,
      onConfirm: () => deleteAutoFile(project.cwd, file.name),
    })
  }
  const confirmDeleteActive = () => {
    if (selection?.kind !== 'auto') return
    const p = autoByCwd.get(selection.cwd)
    const f = p?.files.find((x) => x.name === selection.name) || { name: selection.name }
    confirmDelete({ cwd: selection.cwd }, f)
  }

  const resolvedBackTitle = backTitle || t('split.backToSessions', { defaultValue: '返回 session view' })
  const ModeToggle = (
    <div className="flex items-center" style={{ border: '1px solid var(--border)', borderRadius: 4 }}>
      <button
        type="button"
        className="px-2 py-1 text-xs"
        onClick={() => setViewMode('preview')}
        style={{
          background: viewMode === 'preview' ? 'var(--bg-elevated)' : 'transparent',
          border: 'none', borderRadius: '4px 0 0 4px', cursor: 'pointer',
          color: viewMode === 'preview' ? 'var(--text-primary)' : 'var(--text-dim)',
          transition: 'background 150ms ease, color 150ms ease',
        }}
      >
        {t('skills.preview')}
      </button>
      <button
        type="button"
        className="px-2 py-1 text-xs"
        onClick={() => setViewMode('source')}
        style={{
          background: viewMode === 'source' ? 'var(--bg-elevated)' : 'transparent',
          border: 'none', borderRadius: '0 4px 4px 0', cursor: 'pointer',
          color: viewMode === 'source' ? 'var(--text-primary)' : 'var(--text-dim)',
          transition: 'background 150ms ease, color 150ms ease',
        }}
      >
        {t('skills.source')}
      </button>
    </div>
  )

  return (
    <div
      className="flex flex-1"
      style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}
    >
      {/* Left — scope-first tree */}
      <div
        className="flex flex-col flex-1 overflow-hidden"
        style={{ background: 'var(--bg-surface)', minWidth: 0, minHeight: 0 }}
      >
        {/* Header */}
        <div
          className="flex items-center flex-shrink-0"
          style={{ gap: 10, height: 44, padding: '0 12px', borderBottom: '1px solid var(--border-subtle)' }}
        >
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center justify-center flex-shrink-0"
            aria-label={resolvedBackTitle}
            title={resolvedBackTitle}
            style={{ width: 28, height: 28, padding: 0, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer', transition: 'color 150ms ease, background 150ms ease' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'transparent' }}
          >
            <ArrowLeft size={16} strokeWidth={1.5} />
          </button>
          <span className="truncate font-bold" style={{ color: 'var(--text-primary)', fontSize: 16 }}>
            {t('tabs.memory')}
          </span>
        </div>

        {/* Tree */}
        <div className="flex flex-col overflow-y-auto" style={{ padding: 8, gap: 1, minHeight: 0 }}>
          {loadingList && scopes.length === 0 && (
            [1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 40, borderRadius: 2 }} />)
          )}

          {/* USER group */}
          {userScope && (
            <>
              <SectionLabel
                icon={UsersRound}
                iconColor="var(--green)"
                label={t('memory.userScope')}
                expanded={isScopeExpanded('user')}
                onToggle={() => toggleScope('user')}
              />
              <AnimatedCollapse
                open={isScopeExpanded('user')}
                heightDuration={DUR_MIGRATION.accordionModeB}
                opacityDuration={DUR_MIGRATION.accordionModeB}
              >
                <ClaudeMdRow
                  scopeInfo={userScope}
                  isActive={selection?.kind === 'claude' && selection.scope === 'user'}
                  onSelect={() => selectClaude('user', null)}
                  t={t}
                />
              </AnimatedCollapse>
            </>
          )}

          {/* One group per project workdir */}
          {projectScopes.map((ps) => {
            const auto = autoByCwd.get(ps.cwd)
            const scopeKey = `project:${ps.cwd}`
            const scopeExpanded = isScopeExpanded(scopeKey)
            return (
              <div key={`p:${ps.cwd}`} className="flex flex-col" style={{ gap: 1 }}>
                <SectionLabel
                  icon={FolderGit2}
                  iconColor="var(--blue)"
                  label={shortCwd(ps.cwd)}
                  sub={ps.cwd}
                  expanded={scopeExpanded}
                  onToggle={() => toggleScope(scopeKey)}
                />
                <AnimatedCollapse
                  open={scopeExpanded}
                  heightDuration={DUR_MIGRATION.accordionModeB}
                  opacityDuration={DUR_MIGRATION.accordionModeB}
                >
                  <ClaudeMdRow
                    scopeInfo={ps}
                    isActive={selection?.kind === 'claude' && selection.scope === 'project' && (selection.cwd || null) === (ps.cwd || null)}
                    onSelect={() => selectClaude('project', ps.cwd)}
                    t={t}
                  />
                  {auto && (
                    <AutoFolder
                      project={auto}
                      expanded={!!expanded[auto.cwd]}
                      onToggleExpand={() => toggleExpand(auto.cwd)}
                      onToggleEnabled={toggleAuto}
                      selection={selection}
                      onSelectFile={selectAuto}
                      onDeleteFile={confirmDelete}
                      t={t}
                    />
                  )}
                </AnimatedCollapse>
              </div>
            )
          })}
        </div>

      </div>

      {/* Right — resizable file viewer */}
      <div
        className="flex flex-col flex-shrink-0 relative overflow-hidden"
        style={{ width: viewerWidth, minWidth: 0, minHeight: 0, background: 'var(--bg-base)' }}
      >
        <ResizeHandle
          onMouseDown={onViewerResizeDown}
          dragging={viewerDragging}
          edge="start"
          style={{ left: 0, top: 0, bottom: 0, zIndex: 10 }}
        />
        {selection == null ? (
          <div className="flex flex-1 items-center justify-center" style={{ color: 'var(--text-dim)', fontSize: 13 }}>
            {t('memory.pickScope')}
          </div>
        ) : (
          <>
            <div className="px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
              <div className="flex items-center justify-between" style={{ gap: 12 }}>
                <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 16, fontWeight: 700, minWidth: 0 }}>
                  {activeFileName}
                </span>
                <div className="flex items-center gap-2 flex-shrink-0">
                  {error && <span className="truncate" style={{ color: 'var(--red)', fontSize: 11, maxWidth: 120 }}>{error}</span>}
                  {ModeToggle}
                  {selection.kind === 'auto' && (
                    <button
                      onClick={confirmDeleteActive}
                      title={t('memory.deleteFile')}
                      className="inline-flex items-center justify-center"
                      style={{ width: 28, height: 28, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-dim)', cursor: 'pointer', transition: 'color 150ms ease, background 150ms ease' }}
                      onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
                      onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)'; e.currentTarget.style.background = 'transparent' }}
                    >
                      <Trash2 size={14} strokeWidth={1.5} />
                    </button>
                  )}
                  <button
                    onClick={save}
                    disabled={!dirty || saving}
                    className="px-3 py-1 text-xs font-semibold uppercase"
                    style={{
                      background: dirty && !saving ? 'var(--blue)' : 'var(--bg-surface)',
                      border: dirty && !saving ? 'none' : '1px solid var(--border)',
                      borderRadius: 4, color: dirty && !saving ? 'var(--text-inverse)' : 'var(--text-dim)',
                      cursor: dirty && !saving ? 'pointer' : 'not-allowed', opacity: dirty && !saving ? 1 : 0.6,
                      letterSpacing: '0.06em', transition: 'all 150ms ease',
                    }}
                  >
                    {saving ? <Loader size={12} strokeWidth={1.5} style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }} /> : t('memory.save')}
                  </button>
                </div>
              </div>
              <div className="flex items-center" style={{ marginTop: 4, gap: 8 }}>
                <span className="truncate" title={activePath} style={{ color: 'var(--text-dim)', fontSize: 12, fontFamily: "'JetBrains Mono', monospace", minWidth: 0, flex: '1 1 auto' }}>
                  {activePath}
                </span>
                {activePath && <CopyButton content={activePath} inline />}
              </div>
            </div>

            {/* Preview / editable source */}
            <div className="flex-1 overflow-y-auto" style={{ minWidth: 0, minHeight: 0 }}>
              {contentLoading ? (
                <div className="skeleton" style={{ height: 400, borderRadius: 2, margin: 16 }} />
              ) : viewMode === 'preview' ? (
                <div className="p-4" style={{ minWidth: 0 }}>
                  <MarkdownRenderer content={content} />
                </div>
              ) : (
                <div style={{ padding: 16, minWidth: 0 }}>
                  <ScriptEditor
                    value={content}
                    onChange={setContent}
                    language="markdown"
                    placeholder={t('memory.placeholder')}
                    minHeight={360}
                    maxHeight={100000}
                  />
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}
