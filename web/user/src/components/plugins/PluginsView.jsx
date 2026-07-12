import useUiStore from '@shared/stores/uiStore'
import SkillsPanel from '../skills/SkillsPanel'
import MCPPanel from '../mcp/MCPPanel'
import HooksPanel from '../hooks/HooksPanel'
import SubAgentsPanel from '../subagents/SubAgentsPanel'
import MemoryPanel from '../memory/MemoryPanel'
import CommandsPanel from '../commands/CommandsPanel'

// Content-only "Plugins/Customize" view. Section navigation lives in the
// persistent sidebar (Plugins submenu); this renders just the active section's
// body. All sections are live (Skills, MCP, Hooks, SubAgents, Commands, Memory).
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
      {section === 'commands' && <CommandsPanel backTitle={backTitle} onBack={onBack} />}
      {section === 'memory' && <MemoryPanel backTitle={backTitle} onBack={onBack} />}
    </div>
  )
}
