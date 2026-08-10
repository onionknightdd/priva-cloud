import { useEffect, useRef, useState } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import ResizeHandle from '@shared/components/shared/ResizeHandle'
import useSubagentsStore from '../../stores/subagentsStore'
import SubAgentsSidebar from './SubAgentsSidebar'
import SubAgentEditor from './SubAgentEditor'
import SubAgentEmptyState from './SubAgentEmptyState'
import SubAgentTestPanel from './SubAgentTestPanel'
import SubAgentScopePicker from './SubAgentScopePicker'

const MIN_TEST_WIDTH = 320

// Content-only SubAgents view (rendered inside PluginsView). Left column is the
// agent list; the middle shows the editor / empty state; the right is a resizable
// test-run column. No fixed sidebar / navbar coupling — the persistent app sidebar
// owns that chrome now (mirrors MCPPanel / SkillsPanel).
export default function SubAgentsPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const formDraft = useSubagentsStore((s) => s.formDraft)
  const listWidth = useSubagentsStore((s) => s.listWidth)
  const setListWidth = useSubagentsStore((s) => s.setListWidth)
  const testWidth = useSubagentsStore((s) => s.testWidth)
  const setTestWidth = useSubagentsStore((s) => s.setTestWidth)

  const contentRef = useRef(null)
  const [maxTestWidth, setMaxTestWidth] = useState(720)

  useEffect(() => {
    const update = () => {
      if (contentRef.current) {
        setMaxTestWidth(Math.max(MIN_TEST_WIDTH, Math.floor(contentRef.current.offsetWidth * 0.6)))
      }
    }
    update()
    window.addEventListener('resize', update)
    return () => window.removeEventListener('resize', update)
  }, [listWidth])

  useEffect(() => {
    if (testWidth > maxTestWidth) setTestWidth(maxTestWidth)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxTestWidth])

  const { dragging: listDragging, onMouseDown: onListResizeDown } = useResizable({
    initial: listWidth,
    min: 220,
    max: 420,
    direction: 'right',
    onResize: setListWidth,
  })

  const { dragging: testDragging, onMouseDown: onTestResizeDown } = useResizable({
    initial: testWidth,
    min: MIN_TEST_WIDTH,
    max: maxTestWidth,
    direction: 'left',
    onResize: setTestWidth,
  })

  const showEditor = !!formDraft
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
        style={{ color: 'var(--text-primary)', fontSize: 16, lineHeight: '24px', fontWeight: 700 }}
      >
        {t('tabs.subagents')}
      </span>
    </div>
  )

  return (
    <div
      ref={contentRef}
      className="flex flex-1"
      style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}
    >
      {/* Left column — agent list */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', minHeight: 0 }}
      >
        <SubAgentsSidebar headerStart={headerStart} />
        {/* Resize handle */}
        <ResizeHandle
          onMouseDown={onListResizeDown}
          dragging={listDragging}
          edge="end"
          style={{ right: 0, top: 0, bottom: 0, zIndex: 10 }}
        />
      </div>

      {/* Middle — editor / empty state */}
      <div className="flex flex-col flex-1 overflow-hidden" style={{ minWidth: 0 }}>
        {showEditor ? <SubAgentEditor /> : <SubAgentEmptyState />}
      </div>

      {/* Right — test-run column, resizable (handle rendered inside the panel) */}
      <div
        className="relative flex-shrink-0 overflow-hidden"
        style={{
          width: testWidth,
          transition: testDragging ? 'none' : 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
      >
        <div className="flex flex-col h-full" style={{ width: testWidth }}>
          <SubAgentTestPanel onResize={onTestResizeDown} dragging={testDragging} />
        </div>
      </div>

      {/* New-agent scope picker → editor */}
      <SubAgentScopePicker />
    </div>
  )
}
