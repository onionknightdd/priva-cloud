import { useEffect } from 'react'
import { useResizable } from '@shared/hooks/useResizable'
import useSkillsStore from '../../stores/skillsStore'
import SkillList from './SkillList'
import SkillFileViewer from './SkillFileViewer'
import SkillHubModal from './SkillHubModal'
import SkillSyncModal from './SkillSyncModal'

// Content-only Skills view (rendered inside PluginsView). The skill list — now
// grouped by Personal + workdir, with each skill's file tree inline — is the left
// column; the file viewer fills the rest. No fixed sidebar / navbar coupling.
export default function SkillsPanel() {
  const fetchSkills = useSkillsStore((s) => s.fetchSkills)
  const selectedSkill = useSkillsStore((s) => s.selectedSkill)
  const listWidth = useSkillsStore((s) => s.listWidth)
  const setListWidth = useSkillsStore((s) => s.setListWidth)

  const { dragging, onMouseDown } = useResizable({
    initial: listWidth,
    min: 220,
    max: 560,
    direction: 'right',
    onResize: setListWidth,
  })

  useEffect(() => {
    fetchSkills()
  }, [fetchSkills])

  return (
    <div className="flex flex-1" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}>
      {/* Left column — grouped skill list with inline file trees */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', borderRight: '1px solid var(--border)', minHeight: 0 }}
      >
        <SkillList />
        {/* Resize handle */}
        <div
          onMouseDown={onMouseDown}
          style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: 4,
            cursor: 'col-resize', zIndex: 10,
            background: dragging ? 'var(--blue)' : 'transparent',
            transition: 'background 100ms ease',
          }}
          onMouseEnter={(e) => { if (!dragging) e.currentTarget.style.background = 'var(--blue)' }}
          onMouseLeave={(e) => { if (!dragging) e.currentTarget.style.background = 'transparent' }}
        />
      </div>

      {/* Right column — file viewer */}
      <SkillFileViewer animKey={selectedSkill ? `${selectedSkill.scope}:${selectedSkill.cwd || ''}:${selectedSkill.name}` : 'none'} />

      <SkillHubModal />
      <SkillSyncModal />
    </div>
  )
}
