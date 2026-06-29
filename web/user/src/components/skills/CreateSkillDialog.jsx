import { useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { X, NotebookPen, FolderGit2, FolderOpen, ChevronRight } from 'lucide-react'
import useSidebarStore from '../../stores/sidebarStore'
import DirectoryPicker from '../shared/DirectoryPicker'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

/**
 * Target picker for "Create Skill with Agent" / "Upload a skill". Lets the user
 * pick Personal (~/.claude/skills) or one of their workdirs (from the sidebar's
 * cwd groups), with a Browse… escape hatch to any directory. Calls
 * onConfirm({ scope, cwd }) — scope 'personal' (cwd null) or 'workdir' (cwd path).
 */
export default function CreateSkillDialog({ open, mode = 'create', onConfirm, onCancel }) {
  const { t } = useTranslation()
  const groups = useSidebarStore((s) => s.groups)
  const activeCwd = useSidebarStore((s) => s.activeCwd)
  const [browseOpen, setBrowseOpen] = useState(false)

  if (!open) return null

  // Distinct workdir list: active cwd first, then sidebar groups.
  const cwds = []
  const seen = new Set()
  for (const c of [activeCwd, ...groups.map((g) => g.cwd)]) {
    if (c && !seen.has(c)) { seen.add(c); cwds.push(c) }
  }

  const rowStyle = {
    display: 'flex', alignItems: 'center', gap: 10, width: '100%',
    padding: '8px 10px', background: 'transparent', border: '1px solid transparent',
    borderRadius: 4, cursor: 'pointer', textAlign: 'left',
    transition: 'background 150ms ease, border-color 150ms ease',
  }
  const onIn = (e) => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.borderColor = 'var(--border)' }
  const onOut = (e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'transparent' }

  return createPortal(
    <>
      <div
        className="fixed inset-0 flex items-center justify-center"
        style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(2px)', zIndex: 1000, padding: 16 }}
        onMouseDown={(e) => { if (e.target === e.currentTarget) onCancel?.() }}
      >
        <div
          className="flex flex-col"
          style={{
            width: 440, maxWidth: '100%', maxHeight: '80vh',
            background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4,
            animation: 'none',
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <span className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14 }}>
              {mode === 'upload' ? t('skills.uploadSkill') : t('skills.createTitle')}
            </span>
            <button
              onClick={onCancel}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2, display: 'flex' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              title={t('picker.cancel')}
            >
              <X size={16} strokeWidth={1.5} />
            </button>
          </div>

          {/* Prompt */}
          <div className="px-4 pt-3 pb-1" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {t('skills.createTarget')}
          </div>

          {/* Options */}
          <div className="flex flex-col gap-1 px-3 py-2 overflow-y-auto" style={{ minHeight: 0 }}>
            {/* Personal */}
            <button style={rowStyle} onMouseEnter={onIn} onMouseLeave={onOut}
              onClick={() => onConfirm?.({ scope: 'personal', cwd: null })}>
              <NotebookPen size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--purple)' }} />
              <div className="flex flex-col min-w-0">
                <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 13 }}>{t('skills.personal')}</span>
                <span className="truncate" style={{ color: 'var(--text-dim)', fontSize: 11 }}>~/.claude/skills</span>
              </div>
            </button>

            {/* Workdirs */}
            {cwds.map((cwd) => (
              <button key={cwd} style={rowStyle} onMouseEnter={onIn} onMouseLeave={onOut}
                onClick={() => onConfirm?.({ scope: 'workdir', cwd })} title={cwd}>
                <FolderGit2 size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--blue)' }} />
                <div className="flex flex-col min-w-0">
                  <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 13 }}>{shortCwd(cwd)}</span>
                  <span className="truncate" style={{ color: 'var(--text-dim)', fontSize: 11 }}>{cwd}</span>
                </div>
              </button>
            ))}

            {/* Browse */}
            <button style={{ ...rowStyle, marginTop: 4, borderTop: '1px solid var(--border-subtle)', borderRadius: 0 }}
              onMouseEnter={onIn} onMouseLeave={onOut} onClick={() => setBrowseOpen(true)}>
              <FolderOpen size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-secondary)' }} />
              <span className="flex-1 truncate" style={{ color: 'var(--text-secondary)', fontSize: 13 }}>{t('skills.browse')}</span>
              <ChevronRight size={14} strokeWidth={1.5} style={{ color: 'var(--text-dim)' }} />
            </button>
          </div>
        </div>
      </div>

      <DirectoryPicker
        open={browseOpen}
        multiple={false}
        title={t('picker.cwdTitle')}
        initialPath={activeCwd || '/'}
        onConfirm={(path) => { setBrowseOpen(false); onConfirm?.({ scope: 'workdir', cwd: path }) }}
        onCancel={() => setBrowseOpen(false)}
      />
    </>,
    document.body,
  )
}
