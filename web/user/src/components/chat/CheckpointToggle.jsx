import { useTranslation } from 'react-i18next'
import { Zap } from 'lucide-react'
import useChatStore from '../../stores/chatStore'

export default function CheckpointToggle() {
  const { t } = useTranslation()
  const enabled = useChatStore((s) => s.enableFileCheckpointing)
  const setEnabled = useChatStore((s) => s.setCheckpointingEnabled)
  const isStreaming = useChatStore((s) => s.isStreaming)

  const disabled = isStreaming

  return (
    <button
      type="button"
      aria-pressed={enabled}
      disabled={disabled}
      onClick={() => setEnabled(!enabled)}
      className="inline-flex items-center gap-1 flex-shrink-0"
      title={t('checkpoint.tooltip')}
      style={{
        height: 28,
        padding: '0 6px',
        background: 'transparent',
        border: '1px solid transparent',
        borderRadius: 3,
        color: enabled ? 'var(--blue)' : 'var(--text-dim)',
        fontSize: 12,
        fontWeight: 600,
        cursor: disabled ? 'not-allowed' : 'pointer',
        opacity: disabled ? 0.45 : 1,
        transition: 'color 150ms ease, background 150ms ease',
      }}
      onMouseEnter={(event) => { if (!disabled) event.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(event) => { event.currentTarget.style.background = 'transparent' }}
    >
      <Zap size={12} strokeWidth={1.5} fill={enabled ? 'currentColor' : 'none'} />
      <span>{enabled ? t('checkpoint.labelOn') : t('checkpoint.labelOff')}</span>
    </button>
  )
}
