import { useState, useRef, useEffect, useMemo, useCallback, useLayoutEffect } from 'react'
import { useTranslation } from 'react-i18next'
import {
  Search, RefreshCw, Plus, ArrowDownUp, Package, ToggleLeft, ToggleRight,
  Download, Trash2, ChevronDown, Folder, FolderOpen, FileText,
  NotebookPen, FolderGit2, Sparkles, Upload,
} from 'lucide-react'
import useSkillsStore, { skillKey } from '../../stores/skillsStore'
import useSidebarStore from '../../stores/sidebarStore'
import useUiStore from '@shared/stores/uiStore'
import useAuthStore from '@shared/stores/authStore'
import useChatStore from '../../stores/chatStore'
import useTaskStore from '../../stores/taskStore'
import useSkillHubStore from '../../stores/skillHubStore'
import useSkillSyncStore from '../../stores/skillSyncStore'
import { stopActiveStream } from '../../hooks/useSSE'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import CreateSkillDialog from './CreateSkillDialog'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

const ACCEPT = '.zip,.tar,.tar.gz,.tgz,.gz,.skill,application/zip,application/gzip,application/x-gzip,application/x-tar'

const iconBtn = {
  width: 24, height: 24, display: 'flex', alignItems: 'center', justifyContent: 'center',
  background: 'transparent', border: 'none', borderRadius: 4, cursor: 'pointer',
  color: 'var(--text-dim)', transition: 'color 150ms ease, background 150ms ease',
}
const iconBtnIn = (e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }
const iconBtnOut = (e) => { e.currentTarget.style.color = 'var(--text-dim)'; e.currentTarget.style.background = 'transparent' }

const clamp = (value, min, max) => Math.min(max, Math.max(min, value))
const MENU_BASE_LIST_WIDTH = 300
const MENU_BASE_WIDTH = 260
const MENU_MIN_SCALE = 0.82
const MENU_EDGE_GAP = 4

const menuStyle = {
  position: 'fixed',
  background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4,
  padding: '4px 0', zIndex: 1000,
}
const menuItemStyle = {
  display: 'flex', alignItems: 'center', gap: 8, width: '100%', padding: '7px 12px',
  background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left',
  color: 'var(--text-secondary)', fontSize: 13, transition: 'background 150ms ease, color 150ms ease',
}
const menuItemIn = (e) => { e.currentTarget.style.background = 'var(--bg-surface)'; e.currentTarget.style.color = 'var(--text-primary)' }
const menuItemOut = (e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' }

export default function SkillList() {
  const { t } = useTranslation()

  const personal = useSkillsStore((s) => s.personal)
  const groups = useSkillsStore((s) => s.groups)
  const skillsLoading = useSkillsStore((s) => s.skillsLoading)
  const searchQuery = useSkillsStore((s) => s.searchQuery)
  const setSearchQuery = useSkillsStore((s) => s.setSearchQuery)
  const selectedSkill = useSkillsStore((s) => s.selectedSkill)
  const skillDetail = useSkillsStore((s) => s.skillDetail)
  const detailLoading = useSkillsStore((s) => s.detailLoading)
  const selectedFile = useSkillsStore((s) => s.selectedFile)
  const selectFile = useSkillsStore((s) => s.selectFile)
  const selectSkill = useSkillsStore((s) => s.selectSkill)
  const toggleSkill = useSkillsStore((s) => s.toggleSkill)
  const fetchSkills = useSkillsStore((s) => s.fetchSkills)
  const fetchSkillsConfig = useSkillsStore((s) => s.fetchSkillsConfig)
  const uploadSkill = useSkillsStore((s) => s.uploadSkill)
  const downloadSkill = useSkillsStore((s) => s.downloadSkill)
  const deleteSkill = useSkillsStore((s) => s.deleteSkill)
  const listWidth = useSkillsStore((s) => s.listWidth)

  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const setActiveNavTab = useUiStore((s) => s.setActiveNavTab)
  const isAdmin = useAuthStore((s) => s.user?.role === 'admin')
  const openHub = useSkillHubStore((s) => s.openHub)
  const openPushSync = useSkillSyncStore((s) => s.openPushSync)
  const openPullSync = useSkillSyncStore((s) => s.openPullSync)
  const activeCwd = useSidebarStore((s) => s.activeCwd)

  const [collapsedGroups, setCollapsedGroups] = useState({})
  const [showAddMenu, setShowAddMenu] = useState(false)
  const [showSyncMenu, setShowSyncMenu] = useState(false)
  const [dialogMode, setDialogMode] = useState(null) // 'create' | 'upload' | null
  const listRef = useRef(null)
  const addRef = useRef(null)
  const syncRef = useRef(null)
  const fileInputRef = useRef(null)
  const uploadTargetRef = useRef(null)
  const [menuPosition, setMenuPosition] = useState({ sync: null, add: null })

  useEffect(() => { fetchSkillsConfig() }, [fetchSkillsConfig])

  useEffect(() => {
    if (!showAddMenu && !showSyncMenu) return
    const handler = (e) => {
      if (addRef.current && !addRef.current.contains(e.target)) setShowAddMenu(false)
      if (syncRef.current && !syncRef.current.contains(e.target)) setShowSyncMenu(false)
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showAddMenu, showSyncMenu])

  // Build display sections (Personal + workdirs), filtered by search.
  const sections = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    const match = (s) => !q || s.name.toLowerCase().includes(q) || (s.description || '').toLowerCase().includes(q)
    const out = []
    const dp = (personal || []).filter(match)
    if (dp.length) out.push({ key: 'personal', label: t('skills.personal'), title: '~/.claude/skills', icon: NotebookPen, iconColor: 'var(--purple)', skills: dp })
    for (const g of groups || []) {
      const sk = (g.skills || []).filter(match)
      if (sk.length) out.push({ key: g.cwd, label: shortCwd(g.cwd), title: g.cwd, icon: FolderGit2, iconColor: 'var(--blue)', skills: sk })
    }
    return out
  }, [personal, groups, searchQuery, t])

  const hasAny = sections.length > 0
  const menuScale = clamp(Math.min(1, listWidth / MENU_BASE_LIST_WIDTH), MENU_MIN_SCALE, 1)
  const dropdownWidth = Math.round(MENU_BASE_WIDTH * menuScale)
  const dropdownIconSize = Math.round(14 * menuScale)
  const computeMenuPosition = useCallback((anchorEl) => {
    const anchorRect = anchorEl?.getBoundingClientRect()
    if (!anchorRect) return null

    const listRect = listRef.current?.getBoundingClientRect()
    const panelLeft = (listRect?.left ?? 0) + MENU_EDGE_GAP
    const panelRight = (listRect?.right ?? window.innerWidth) - MENU_EDGE_GAP
    const maxWidth = Math.max(160, panelRight - panelLeft)
    const width = Math.min(dropdownWidth, maxWidth)
    const maxLeft = Math.max(panelLeft, panelRight - width)
    const left = clamp(anchorRect.right - width, panelLeft, maxLeft)

    return {
      top: Math.round(anchorRect.bottom + MENU_EDGE_GAP),
      left: Math.round(left),
      width: Math.round(width),
    }
  }, [dropdownWidth])
  const updateMenuPosition = useCallback((key, ref) => {
    const next = computeMenuPosition(ref.current)
    if (!next) return
    setMenuPosition((prev) => ({ ...prev, [key]: next }))
  }, [computeMenuPosition])
  const positionedMenuStyle = useCallback((key) => {
    const position = menuPosition[key]
    return {
      ...menuStyle,
      top: position?.top ?? 0,
      left: position?.left ?? 0,
      width: position?.width ?? dropdownWidth,
      minWidth: 0,
      visibility: position ? 'visible' : 'hidden',
      padding: `${Math.round(4 * menuScale)}px 0`,
    }
  }, [dropdownWidth, menuPosition, menuScale])
  const scaledMenuItemStyle = {
    ...menuItemStyle,
    gap: Math.round(8 * menuScale),
    padding: `${Math.round(7 * menuScale)}px ${Math.round(12 * menuScale)}px`,
    fontSize: Number((13 * menuScale).toFixed(1)),
  }

  useLayoutEffect(() => {
    if (showSyncMenu) updateMenuPosition('sync', syncRef)
    if (showAddMenu) updateMenuPosition('add', addRef)
  }, [listWidth, showAddMenu, showSyncMenu, updateMenuPosition])

  useEffect(() => {
    if (!showAddMenu && !showSyncMenu) return
    const update = () => {
      if (showSyncMenu) updateMenuPosition('sync', syncRef)
      if (showAddMenu) updateMenuPosition('add', addRef)
    }
    window.addEventListener('resize', update)
    window.addEventListener('scroll', update, true)
    return () => {
      window.removeEventListener('resize', update)
      window.removeEventListener('scroll', update, true)
    }
  }, [showAddMenu, showSyncMenu, updateMenuPosition])

  const isSelected = (s) =>
    selectedSkill && selectedSkill.scope === s.scope && (selectedSkill.cwd || null) === (s.cwd || null) && selectedSkill.name === s.name

  const handleDelete = (s) => {
    showConfirmDialog({
      title: t('skills.deleteTitle'),
      message: t('skills.deleteMessage', { name: s.name }),
      confirmLabel: t('sidebar.delete'),
      danger: true,
      requireText: s.name,
      onConfirm: () => deleteSkill(s.scope, s.cwd, s.name),
    })
  }

  const seedCreateSession = (target) => {
    stopActiveStream()
    const chat = useChatStore.getState()
    chat.clearMessages()
    useTaskStore.getState().clearTasks?.()
    useUiStore.getState().hideCanvas?.()
    const sessionCwd = target.scope === 'workdir' ? target.cwd : (activeCwd || null)
    const promptPath = target.scope === 'workdir' ? target.cwd : '~/.claude/skills'
    if (sessionCwd) chat.setCwdDraft(sessionCwd)
    // Activate the skill-creator skill as a chip + prefill the starter prompt so
    // the user can refine it before sending (handleSend prepends "/skill-creator ").
    chat.setPendingComposerSend({
      skill: { name: 'skill-creator', level: 'project' },
      text: t('skills.createPrompt', { path: promptPath }),
    })
    setActiveNavTab('priva')
  }

  const handleDialogConfirm = (target) => {
    const mode = dialogMode
    setDialogMode(null)
    if (mode === 'create') {
      seedCreateSession(target)
    } else if (mode === 'upload') {
      uploadTargetRef.current = target
      fileInputRef.current?.click()
    }
  }

  const handleFileChange = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    const tgt = uploadTargetRef.current || { scope: 'personal', cwd: null }
    try {
      await uploadSkill(tgt.scope, tgt.cwd, file)
    } catch (err) {
      console.error('Skill upload failed:', err)
    }
    e.target.value = ''
  }

  return (
    <div ref={listRef} className="flex flex-col" style={{ height: '100%', minHeight: 0 }}>
      {/* Header */}
      <div className="flex-shrink-0" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        {/* Title + actions */}
        <div className="flex items-center justify-between px-3 pt-2 pb-1">
          <span className="uppercase font-semibold" style={{ color: 'var(--text-dim)', letterSpacing: '0.06em', fontSize: 11 }}>
            {t('tabs.skills')}
          </span>
          <div className="flex items-center" style={{ gap: 2 }}>
            <button style={iconBtn} onClick={fetchSkills} onMouseEnter={iconBtnIn} onMouseLeave={iconBtnOut} title={t('skills.refresh')}>
              <RefreshCw size={14} strokeWidth={1.5} style={{ animation: skillsLoading ? 'spin 1s linear infinite' : 'none' }} />
            </button>
            <div ref={syncRef} className="relative">
              <button style={iconBtn} onClick={() => { setShowSyncMenu((v) => !v); setShowAddMenu(false) }} onMouseEnter={iconBtnIn} onMouseLeave={iconBtnOut} title={t('skillSync.title')}>
                <ArrowDownUp size={14} strokeWidth={1.5} />
              </button>
              {showSyncMenu && (
                <div style={positionedMenuStyle('sync')}>
                  <button style={scaledMenuItemStyle} onMouseEnter={menuItemIn} onMouseLeave={menuItemOut} onClick={() => { setShowSyncMenu(false); openPushSync() }}>
                    <span className="truncate">{t('skillSync.pushToAgent')}</span>
                  </button>
                  <button style={scaledMenuItemStyle} onMouseEnter={menuItemIn} onMouseLeave={menuItemOut} onClick={() => { setShowSyncMenu(false); openPullSync() }}>
                    <span className="truncate">{t('skillSync.pullFromAgent')}</span>
                  </button>
                </div>
              )}
            </div>
            <div ref={addRef} className="relative">
              <button style={iconBtn} onClick={() => { setShowAddMenu((v) => !v); setShowSyncMenu(false) }} onMouseEnter={iconBtnIn} onMouseLeave={iconBtnOut} title={t('skills.add')}>
                <Plus size={16} strokeWidth={1.5} />
              </button>
              {showAddMenu && (
                <div style={positionedMenuStyle('add')}>
                  <button style={scaledMenuItemStyle} onMouseEnter={menuItemIn} onMouseLeave={menuItemOut} onClick={() => { setShowAddMenu(false); setDialogMode('create') }}>
                    <Sparkles size={dropdownIconSize} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--purple)' }} />
                    <span className="truncate">{t('skills.createWithAgent')}</span>
                  </button>
                  <button style={scaledMenuItemStyle} onMouseEnter={menuItemIn} onMouseLeave={menuItemOut} onClick={() => { setShowAddMenu(false); setDialogMode('upload') }}>
                    <Upload size={dropdownIconSize} strokeWidth={1.5} style={{ flexShrink: 0 }} />
                    <span className="truncate">{t('skills.uploadSkill')}</span>
                  </button>
                </div>
              )}
            </div>
          </div>
        </div>

        {/* Search */}
        <div className="px-3 pb-2">
          <div className="flex items-center gap-1 px-2 py-1" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4 }}>
            <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            <input
              className="flex-1"
              style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', minWidth: 0, fontSize: 13 }}
              placeholder={t('skills.search')}
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
            />
          </div>
        </div>

        {/* Skill Hub */}
        <div className="px-3 pb-2">
          <button
            className="flex items-center justify-center gap-2 w-full px-2 py-1"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 13, transition: 'color 150ms ease, border-color 150ms ease, background 150ms ease' }}
            onClick={openHub}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--blue)'; e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.background = 'transparent' }}
          >
            <Package size={14} strokeWidth={1.5} />
            <span>{t('skillHub.title')}</span>
          </button>
        </div>
      </div>

      <input ref={fileInputRef} type="file" accept={ACCEPT} style={{ display: 'none' }} onChange={handleFileChange} />

      {/* Grouped list */}
      <div className="flex-1 overflow-y-auto py-1" style={{ minHeight: 0 }}>
        {skillsLoading ? (
          <div className="flex flex-col gap-1 px-3">
            {[1, 2, 3, 4].map((i) => <div key={i} className="skeleton" style={{ height: 34, borderRadius: 2 }} />)}
          </div>
        ) : !hasAny ? (
          <div className="px-3 py-6" style={{ color: 'var(--text-dim)', textAlign: 'center', fontSize: 13 }}>
            {t('skills.noSkills')}
          </div>
        ) : (
          sections.map((sec) => {
            const open = !collapsedGroups[sec.key]
            const Icon = sec.icon
            return (
              <div key={sec.key}>
                <button
                  className="flex items-center gap-1 w-full px-2 py-1"
                  style={{ background: 'transparent', border: 'none', cursor: 'pointer', textAlign: 'left' }}
                  title={sec.title}
                  onClick={() => setCollapsedGroups((m) => ({ ...m, [sec.key]: open }))}
                >
                  <AnimatedChevron open={open} style={{ color: 'var(--text-dim)' }}>
                    <ChevronDown size={12} strokeWidth={1.5} />
                  </AnimatedChevron>
                  <Icon size={13} strokeWidth={1.5} style={{ flexShrink: 0, color: sec.iconColor }} />
                  <span className="uppercase font-semibold truncate flex-1" style={{ color: 'var(--text-dim)', letterSpacing: '0.05em', fontSize: 11 }}>
                    {sec.label}
                  </span>
                  <span style={{ flexShrink: 0, fontSize: 11, color: 'var(--text-dim)' }}>{sec.skills.length}</span>
                </button>
                <AnimatedCollapse open={open}>
                  <div>
                    {sec.skills.map((s) => (
                      <SkillRow
                        key={skillKey(s)}
                        skill={s}
                        selected={isSelected(s)}
                        detail={isSelected(s) ? skillDetail : null}
                        detailLoading={isSelected(s) && detailLoading}
                        selectedFile={selectedFile}
                        onSelect={() => selectSkill(s.scope, s.cwd, s.name)}
                        onSelectFile={selectFile}
                        onToggle={() => toggleSkill(s.name)}
                        onDownload={() => downloadSkill(s.scope, s.cwd, s.name)}
                        onDelete={() => handleDelete(s)}
                        canDelete={s.scope === 'workdir' || isAdmin}
                        t={t}
                      />
                    ))}
                  </div>
                </AnimatedCollapse>
              </div>
            )
          })
        )}
      </div>

      <CreateSkillDialog
        open={!!dialogMode}
        mode={dialogMode || 'create'}
        onConfirm={handleDialogConfirm}
        onCancel={() => setDialogMode(null)}
      />
    </div>
  )
}

function SkillRow({ skill, selected, detail, detailLoading, selectedFile, onSelect, onSelectFile, onToggle, onDownload, onDelete, canDelete, t }) {
  const [hovered, setHovered] = useState(false)
  const enabled = skill.enabled !== false
  return (
    <div>
      <div
        className="flex items-center gap-1 px-2 py-1"
        style={{
          background: selected ? 'var(--bg-elevated)' : 'transparent',
          borderLeft: selected ? '2px solid var(--blue)' : '2px solid transparent',
          cursor: 'pointer', transition: 'background 150ms ease',
          opacity: enabled ? 1 : 0.5,
        }}
        onClick={onSelect}
        onMouseEnter={(e) => { setHovered(true); if (!selected) e.currentTarget.style.background = 'var(--bg-elevated)' }}
        onMouseLeave={(e) => { setHovered(false); if (!selected) e.currentTarget.style.background = 'transparent' }}
      >
        <AnimatedChevron open={selected} style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
          <ChevronDown size={12} strokeWidth={1.5} />
        </AnimatedChevron>
        <span className="truncate flex-1 min-w-0" style={{ color: selected ? 'var(--text-primary)' : 'var(--text-secondary)', fontSize: 13 }}>{skill.name}</span>
        {hovered && (
          <>
            <button
              className="flex items-center justify-center flex-shrink-0"
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }}
              onClick={(e) => { e.stopPropagation(); onDownload() }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--blue)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              title={t('skills.download')}
            >
              <Download size={14} strokeWidth={1.5} />
            </button>
            {canDelete && (
              <button
                className="flex items-center justify-center flex-shrink-0"
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }}
                onClick={(e) => { e.stopPropagation(); onDelete() }}
                onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
                onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                title={t('skills.delete')}
              >
                <Trash2 size={14} strokeWidth={1.5} />
              </button>
            )}
          </>
        )}
        <button
          className="flex items-center justify-center flex-shrink-0"
          style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: enabled ? 'var(--green)' : 'var(--text-dim)', padding: 0 }}
          onClick={(e) => { e.stopPropagation(); onToggle() }}
          title={enabled ? t('skills.disableSkill') : t('skills.enableSkill')}
        >
          {enabled ? <ToggleRight size={20} strokeWidth={1.5} /> : <ToggleLeft size={20} strokeWidth={1.5} />}
        </button>
      </div>

      {/* Inline file tree for the selected skill */}
      <AnimatedCollapse open={selected} animateHeight={false}>
        <div style={{ paddingBottom: 4 }}>
          {detailLoading ? (
            <div className="flex flex-col gap-1 px-3 py-1">
              {[1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 20, borderRadius: 2 }} />)}
            </div>
          ) : detail?.tree?.length ? (
            detail.tree.map((node) => (
              <InlineTreeNode key={node.name} node={node} depth={0} parentPath="" selectedFile={selectedFile} onSelectFile={onSelectFile} />
            ))
          ) : null}
        </div>
      </AnimatedCollapse>
    </div>
  )
}

function InlineTreeNode({ node, depth, parentPath, selectedFile, onSelectFile }) {
  const [expanded, setExpanded] = useState(false)
  const path = parentPath ? `${parentPath}/${node.name}` : node.name
  const pad = 22 + depth * 14

  if (node.type === 'directory') {
    return (
      <>
        <button
          className="flex items-center gap-1 w-full pr-2 py-1"
          style={{ paddingLeft: pad, background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-secondary)', fontSize: 12, textAlign: 'left', transition: 'background 150ms ease' }}
          onClick={() => setExpanded((v) => !v)}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          <AnimatedChevron open={expanded} style={{ color: 'var(--text-dim)', flexShrink: 0 }}>
            <ChevronDown size={11} strokeWidth={1.5} />
          </AnimatedChevron>
          {expanded
            ? <FolderOpen size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
            : <Folder size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />}
          <span className="truncate">{node.name}</span>
        </button>
        <AnimatedCollapse open={expanded} animateHeight={false}>
          <div>
            {(node.children || []).map((c) => (
              <InlineTreeNode key={c.name} node={c} depth={depth + 1} parentPath={path} selectedFile={selectedFile} onSelectFile={onSelectFile} />
            ))}
          </div>
        </AnimatedCollapse>
      </>
    )
  }

  const active = selectedFile === path
  return (
    <button
      className="flex items-center gap-1 w-full pr-2 py-1"
      style={{
        paddingLeft: pad + 13,
        background: active ? 'var(--bg-elevated)' : 'transparent',
        border: '2px solid transparent',
        borderLeftColor: active ? 'var(--cyan)' : 'transparent',
        cursor: 'pointer', color: active ? 'var(--text-primary)' : 'var(--text-secondary)', fontSize: 12,
        textAlign: 'left', transition: 'background 150ms ease',
      }}
      onClick={() => onSelectFile(path)}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      <FileText size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
      <span className="truncate">{node.name}</span>
    </button>
  )
}
