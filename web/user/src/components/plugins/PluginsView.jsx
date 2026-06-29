import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import SkillsPanel from '../skills/SkillsPanel'

// Content-only "Plugins/Customize" view. Section navigation lives in the
// persistent sidebar (Plugins submenu); this renders just the active section's
// body. Skills is live; the other four are placeholders until each is redesigned
// one by one (Phase 2, incremental).
function Placeholder({ labelKey }) {
  const { t } = useTranslation()
  return (
    <div
      className="flex flex-1 items-center justify-center"
      style={{ background: 'var(--bg-base)', color: 'var(--text-dim)', fontSize: 13, minHeight: 0, minWidth: 0 }}
    >
      {t('plugins.comingSoon', { name: t(labelKey) })}
    </div>
  )
}

export default function PluginsView() {
  const section = useUiStore((s) => s.activePluginSection)

  return (
    <div
      className="flex flex-1"
      style={{ background: 'var(--bg-base)', minHeight: 0, minWidth: 0, overflow: 'hidden' }}
    >
      {section === 'skills' && <SkillsPanel />}
      {section === 'mcp' && <Placeholder labelKey="tabs.mcp" />}
      {section === 'hooks' && <Placeholder labelKey="tabs.hooks" />}
      {section === 'subagents' && <Placeholder labelKey="tabs.subagents" />}
      {section === 'memory' && <Placeholder labelKey="tabs.memory" />}
    </div>
  )
}
