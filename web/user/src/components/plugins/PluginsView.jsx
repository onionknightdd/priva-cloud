import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import SkillsPanel from '../skills/SkillsPanel'
import MCPPanel from '../mcp/MCPPanel'
import HooksPanel from '../hooks/HooksPanel'
import SubAgentsPanel from '../subagents/SubAgentsPanel'

// Content-only "Plugins/Customize" view. Section navigation lives in the
// persistent sidebar (Plugins submenu); this renders just the active section's
// body. Skills + MCP + Hooks + SubAgents are live; the rest are placeholders
// until each is redesigned one by one (Phase 2, incremental).
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

export default function PluginsView({ backTitle, onBack }) {
  const section = useUiStore((s) => s.activePluginSection)

  return (
    <div
      className="flex flex-1"
      style={{ background: 'var(--bg-base)', minHeight: 0, minWidth: 0, overflow: 'hidden' }}
    >
      {section === 'skills' && <SkillsPanel backTitle={backTitle} onBack={onBack} />}
      {section === 'mcp' && <MCPPanel backTitle={backTitle} onBack={onBack} />}
      {section === 'hooks' && <HooksPanel backTitle={backTitle} onBack={onBack} />}
      {section === 'subagents' && <SubAgentsPanel backTitle={backTitle} onBack={onBack} />}
      {section === 'memory' && <Placeholder labelKey="tabs.memory" />}
    </div>
  )
}
