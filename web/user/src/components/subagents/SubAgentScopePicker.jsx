import { useState } from 'react'
import { createPortal } from 'react-dom'
import { X, FolderGit2, UsersRound, FolderOpen, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useSubagentsStore from '../../stores/subagentsStore'
import useSidebarStore from '../../stores/sidebarStore'
import DirectoryPicker from '../shared/DirectoryPicker'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

// Scope chooser for "New subagent" — mirrors the MCP add-server scope picker.
// USER (~/.claude/agents, every user has one) at the top, then one row per existing
// project (workdir, from the sidebar's cwd groups) writing {cwd}/.claude/agents,
// plus a Browse… escape hatch. Picking a scope opens the editor via chooseScope,
// which seeds the pending template (if any) at that scope.
export default function SubAgentScopePicker() {
  const { t } = useTranslation()
  const open = useSubagentsStore((s) => s.scopePickerOpen)
  const closeScopePicker = useSubagentsStore((s) => s.closeScopePicker)
  const chooseScope = useSubagentsStore((s) => s.chooseScope)
  const groups = useSidebarStore((s) => s.groups)
  const activeCwd = useSidebarStore((s) => s.activeCwd)
  const [browseOpen, setBrowseOpen] = useState(false)

  if (!open) return null

  // Distinct workdir list: active cwd first, then sidebar groups (same source MCP/Skills use).
  const cwds = []
  const seen = new Set()
  for (const c of [activeCwd, ...groups.map((g) => g.cwd)]) {
    if (c && !seen.has(c)) { seen.add(c); cwds.push(c) }
  }

  const rowStyle = {
    display: 'flex', alignItems: 'center', gap: 10, width: '100%',
    padding: '5px 10px', background: 'transparent', border: '1px solid transparent',
    borderRadius: 4, cursor: 'pointer', textAlign: 'left',
    transition: 'background 150ms ease, border-color 150ms ease',
  }
  const onIn = (e) => { e.currentTarget.style.background = 'var(--bg-elevated)'; e.currentTarget.style.borderColor = 'var(--border)' }
  const onOut = (e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.borderColor = 'transparent' }
  const subtitle = { color: 'var(--text-dim)', fontSize: 11, fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace" }

  return createPortal(
    <>
      <div
        className="fixed inset-0 flex items-center justify-center"
        style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 1000, padding: 16 }}
        onMouseDown={(e) => { if (e.target === e.currentTarget) closeScopePicker() }}
      >
        <div
          className="flex flex-col"
          style={{
            width: 440, maxWidth: '100%', maxHeight: '80vh',
            background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4,
            animation: 'dialog-scale-in 200ms cubic-bezier(0.16, 1, 0.3, 1)',
          }}
        >
          {/* Header */}
          <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            <span className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14 }}>
              {t('subagents.newScopeTitle')}
            </span>
            <button
              onClick={closeScopePicker}
              style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2, display: 'flex' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              title={t('subagents.cancel')}
            >
              <X size={16} strokeWidth={1.5} />
            </button>
          </div>

          {/* Prompt */}
          <div className="px-4 pt-3 pb-1" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>
            {t('subagents.addScopePrompt')}
          </div>

          {/* Options */}
          <div className="flex flex-col gap-0.5 px-3 py-2 overflow-y-auto" style={{ minHeight: 0 }}>
            {/* User scope — ~/.claude/agents (available to every user) */}
            <button style={rowStyle} onMouseEnter={onIn} onMouseLeave={onOut}
              onClick={() => chooseScope('user', null)}>
              <UsersRound size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--green)' }} />
              <div className="flex flex-col min-w-0 flex-1">
                <span className="uppercase font-semibold truncate" style={{ color: 'var(--text-primary)', fontSize: 12, letterSpacing: '0.04em' }}>
                  {t('subagents.user')}
                </span>
                <span className="truncate" style={subtitle}>~/.claude/agents</span>
              </div>
              <ChevronRight size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
            </button>

            {/* Existing projects (workdirs) → {cwd}/.claude/agents */}
            {cwds.map((cwd) => (
              <button key={cwd} style={rowStyle} onMouseEnter={onIn} onMouseLeave={onOut}
                onClick={() => chooseScope('project', cwd)} title={cwd}>
                <FolderGit2 size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--blue)' }} />
                <div className="flex flex-col min-w-0 flex-1">
                  <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 12 }}>{shortCwd(cwd)}</span>
                  <span className="truncate" style={subtitle}>{cwd}</span>
                </div>
                <ChevronRight size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
              </button>
            ))}

            {/* Browse to any directory */}
            <button style={{ ...rowStyle, marginTop: 4, borderTop: '1px solid var(--border-subtle)', borderRadius: 0 }}
              onMouseEnter={onIn} onMouseLeave={onOut} onClick={() => setBrowseOpen(true)}>
              <FolderOpen size={16} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-secondary)' }} />
              <span className="flex-1 truncate" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{t('subagents.browse')}</span>
              <ChevronRight size={14} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
            </button>
          </div>
        </div>
      </div>

      <DirectoryPicker
        open={browseOpen}
        multiple={false}
        title={t('picker.cwdTitle')}
        initialPath={activeCwd || '/'}
        onConfirm={(path) => { setBrowseOpen(false); chooseScope('project', path) }}
        onCancel={() => setBrowseOpen(false)}
      />

      <style>{`
        @keyframes dialog-scale-in {
          from { transform: scale(0.95); opacity: 0; }
          to { transform: scale(1); opacity: 1; }
        }
      `}</style>
    </>,
    document.body,
  )
}
