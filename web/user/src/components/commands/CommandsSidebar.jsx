import { useEffect } from 'react'
import { ArrowLeft, Plus, UsersRound, FolderGit2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useCommandsStore from '../../stores/commandsStore'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

const groupLabel = {
  color: 'var(--text-dim)', fontSize: 11, fontWeight: 600,
  letterSpacing: '0.06em', textTransform: 'uppercase', padding: '8px 10px 4px',
}

function CommandItem({ cmd, active, onSelect }) {
  return (
    <button
      onClick={onSelect}
      className="flex flex-col text-left"
      style={{
        padding: '6px 10px', width: '100%',
        background: active ? 'var(--bg-elevated)' : 'transparent',
        borderLeft: active ? '2px solid var(--blue)' : '2px solid transparent',
        borderRadius: 4, cursor: 'pointer', transition: 'background 150ms ease',
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      <span className="truncate" style={{ color: 'var(--text-primary)', fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>
        /{cmd.name}
      </span>
      {cmd.description && (
        <span className="truncate" style={{ color: 'var(--text-dim)', fontSize: 11 }}>{cmd.description}</span>
      )}
    </button>
  )
}

export default function CommandsSidebar({ backTitle, onBack }) {
  const { t } = useTranslation()
  const list = useCommandsStore((s) => s.list)
  const listLoading = useCommandsStore((s) => s.listLoading)
  const selectedName = useCommandsStore((s) => s.selectedName)
  const selectedScope = useCommandsStore((s) => s.selectedScope)
  const selectedCwd = useCommandsStore((s) => s.selectedCwd)
  const loadList = useCommandsStore((s) => s.loadList)
  const selectCommand = useCommandsStore((s) => s.selectCommand)
  const openScopePicker = useCommandsStore((s) => s.openScopePicker)

  useEffect(() => { loadList() }, [loadList])

  const userItems = list.filter((c) => c.scope === 'user')
  const projCwds = []
  const seen = new Set()
  for (const c of list) {
    if (c.scope === 'project' && c.cwd && !seen.has(c.cwd)) { seen.add(c.cwd); projCwds.push(c.cwd) }
  }
  const isActive = (c) => c.name === selectedName && c.scope === selectedScope && (c.cwd || null) === (selectedCwd || null)

  return (
    <div className="flex flex-col h-full" style={{ minHeight: 0 }}>
      {/* Header */}
      <div className="flex items-center flex-shrink-0" style={{ gap: 10, height: 44, padding: '0 12px', borderBottom: '1px solid var(--border-subtle)' }}>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center justify-center flex-shrink-0"
          aria-label={backTitle}
          title={backTitle}
          style={{ width: 28, height: 28, padding: 0, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer', transition: 'color 150ms ease, background 150ms ease' }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-primary)'; e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.background = 'transparent' }}
        >
          <ArrowLeft size={16} strokeWidth={1.5} />
        </button>
        <span className="truncate font-bold flex-1" style={{ color: 'var(--text-primary)', fontSize: 16 }}>
          {t('tabs.commands')}
        </span>
        <button
          onClick={() => openScopePicker()}
          className="inline-flex items-center justify-center flex-shrink-0"
          title={t('commands.new')}
          style={{ width: 28, height: 28, background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer', transition: 'all 150ms ease' }}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--blue)'; e.currentTarget.style.borderColor = 'var(--blue)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-secondary)'; e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <Plus size={16} strokeWidth={1.5} />
        </button>
      </div>

      {/* List */}
      <div className="flex flex-col overflow-y-auto" style={{ padding: 6, minHeight: 0 }}>
        {listLoading && list.length === 0 && (
          [1, 2, 3].map((i) => <div key={i} className="skeleton" style={{ height: 36, borderRadius: 2, margin: 4 }} />)
        )}
        {!listLoading && list.length === 0 && (
          <div style={{ color: 'var(--text-dim)', fontSize: 12, padding: 12 }}>{t('commands.empty')}</div>
        )}

        {userItems.length > 0 && (
          <>
            <div className="flex items-center gap-1" style={groupLabel}>
              <UsersRound size={12} strokeWidth={1.5} style={{ color: 'var(--green)' }} />
              {t('commands.user')}
            </div>
            {userItems.map((c) => (
              <CommandItem key={`u:${c.name}`} cmd={c} active={isActive(c)}
                onSelect={() => selectCommand('user', null, c.name)} />
            ))}
          </>
        )}

        {projCwds.map((cwd) => (
          <div key={`g:${cwd}`}>
            <div className="flex items-center gap-1" style={groupLabel} title={cwd}>
              <FolderGit2 size={12} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />
              <span className="truncate">{shortCwd(cwd)}</span>
            </div>
            {list.filter((c) => c.scope === 'project' && c.cwd === cwd).map((c) => (
              <CommandItem key={`p:${cwd}:${c.name}`} cmd={c} active={isActive(c)}
                onSelect={() => selectCommand('project', cwd, c.name)} />
            ))}
          </div>
        ))}
      </div>
    </div>
  )
}
