import { useEffect, useState } from 'react'
import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { createDirectory } from '../../api/userFiles'
import useOverlayTransition from '@shared/motion/useOverlayTransition'

export default function CreateDirectoryDialog({ open, parentPath, onCreated, onCancel }) {
  const { t } = useTranslation()
  const [name, setName] = useState('')
  const [error, setError] = useState(null)
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) return
    setName('')
    setError(null)
    setSubmitting(false)
  }, [open, parentPath])

  const submit = async (event) => {
    event.preventDefault()
    const trimmedName = name.trim()
    if (!trimmedName) {
      setError(t('picker.folderNameRequired'))
      return
    }

    setSubmitting(true)
    setError(null)
    try {
      const created = await createDirectory(parentPath, trimmedName)
      onCreated?.(created)
    } catch (requestError) {
      setError(requestError?.message || String(requestError))
      setSubmitting(false)
    }
  }

  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'scale' })
  if (!mounted) return null

  const dismiss = () => {
    if (!submitting) onCancel?.()
  }

  const modal = (
    <div
      ref={backdropRef}
      className="fixed inset-0 flex items-center justify-center"
      style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', padding: 16, zIndex: 1100 }}
      onMouseDown={(event) => { if (event.target === event.currentTarget) dismiss() }}
    >
      <form
        ref={panelRef}
        className="flex flex-col"
        role="dialog"
        aria-modal="true"
        aria-labelledby="create-directory-title"
        onSubmit={submit}
        style={{
          width: 'min(420px, 100%)',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-strong)',
          borderRadius: 4,
        }}
      >
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <span id="create-directory-title" className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14 }}>
            {t('picker.newFolderTitle')}
          </span>
          <button
            type="button"
            onClick={dismiss}
            disabled={submitting}
            aria-label={t('picker.cancel')}
            style={{
              background: 'transparent', border: 'none', padding: 2,
              color: 'var(--text-dim)', cursor: submitting ? 'not-allowed' : 'pointer',
              display: 'flex', transition: 'color 150ms ease',
            }}
            onMouseEnter={(event) => { if (!submitting) event.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(event) => { event.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>

        <div className="flex flex-col gap-2 px-4 py-4">
          <span className="text-xs" style={{ color: 'var(--text-secondary)', wordBreak: 'break-word', overflowWrap: 'break-word' }}>
            {t('picker.createIn', { path: parentPath || '/' })}
          </span>
          <input
            autoFocus
            type="text"
            value={name}
            onChange={(event) => { setName(event.target.value); setError(null) }}
            placeholder={t('picker.folderNamePlaceholder')}
            disabled={submitting}
            style={{
              width: '100%', boxSizing: 'border-box', padding: '7px 8px',
              background: 'var(--bg-elevated)', border: `1px solid ${error ? 'var(--red)' : 'var(--border)'}`,
              borderRadius: 4, color: 'var(--text-primary)', fontSize: 13,
              fontFamily: 'var(--font-ui)', outline: 'none',
            }}
          />
          {error && (
            <span className="text-xs" role="alert" style={{ color: 'var(--red)', wordBreak: 'break-word', overflowWrap: 'break-word' }}>
              {error}
            </span>
          )}
        </div>

        <div className="flex items-center justify-end gap-2 px-4 py-3" style={{ borderTop: '1px solid var(--border)' }}>
          <button
            type="button"
            onClick={dismiss}
            disabled={submitting}
            className="px-3 py-1"
            style={{
              background: 'transparent', border: '1px solid var(--border)', borderRadius: 4,
              color: 'var(--text-secondary)', cursor: submitting ? 'not-allowed' : 'pointer', fontSize: 12,
            }}
          >
            {t('picker.cancel')}
          </button>
          <button
            type="submit"
            disabled={submitting}
            className="px-3 py-1"
            style={{
              background: submitting ? 'var(--bg-elevated)' : 'var(--blue)', border: 'none', borderRadius: 4,
              color: submitting ? 'var(--text-dim)' : 'var(--text-inverse)',
              cursor: submitting ? 'not-allowed' : 'pointer', fontSize: 12, fontWeight: 600,
            }}
          >
            {t('picker.create')}
          </button>
        </div>
      </form>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}
