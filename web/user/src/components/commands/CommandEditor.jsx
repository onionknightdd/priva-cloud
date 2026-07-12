import { Trash2, Loader, UsersRound, FolderGit2 } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useCommandsStore from '../../stores/commandsStore'
import useUiStore from '@shared/stores/uiStore'
import ScriptEditor from '../shared/ScriptEditor'

function shortCwd(p) {
  if (!p) return '~'
  const parts = String(p).split('/').filter(Boolean)
  return parts.length ? parts[parts.length - 1] : p
}

const labelStyle = {
  fontSize: 11, color: 'var(--text-dim)', fontWeight: 600,
  letterSpacing: '0.06em', textTransform: 'uppercase', marginBottom: 4,
}
const inputStyle = {
  background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4,
  color: 'var(--text-primary)', padding: '6px 8px', fontSize: 12,
  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace", outline: 'none',
  width: '100%', boxSizing: 'border-box',
}

function Field({ label, children }) {
  return (
    <div className="flex flex-col gap-1">
      <div style={labelStyle}>{label}</div>
      {children}
    </div>
  )
}

export default function CommandEditor() {
  const { t } = useTranslation()
  const draft = useCommandsStore((s) => s.formDraft)
  const dirty = useCommandsStore((s) => s.dirty)
  const saving = useCommandsStore((s) => s.saving)
  const error = useCommandsStore((s) => s.error)
  const setFormField = useCommandsStore((s) => s.setFormField)
  const saveDraft = useCommandsStore((s) => s.saveDraft)
  const discardDraft = useCommandsStore((s) => s.discardDraft)
  const deleteSelected = useCommandsStore((s) => s.deleteSelected)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)

  if (!draft) return null

  const isEdit = draft.__mode === 'edit'
  const canSave = draft.name.trim().length > 0 && draft.prompt.trim().length > 0
  const ScopeIcon = draft.scope === 'user' ? UsersRound : FolderGit2
  const scopeLabel = draft.scope === 'user' ? t('commands.user') : shortCwd(draft.cwd)

  const onFocus = (e) => { e.target.style.borderColor = 'var(--border-strong)' }
  const onBlur = (e) => { e.target.style.borderColor = 'var(--border)' }

  const handleDelete = () => {
    showConfirmDialog({
      title: t('commands.deleteTitle'),
      message: t('commands.deleteMessage', { name: draft.name }),
      confirmLabel: t('commands.deleteConfirm'),
      danger: true,
      onConfirm: deleteSelected,
    })
  }

  return (
    <div className="flex flex-col h-full" style={{ minHeight: 0 }}>
      {/* Header: scope badge + actions */}
      <div className="flex items-center gap-2 flex-shrink-0" style={{ height: 44, padding: '0 16px', borderBottom: '1px solid var(--border-subtle)' }}>
        <ScopeIcon size={14} strokeWidth={1.5} style={{ color: draft.scope === 'user' ? 'var(--green)' : 'var(--blue)', flexShrink: 0 }} />
        <span className="truncate" style={{ color: 'var(--text-secondary)', fontSize: 12 }}>{scopeLabel}</span>
        <span className="flex-1" />
        {error && <span style={{ color: 'var(--red)', fontSize: 11 }}>{error}</span>}
        {isEdit && (
          <button
            onClick={handleDelete}
            className="inline-flex items-center justify-center"
            title={t('commands.delete')}
            style={{ width: 28, height: 28, background: 'transparent', border: 'none', borderRadius: 4, color: 'var(--text-dim)', cursor: 'pointer', transition: 'color 150ms ease' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <Trash2 size={14} strokeWidth={1.5} />
          </button>
        )}
        {dirty && (
          <button
            onClick={discardDraft}
            className="px-3 py-1 text-xs"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer' }}
          >
            {t('commands.discard')}
          </button>
        )}
        <button
          onClick={saveDraft}
          disabled={!canSave || saving || !dirty}
          className="px-3 py-1 text-xs font-semibold uppercase"
          style={{
            background: canSave && dirty && !saving ? 'var(--blue)' : 'var(--bg-surface)',
            border: canSave && dirty && !saving ? 'none' : '1px solid var(--border)',
            borderRadius: 4, color: canSave && dirty && !saving ? 'var(--text-inverse)' : 'var(--text-dim)',
            cursor: canSave && dirty && !saving ? 'pointer' : 'not-allowed',
            opacity: canSave && dirty && !saving ? 1 : 0.6, letterSpacing: '0.06em', transition: 'all 150ms ease',
          }}
        >
          {saving ? <Loader size={12} strokeWidth={1.5} style={{ animation: 'spin 1s linear infinite', display: 'inline-block' }} /> : t('commands.save')}
        </button>
      </div>

      {/* Body */}
      <div className="flex flex-col gap-3 overflow-y-auto" style={{ padding: 16, minHeight: 0 }}>
        <Field label={t('commands.name')}>
          <div className="flex items-center gap-2">
            <span style={{ color: 'var(--text-dim)', fontSize: 13, fontFamily: "'JetBrains Mono', monospace" }}>/</span>
            <input
              style={inputStyle}
              value={draft.name}
              onChange={(e) => setFormField('name', e.target.value)}
              placeholder={t('commands.namePlaceholder')}
              onFocus={onFocus} onBlur={onBlur}
            />
          </div>
        </Field>

        <Field label={t('commands.description')}>
          <input
            style={inputStyle}
            value={draft.description}
            onChange={(e) => setFormField('description', e.target.value)}
            placeholder={t('commands.descriptionPlaceholder')}
            onFocus={onFocus} onBlur={onBlur}
          />
        </Field>

        <div className="flex gap-3">
          <div className="flex-1 min-w-0">
            <Field label={t('commands.argumentHint')}>
              <input
                style={inputStyle}
                value={draft.argumentHint}
                onChange={(e) => setFormField('argumentHint', e.target.value)}
                placeholder="[env] [target]"
                onFocus={onFocus} onBlur={onBlur}
              />
            </Field>
          </div>
          <div className="flex-1 min-w-0">
            <Field label={t('commands.model')}>
              <input
                style={inputStyle}
                value={draft.model}
                onChange={(e) => setFormField('model', e.target.value)}
                placeholder={t('commands.modelPlaceholder')}
                onFocus={onFocus} onBlur={onBlur}
              />
            </Field>
          </div>
        </div>

        <Field label={t('commands.allowedTools')}>
          <input
            style={inputStyle}
            value={draft.allowedToolsText}
            onChange={(e) => setFormField('allowedToolsText', e.target.value)}
            placeholder="Bash(git add:*), Bash(git status:*), Read"
            onFocus={onFocus} onBlur={onBlur}
          />
        </Field>

        <Field label={t('commands.prompt')}>
          <ScriptEditor
            value={draft.prompt}
            onChange={(v) => setFormField('prompt', v)}
            language="markdown"
            placeholder={t('commands.promptPlaceholder')}
            minHeight={220}
            maxHeight={100000}
          />
        </Field>
      </div>
    </div>
  )
}
