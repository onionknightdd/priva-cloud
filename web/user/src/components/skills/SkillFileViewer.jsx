import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Info, MessageCircle, MoreVertical, Sparkles, Trash2 } from 'lucide-react'
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github-dark.css'
import bash from 'highlight.js/lib/languages/bash'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import yaml from 'highlight.js/lib/languages/yaml'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import java from 'highlight.js/lib/languages/java'
import markdown from 'highlight.js/lib/languages/markdown'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'

hljs.registerLanguage('bash', bash)
hljs.registerLanguage('python', python)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('yaml', yaml)
hljs.registerLanguage('json', json)
hljs.registerLanguage('xml', xml)
hljs.registerLanguage('html', xml)
hljs.registerLanguage('css', css)
hljs.registerLanguage('sql', sql)
hljs.registerLanguage('go', go)
hljs.registerLanguage('rust', rust)
hljs.registerLanguage('java', java)
hljs.registerLanguage('markdown', markdown)
hljs.registerLanguage('dockerfile', dockerfile)
hljs.registerLanguage('ini', ini)
hljs.registerLanguage('plaintext', () => ({ contains: [] }))
import useSkillsStore from '../../stores/skillsStore'
import useChatStore from '../../stores/chatStore'
import useUiStore from '@shared/stores/uiStore'
import MarkdownRenderer from '../markdown/MarkdownRenderer'
import CopyButton from '@shared/components/shared/CopyButton'
import Toggle from '@shared/components/shared/Toggle'
import OptimizePopup from '../shared/OptimizePopup'
import VirtualizedCodeLines from '../shared/VirtualizedCodeLines'
import getLineFromNode from '../../utils/getLineFromNode'

function splitLeadingFrontmatter(content) {
  if (!content) return { bodyContent: '', lineNumberStart: 1 }
  const match = content.match(/^---\r?\n[\s\S]*?\r?\n---(?:\r?\n|$)/)
  if (!match) return { bodyContent: content, lineNumberStart: 1 }

  let bodyContent = content.slice(match[0].length)
  const leadingWhitespace = bodyContent.match(/^\s+/)?.[0] || ''
  bodyContent = bodyContent.slice(leadingWhitespace.length)

  const skipped = match[0] + leadingWhitespace
  const lineNumberStart = (skipped.match(/\n/g) || []).length + 1
  return { bodyContent, lineNumberStart }
}

function stripLeadingFrontmatter(content) {
  return splitLeadingFrontmatter(content).bodyContent
}

function basename(path) {
  if (!path) return ''
  const parts = String(path).split('/').filter(Boolean)
  return parts[parts.length - 1] || path
}

function shortScopeLabel(skill) {
  if (!skill) return ''
  if (skill.scope === 'personal') return 'Personal'
  if (!skill.cwd) return 'Project'
  const parts = String(skill.cwd).split('/').filter(Boolean)
  return parts[parts.length - 1] || skill.cwd
}

const iconButtonStyle = {
  width: 28,
  height: 28,
  padding: 0,
  display: 'inline-flex',
  alignItems: 'center',
  justifyContent: 'center',
  background: 'transparent',
  border: 'none',
  borderRadius: 4,
  color: 'var(--text-dim)',
  cursor: 'pointer',
  transition: 'color 150ms ease, background 150ms ease',
}
const iconButtonIn = (e) => {
  e.currentTarget.style.color = 'var(--text-primary)'
  e.currentTarget.style.background = 'var(--bg-elevated)'
}
const iconButtonOut = (e) => {
  e.currentTarget.style.color = 'var(--text-dim)'
  e.currentTarget.style.background = 'transparent'
}

const menuStyle = {
  position: 'fixed',
  minWidth: 156,
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  padding: '4px 0',
  zIndex: 1000,
}

const menuItemStyle = {
  display: 'flex',
  alignItems: 'center',
  gap: 10,
  width: '100%',
  padding: '8px 12px',
  background: 'transparent',
  border: 'none',
  cursor: 'pointer',
  textAlign: 'left',
  color: 'var(--text-primary)',
  fontSize: 13,
  transition: 'background 150ms ease, color 150ms ease',
}

const menuItemIn = (e) => { e.currentTarget.style.background = 'var(--bg-surface)' }
const menuItemOut = (e) => { e.currentTarget.style.background = 'transparent' }

export default function SkillFileViewer({ animKey }) {
  const { t } = useTranslation()
  const selectedFile = useSkillsStore((s) => s.selectedFile)
  const selectedSkill = useSkillsStore((s) => s.selectedSkill)
  const skillDetail = useSkillsStore((s) => s.skillDetail)
  const detailLoading = useSkillsStore((s) => s.detailLoading)
  const fileContent = useSkillsStore((s) => s.fileContent)
  const fileLoading = useSkillsStore((s) => s.fileLoading)
  const viewerMode = useSkillsStore((s) => s.viewerMode)
  const viewMode = useSkillsStore((s) => s.viewMode)
  const setViewMode = useSkillsStore((s) => s.setViewMode)
  const cacheSkillFile = useSkillsStore((s) => s.cacheSkillFile)
  const toggleSkill = useSkillsStore((s) => s.toggleSkill)
  const deleteSkill = useSkillsStore((s) => s.deleteSkill)
  const setActiveNavTab = useUiStore((s) => s.setActiveNavTab)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const setPendingComposerSend = useChatStore((s) => s.setPendingComposerSend)
  const setCwdDraft = useChatStore((s) => s.setCwdDraft)
  const skillSummary = useSkillsStore((s) => {
    if (!selectedSkill) return null
    const matches = (skill) =>
      skill.name === selectedSkill.name
      && skill.scope === selectedSkill.scope
      && (skill.cwd || null) === (selectedSkill.cwd || null)
    if (selectedSkill.scope === 'personal') return (s.personal || []).find(matches) || null
    return (s.groups || []).flatMap((group) => group.skills || []).find(matches) || null
  })

  // Selection tooltip state
  const [tooltip, setTooltip] = useState(null) // { x, y, startLine, endLine, selectedText }
  // Optimize popup state
  const [optimizeData, setOptimizeData] = useState(null)
  const [skillMenuOpen, setSkillMenuOpen] = useState(false)
  const [skillMenuPosition, setSkillMenuPosition] = useState(null)
  const contentRef = useRef(null)
  const moreButtonRef = useRef(null)
  const skillMenuRef = useRef(null)
  const skillMdRetryKeyRef = useRef(null)

  const fullPath = skillDetail?.base_path && selectedFile
    ? `${skillDetail.base_path}/${selectedFile}`
    : selectedFile
  const fileName = basename(selectedFile)
  const fallbackSkillFile = selectedFile === 'SKILL.md' && typeof skillDetail?.skill_md_content === 'string'
    ? { path: 'SKILL.md', content: skillDetail.skill_md_content, language: 'markdown', is_binary: false }
    : null
  const effectiveFileContent = fileContent || fallbackSkillFile
  const rawFileContent = effectiveFileContent?.content || ''
  const isSkillMarkdownContent = selectedFile === 'SKILL.md' || (viewerMode === 'skill' && effectiveFileContent?.path === 'SKILL.md')
  const skillMarkdownParts = isSkillMarkdownContent
    ? splitLeadingFrontmatter(rawFileContent)
    : { bodyContent: rawFileContent, lineNumberStart: 1 }
  const skillMarkdownBody = skillMarkdownParts.bodyContent
  const skillMarkdownLineNumberStart = skillMarkdownParts.lineNumberStart
  const skillDescription = skillDetail?.description || skillDetail?.frontmatter?.description || ''
  const skillTrigger = skillDetail?.frontmatter?.trigger || t('skills.defaultTrigger', { defaultValue: 'Slash command + auto' })
  const addedBy = skillDetail?.frontmatter?.author || skillDetail?.frontmatter?.added_by || shortScopeLabel(selectedSkill)
  const skillEnabled = skillSummary?.enabled !== false
  const isSkillView = viewerMode !== 'file'
  const skillDetailContent = skillMarkdownBody || stripLeadingFrontmatter(rawFileContent)
  const skillDetailLineNumberStart = isSkillMarkdownContent ? skillMarkdownLineNumberStart : 1
  const optimizeSourceContent = isSkillView ? skillDetailContent : rawFileContent
  const optimizeLineNumberStart = isSkillView ? skillDetailLineNumberStart : 1

  useEffect(() => {
    if (viewerMode !== 'skill' || !selectedSkill || detailLoading || fileLoading) return
    if (effectiveFileContent?.content) return

    const retryKey = `${selectedSkill.scope}:${selectedSkill.cwd || ''}:${selectedSkill.name}:SKILL.md`
    if (skillMdRetryKeyRef.current === retryKey) return
    skillMdRetryKeyRef.current = retryKey
    cacheSkillFile('SKILL.md')
  }, [viewerMode, selectedSkill, detailLoading, fileLoading, effectiveFileContent?.content, cacheSkillFile])

  useEffect(() => {
    if (!skillMenuOpen) return
    const handler = (event) => {
      if (skillMenuRef.current?.contains(event.target)) return
      if (moreButtonRef.current?.contains(event.target)) return
      setSkillMenuOpen(false)
    }
    const keyHandler = (event) => {
      if (event.key === 'Escape') setSkillMenuOpen(false)
    }
    document.addEventListener('mousedown', handler)
    document.addEventListener('keydown', keyHandler)
    return () => {
      document.removeEventListener('mousedown', handler)
      document.removeEventListener('keydown', keyHandler)
    }
  }, [skillMenuOpen])

  const openSkillMenu = () => {
    const rect = moreButtonRef.current?.getBoundingClientRect()
    if (!rect) return
    const width = 156
    const left = Math.max(8, Math.min(window.innerWidth - width - 8, rect.right - width))
    setSkillMenuPosition({ top: rect.bottom + 6, left, width })
    setSkillMenuOpen((open) => !open)
  }

  const handleTryInChat = () => {
    if (!selectedSkill?.name) return
    setSkillMenuOpen(false)
    if (selectedSkill.scope === 'workdir' && selectedSkill.cwd) {
      setCwdDraft(selectedSkill.cwd)
    }
    setPendingComposerSend({
      skill: {
        name: selectedSkill.name,
        level: selectedSkill.scope === 'workdir' ? 'project' : 'global',
      },
      text: '',
    })
    setActiveNavTab('priva')
  }

  const handleUninstall = () => {
    if (!selectedSkill?.name) return
    const { scope, cwd, name } = selectedSkill
    setSkillMenuOpen(false)
    showConfirmDialog({
      title: t('skills.uninstallTitle', { defaultValue: 'Uninstall Skill' }),
      message: t('skills.uninstallMessage', {
        name,
        defaultValue: `Uninstall skill "${name}"? This cannot be undone.`,
      }),
      confirmLabel: t('skills.uninstall', { defaultValue: 'Uninstall' }),
      danger: true,
      requireText: name,
      onConfirm: () => deleteSkill(scope, cwd, name),
    })
  }

  // Handle text selection in the code area (editable workdir-scoped skills only)
  const isProjectSkill = selectedSkill?.scope === 'workdir'
  // Timestamp guard: prevent selectionchange from racing with mouseup
  const tooltipSetAtRef = useRef(0)

  // Listen on document so mouseup is caught even when drag ends outside content area
  useEffect(() => {
    const onMouseUp = (e) => {
      if (!isProjectSkill) return
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !selection.toString().trim()) {
        return
      }

      const text = selection.toString()
      const range = selection.getRangeAt(0)

      // Make sure the selection overlaps our content area.
      // The commonAncestorContainer may be inside contentRef (normal)
      // or ABOVE it (browser lifts ancestor when selecting many rows).
      const ancestor = range.commonAncestorContainer
      if (contentRef.current
        && !contentRef.current.contains(ancestor)
        && !(ancestor.contains?.(contentRef.current))) {
        return
      }

      let startLine = getLineFromNode(range.startContainer, range.startOffset)
      let endLine = getLineFromNode(range.endContainer, Math.max(0, range.endOffset - 1))

      // Fallback: when selecting 3+ rows, browsers may lift range containers
      // above the row list (to a wrapper div), causing getLineFromNode to fail.
      // Use range.intersectsNode against the mounted [data-line-number] rows.
      if ((!startLine || !endLine) && contentRef.current) {
        const rowNodes = contentRef.current.querySelectorAll('[data-line-number]')
        if (rowNodes.length > 0) {
          if (!startLine) {
            for (let i = 0; i < rowNodes.length; i++) {
              if (range.intersectsNode(rowNodes[i])) {
                startLine = Number(rowNodes[i].dataset.lineNumber) || i + 1
                break
              }
            }
          }
          if (!endLine) {
            for (let i = rowNodes.length - 1; i >= 0; i--) {
              if (range.intersectsNode(rowNodes[i])) {
                endLine = Number(rowNodes[i].dataset.lineNumber) || i + 1
                break
              }
            }
          }
        }
      }

      if (!startLine || !endLine) {
        return
      }

      // Position tooltip near the mouse release point (always close to selection end)
      tooltipSetAtRef.current = Date.now()
      setTooltip({
        x: e.clientX + 8,
        y: e.clientY + 8,
        startLine: Math.min(startLine, endLine),
        endLine: Math.max(startLine, endLine),
        selectedText: text,
      })
    }

    document.addEventListener('mouseup', onMouseUp)
    return () => document.removeEventListener('mouseup', onMouseUp)
  }, [isProjectSkill])

  // Hide tooltip when selection is cleared by clicking elsewhere
  useEffect(() => {
    const onSelectionChange = () => {
      // Ignore selectionchange events within 150ms of setting the tooltip,
      // as browsers may fire multiple events with a transiently collapsed selection.
      if (Date.now() - tooltipSetAtRef.current < 150) return
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed) {
        setTooltip(null)
      }
    }
    document.addEventListener('selectionchange', onSelectionChange)
    return () => document.removeEventListener('selectionchange', onSelectionChange)
  }, [])

  // Handle clicking the optimize tooltip
  const handleOptimizeClick = useCallback(() => {
    if (!tooltip) return
    // Split the currently rendered source into lines. In skill detail mode this
    // is SKILL.md body content, with row numbers offset to the real file lines.
    const allLines = (optimizeSourceContent || '').replace(/\n$/, '').split('\n')
    const selStart = tooltip.startLine
    const selEnd = tooltip.endLine
    // +2 context lines before and after (for preview only, not sent in prompt)
    const sourceStartLine = optimizeLineNumberStart
    const sourceEndLine = sourceStartLine + Math.max(allLines.length - 1, 0)
    const ctxStart = Math.max(sourceStartLine, selStart - 2)
    const ctxEnd = Math.min(sourceEndLine, selEnd + 2)
    const previewLines = []
    for (let i = ctxStart; i <= ctxEnd; i++) {
      previewLines.push({
        lineNum: i,
        text: allLines[i - sourceStartLine] || '',
        isSelected: i >= selStart && i <= selEnd,
      })
    }
    setOptimizeData({
      source: 'skill',
      skillName: selectedSkill?.name || '',
      filePath: fullPath || selectedFile || '',
      startLine: selStart,
      endLine: selEnd,
      selectedText: tooltip.selectedText,
      language: fileContent?.language || '',
      previewLines,
      // Anchor position for popup (viewport coords)
      anchorX: tooltip.x,
      anchorY: tooltip.y,
    })
    setTooltip(null)
    window.getSelection()?.removeAllRanges()
  }, [tooltip, selectedSkill, fullPath, selectedFile, fileContent, optimizeSourceContent, optimizeLineNumberStart])

  if (!selectedSkill) {
    return (
      <div
        className="flex-1 flex items-center justify-center"
        style={{ background: 'var(--bg-base)', color: 'var(--text-dim)', fontSize: 13 }}
      >
        {t('skills.selectToView')}
      </div>
    )
  }

  if (detailLoading || fileLoading) {
    return (
      <div className="flex-1 flex flex-col" style={{ background: 'var(--bg-base)' }}>
        <div className="px-4 py-3" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
          <div className="skeleton" style={{ height: 16, width: 200, borderRadius: 2 }} />
        </div>
        <div className="flex-1 p-4 flex flex-col gap-2">
          {[1, 2, 3, 4, 5].map((i) => (
            <div key={i} className="skeleton" style={{ height: 14, width: `${80 - i * 10}%`, borderRadius: 2 }} />
          ))}
        </div>
      </div>
    )
  }

  const effectivePath = selectedFile || effectiveFileContent?.path || ''
  const isMarkdown = effectivePath.endsWith('.md')
  const previewContent = isSkillMarkdownContent ? skillMarkdownBody : (effectiveFileContent?.content || '')
  const sourceContent = effectiveFileContent?.content || ''
  const showToggle = isMarkdown
  const ModeToggle = showToggle ? (
    <div className="flex items-center" style={{ border: '1px solid var(--border)', borderRadius: '4px' }}>
      <button
        className="px-2 py-1 text-xs"
        style={{
          background: viewMode === 'preview' ? 'var(--bg-elevated)' : 'transparent',
          border: 'none',
          borderRadius: '4px 0 0 4px',
          cursor: 'pointer',
          color: viewMode === 'preview' ? 'var(--text-primary)' : 'var(--text-dim)',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={() => setViewMode('preview')}
      >
        {t('skills.preview')}
      </button>
      <button
        className="px-2 py-1 text-xs"
        style={{
          background: viewMode === 'source' ? 'var(--bg-elevated)' : 'transparent',
          border: 'none',
          borderRadius: '0 4px 4px 0',
          cursor: 'pointer',
          color: viewMode === 'source' ? 'var(--text-primary)' : 'var(--text-dim)',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={() => setViewMode('source')}
      >
        {t('skills.source')}
      </button>
    </div>
  ) : null

  return (
    <div key={animKey} className="skill-drawer-left" style={{
      flex: '1 1 0%',
      display: 'flex',
      flexDirection: 'column',
      overflow: 'hidden',
      background: 'var(--bg-base)',
      minWidth: 0,
      maxWidth: '100%',
      width: 0,
      animation: 'skill-content-slide-in 0.5s cubic-bezier(0.16, 1, 0.3, 1)',
    }}>
      {!isSkillView && (
        <div
          className="px-4 py-3 flex-shrink-0"
          style={{ borderBottom: '1px solid var(--border-subtle)' }}
        >
          <div className="flex items-center justify-between" style={{ gap: 12 }}>
            <span
              className="truncate"
              style={{ color: 'var(--text-primary)', fontSize: 16, fontWeight: 700, minWidth: 0 }}
            >
              {fileName}
            </span>
            <div className="flex items-center gap-2 flex-shrink-0">
              {ModeToggle}
            </div>
          </div>
          <div className="flex items-center" style={{ marginTop: 4, gap: 8 }}>
            <div
              className="truncate"
              title={fullPath || ''}
              style={{
                color: 'var(--text-dim)',
                fontSize: 12,
                fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                minWidth: 0,
                flex: '1 1 auto',
              }}
            >
              {fullPath}
            </div>
            {fullPath && (
              <div className="flex-shrink-0">
                <CopyButton content={fullPath} inline />
              </div>
            )}
          </div>
        </div>
      )}

      {/* Content — uses width:0 + flex:1 to force the flex item to take
          only the space allocated by the parent, never more. overflow:auto
          on this element then scrolls the code within that bounded area. */}
      <div
        ref={contentRef}
        style={{
          flex: '1 1 0%',
          overflow: 'auto',
          minWidth: 0,
          minHeight: 0,
          width: '100%',
          position: 'relative',
          overflowAnchor: 'none',
        }}
      >
        {isSkillView ? (
          <div className="p-4" style={{ maxWidth: '100%', minWidth: 0 }}>
            <div className="flex items-start justify-between" style={{ gap: 16, marginBottom: 24 }}>
              <h2 style={{ margin: 0, color: 'var(--text-primary)', fontSize: 16, lineHeight: '24px', fontWeight: 700 }}>
                {skillDetail?.name || selectedSkill?.name}
              </h2>
              <div className="flex items-center gap-2 flex-shrink-0">
                <Toggle
                  checked={skillEnabled}
                  onChange={() => toggleSkill(selectedSkill.name)}
                  showLabel={false}
                  ariaLabel={skillEnabled ? t('skills.disableSkill') : t('skills.enableSkill')}
                />
                <button
                  ref={moreButtonRef}
                  type="button"
                  className="inline-flex items-center justify-center"
                  title={t('skills.more', { defaultValue: 'More' })}
                  style={iconButtonStyle}
                  onClick={openSkillMenu}
                  onMouseEnter={iconButtonIn}
                  onMouseLeave={iconButtonOut}
                >
                  <MoreVertical size={16} strokeWidth={1.5} />
                </button>
              </div>
            </div>

            <div className="flex items-start" style={{ gap: 40, marginBottom: 20 }}>
              <div>
                <div style={{ color: 'var(--text-dim)', fontSize: 11, marginBottom: 4 }}>
                  {t('skills.addedBy', { defaultValue: 'Added by' })}
                </div>
                <div style={{ color: 'var(--text-primary)', fontSize: 12 }}>
                  {addedBy}
                </div>
              </div>
              <div>
                <div style={{ color: 'var(--text-dim)', fontSize: 11, marginBottom: 4 }}>
                  {t('skills.trigger', { defaultValue: 'Trigger' })}
                </div>
                <div style={{ color: 'var(--text-primary)', fontSize: 12 }}>
                  {skillTrigger}
                </div>
              </div>
            </div>

            {skillDescription && (
              <div style={{ marginBottom: 20 }}>
                <div className="flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 11, marginBottom: 8 }}>
                  <span>{t('skills.descriptionLabel', { defaultValue: 'Description' })}</span>
                  <Info size={12} strokeWidth={1.5} />
                </div>
                <div style={{ color: 'var(--text-primary)', fontSize: 12, lineHeight: 1.55, overflowWrap: 'break-word' }}>
                  {skillDescription}
                </div>
              </div>
            )}

            <div
              style={{
                border: '1px solid var(--border)',
                borderRadius: 4,
                background: 'var(--bg-base)',
                overflow: 'hidden',
              }}
            >
              <div className="flex items-center justify-end px-3 py-2" style={{ borderBottom: '1px solid var(--border-subtle)', gap: 8 }}>
                {ModeToggle}
                {skillDetailContent && !effectiveFileContent?.is_binary && (
                  <CopyButton content={skillDetailContent} inline />
                )}
              </div>
              {effectiveFileContent?.is_binary ? (
                <div className="flex items-center justify-center" style={{ color: 'var(--text-dim)', fontSize: 13, minHeight: 160 }}>
                  {t('skills.binaryFile')}
                </div>
              ) : isMarkdown && viewMode === 'preview' ? (
                <div className="p-4">
                  <MarkdownRenderer content={skillDetailContent} />
                </div>
              ) : (
                <HighlightedCode
                  content={skillDetailContent}
                  language={effectiveFileContent?.language}
                  lineNumberStart={skillDetailLineNumberStart}
                  scrollRef={contentRef}
                />
              )}
            </div>
          </div>
        ) : effectiveFileContent?.is_binary ? (
          <div
            className="flex items-center justify-center h-full"
            style={{ color: 'var(--text-dim)', fontSize: 13 }}
          >
            {t('skills.binaryFile')}
          </div>
        ) : isMarkdown && viewMode === 'preview' ? (
          <div className="p-4">
            {selectedFile === 'SKILL.md' && skillDescription && (
              <div style={{ marginBottom: 24, paddingBottom: 20, borderBottom: '1px solid var(--border-subtle)' }}>
                <div className="flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 12, marginBottom: 8 }}>
                  <span>{t('skills.descriptionLabel', { defaultValue: 'Description' })}</span>
                  <Info size={12} strokeWidth={1.5} />
                </div>
                <div style={{ color: 'var(--text-primary)', fontSize: 14, lineHeight: 1.55, overflowWrap: 'break-word' }}>
                  {skillDescription}
                </div>
              </div>
            )}
            <MarkdownRenderer content={previewContent} />
          </div>
        ) : (
            <HighlightedCode
              content={sourceContent}
              language={effectiveFileContent?.language}
              scrollRef={contentRef}
            />
        )}
      </div>

      {/* Selection tooltip — portaled to body to avoid transform/transition containing block issues */}
      {tooltip && createPortal(
        <button
          className="flex items-center gap-1"
          onClick={handleOptimizeClick}
          style={{
            position: 'fixed',
            left: tooltip.x,
            top: tooltip.y,
            zIndex: 9999,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            padding: '4px 10px',
            cursor: 'pointer',
            fontSize: 12,
            color: 'var(--text-secondary)',
            whiteSpace: 'nowrap',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.borderColor = 'var(--blue)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.borderColor = 'var(--border)'
          }}
        >
          <Sparkles size={14} strokeWidth={1.5} />
          {t('optimize.helpToOptimize')}
        </button>,
        document.body
      )}

      {skillMenuOpen && skillMenuPosition && createPortal(
        <div
          ref={skillMenuRef}
          style={{
            ...menuStyle,
            top: skillMenuPosition.top,
            left: skillMenuPosition.left,
            width: skillMenuPosition.width,
          }}
        >
          <button
            type="button"
            style={menuItemStyle}
            onClick={handleTryInChat}
            onMouseEnter={menuItemIn}
            onMouseLeave={menuItemOut}
          >
            <MessageCircle size={14} strokeWidth={1.5} />
            <span>{t('skills.tryInChat', { defaultValue: 'Try in chat' })}</span>
          </button>
          <button
            type="button"
            style={{ ...menuItemStyle, color: 'var(--red)' }}
            onClick={handleUninstall}
            onMouseEnter={menuItemIn}
            onMouseLeave={menuItemOut}
          >
            <Trash2 size={14} strokeWidth={1.5} />
            <span>{t('skills.uninstall', { defaultValue: 'Uninstall' })}</span>
          </button>
        </div>,
        document.body
      )}

      {/* Optimize floating popup — also portaled */}
      {optimizeData && createPortal(
        <OptimizePopup
          data={optimizeData}
          onClose={() => setOptimizeData(null)}
        />,
        document.body
      )}

      <style>{`
        @keyframes skill-content-slide-in {
          from { transform: translateX(-14px); opacity: 0; }
          to { transform: translateX(0); opacity: 1; }
        }
        @media (prefers-reduced-motion: reduce) {
          .skill-drawer-left {
            animation: none !important;
          }
        }
      `}</style>
    </div>
  )
}

function HighlightedCode({ content, language, lineNumberStart = 1, scrollRef }) {
  const highlighted = useMemo(() => {
    if (!content) return null
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(content, { language }).value
      }
      return hljs.highlightAuto(content).value
    } catch {
      return null
    }
  }, [content, language])

  const lines = useMemo(() => {
    if (!content) return []
    const raw = content.replace(/\n$/, '')
    if (!highlighted) {
      return raw.split('\n').map((line) => ({ text: line, html: null }))
    }
    // Split highlighted HTML by newlines
    return highlighted.replace(/\n$/, '').split('\n').map((html) => ({ text: null, html }))
  }, [content, highlighted])

  return (
    <VirtualizedCodeLines
      lines={lines}
      lineNumberStart={lineNumberStart}
      scrollRef={scrollRef}
    />
  )
}
