import { Globe } from 'lucide-react'
import useUiStore from '../../stores/uiStore'

export default function LanguageToggleButton() {
  const language = useUiStore((s) => s.language)
  const toggleLanguage = useUiStore((s) => s.toggleLanguage)

  return (
    <button
      className="flex items-center gap-1 text-xs"
      onClick={toggleLanguage}
      style={{
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        padding: 4,
        color: 'var(--text-dim)',
        transition: 'color 150ms ease',
      }}
      onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
      onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
    >
      <Globe size={14} strokeWidth={1.5} />
      <span>{language.toUpperCase()}</span>
    </button>
  )
}
