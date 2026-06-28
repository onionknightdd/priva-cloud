import { useEffect, useRef } from 'react'
import { Key, Cpu, Zap, Settings2, Radio, FileText, Sun, Moon, Globe, Lightbulb } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import { getBackendOrigin } from '@shared/api/client'

const ITEMS = [
  { id: 'api', labelKey: 'settings.apiKey', icon: Key },
  { id: 'models', labelKey: 'settings.llmProvider', icon: Cpu },
  { id: 'quickactions', labelKey: 'settings.quickActions', icon: Zap },
  { id: 'advanced', labelKey: 'settings.advanced', icon: Settings2 },
]

export default function SettingsPopover() {
  const { t } = useTranslation()
  const open = useUiStore((s) => s.settingsPopoverOpen)
  const closePopover = useUiStore((s) => s.closeSettingsPopover)
  const openSettings = useUiStore((s) => s.openSettings)
  const theme = useUiStore((s) => s.theme)
  const toggleTheme = useUiStore((s) => s.toggleTheme)
  const language = useUiStore((s) => s.language)
  const toggleLanguage = useUiStore((s) => s.toggleLanguage)
  const openIntro = useUiStore((s) => s.openIntro)
  const ref = useRef(null)

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) {
        closePopover()
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open, closePopover])

  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (e.key === 'Escape') { e.preventDefault(); closePopover() }
    }
    document.addEventListener('keydown', handler)
    return () => document.removeEventListener('keydown', handler)
  }, [open, closePopover])

  if (!open) return null

  return (
    <div
      ref={ref}
      className="absolute flex flex-col"
      style={{
        bottom: '100%',
        left: 0,
        marginBottom: 4,
        width: 200,
        background: 'var(--bg-elevated)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        zIndex: 50,
        overflow: 'hidden',
        animation: 'scale-in 200ms cubic-bezier(0.16, 1, 0.3, 1)',
      }}
    >
      {ITEMS.map((item) => (
        <button
          key={item.id}
          className="flex items-center gap-2 px-3 py-2 w-full text-sm"
          style={{
            background: 'transparent',
            border: 'none',
            color: 'var(--text-secondary)',
            cursor: 'pointer',
            fontSize: 13,
            textAlign: 'left',
            transition: 'background 150ms ease, color 150ms ease',
          }}
          onClick={() => openSettings(item.id)}
          onMouseEnter={(e) => {
            e.currentTarget.style.background = 'var(--bg-surface)'
            e.currentTarget.style.color = 'var(--text-primary)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.background = 'transparent'
            e.currentTarget.style.color = 'var(--text-secondary)'
          }}
        >
          <item.icon size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
          {t(item.labelKey)}
        </button>
      ))}
      {/* Separator */}
      <div style={{ height: 1, background: 'var(--border)', margin: '2px 8px' }} />
      {/* API Doc — opens /sandbox/apidocs in new tab */}
      <button
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: 'transparent',
          border: 'none',
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontSize: 13,
          textAlign: 'left',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={() => {
          window.open(`${getBackendOrigin()}/sandbox/apidocs`, '_blank', 'noopener,noreferrer')
          closePopover()
        }}
        onMouseEnter={(e) => {
          e.currentTarget.style.background = 'var(--bg-surface)'
          e.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.background = 'transparent'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        <FileText size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        {t('settings.apiDoc')}
      </button>

      {/* Separator */}
      <div style={{ height: 1, background: 'var(--border)', margin: '2px 8px' }} />

      {/* Theme toggle */}
      <button
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: 'transparent', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontSize: 13, textAlign: 'left',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={toggleTheme}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)'; e.currentTarget.style.color = 'var(--text-primary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' }}
      >
        {theme === 'dark'
          ? <Sun size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
          : <Moon size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />}
        <span className="flex-1">{t('settings.theme')}</span>
        <span className="uppercase" style={{ color: 'var(--text-dim)', fontSize: 11, letterSpacing: '0.06em' }}>
          {theme === 'dark' ? t('settings.themeDark') : t('settings.themeLight')}
        </span>
      </button>

      {/* Language toggle */}
      <button
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: 'transparent', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontSize: 13, textAlign: 'left',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={toggleLanguage}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)'; e.currentTarget.style.color = 'var(--text-primary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' }}
      >
        <Globe size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        <span className="flex-1">{t('settings.language')}</span>
        <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>
          {language === 'zh' ? '中文' : 'EN'}
        </span>
      </button>

      {/* Intro / help */}
      <button
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: 'transparent', border: 'none', color: 'var(--text-secondary)',
          cursor: 'pointer', fontSize: 13, textAlign: 'left',
          transition: 'background 150ms ease, color 150ms ease',
        }}
        onClick={() => { openIntro(); closePopover() }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)'; e.currentTarget.style.color = 'var(--text-primary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent'; e.currentTarget.style.color = 'var(--text-secondary)' }}
      >
        <Lightbulb size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        {t('settings.intro')}
      </button>
    </div>
  )
}
