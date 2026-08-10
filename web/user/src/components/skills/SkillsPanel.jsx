import { useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import ResizeHandle from '@shared/components/shared/ResizeHandle'
import useSkillsStore from '../../stores/skillsStore'
import SkillList from './SkillList'
import SkillFileViewer from './SkillFileViewer'
import SkillHubModal from './SkillHubModal'
import SkillSyncModal from './SkillSyncModal'

// Content-only Skills view (rendered inside PluginsView). The skill list — now
// grouped by Personal + workdir, with each skill's file tree inline — is the left
// column; the file viewer fills the rest. No fixed sidebar / navbar coupling.
export default function SkillsPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const fetchSkills = useSkillsStore((s) => s.fetchSkills)
  const selectedSkill = useSkillsStore((s) => s.selectedSkill)
  const selectedFile = useSkillsStore((s) => s.selectedFile)
  const viewerMode = useSkillsStore((s) => s.viewerMode)
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

  const resolvedBackTitle = backTitle || t('split.backToSessions', { defaultValue: '返回 session view' })
  const headerStart = (
    <div className="inline-flex items-center min-w-0" style={{ gap: 10, flex: '1 1 auto' }}>
      <button
        type="button"
        onClick={onBack}
        className="inline-flex items-center justify-center flex-shrink-0"
        aria-label={resolvedBackTitle}
        title={resolvedBackTitle}
        style={{
          width: 28,
          height: 28,
          padding: 0,
          background: 'transparent',
          border: 'none',
          borderRadius: 4,
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          transition: 'color 150ms ease, background 150ms ease',
        }}
        onMouseEnter={(event) => {
          event.currentTarget.style.color = 'var(--text-primary)'
          event.currentTarget.style.background = 'var(--bg-elevated)'
        }}
        onMouseLeave={(event) => {
          event.currentTarget.style.color = 'var(--text-secondary)'
          event.currentTarget.style.background = 'transparent'
        }}
      >
        <ArrowLeft size={16} strokeWidth={1.5} />
      </button>
      <span
        className="truncate"
        style={{
          color: 'var(--text-primary)',
          fontSize: 16,
          lineHeight: '24px',
          fontWeight: 700,
        }}
      >
        {t('tabs.skills')}
      </span>
    </div>
  )

  const viewerAnimKey = selectedSkill
    ? [
        selectedSkill.scope,
        selectedSkill.cwd || '',
        selectedSkill.name,
        viewerMode || 'skill',
        selectedFile || '',
      ].join(':')
    : 'none'

  return (
    <div className="flex flex-1" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}>
      {/* Left column — grouped skill list with inline file trees */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', minHeight: 0 }}
      >
        <SkillList headerStart={headerStart} />
        {/* Resize handle */}
        <ResizeHandle
          onMouseDown={onMouseDown}
          dragging={dragging}
          edge="end"
          style={{ right: 0, top: 0, bottom: 0, zIndex: 10 }}
        />
      </div>

      {/* Right column — file viewer */}
      <SkillFileViewer animKey={viewerAnimKey} />

      <SkillHubModal />
      <SkillSyncModal />
    </div>
  )
}
