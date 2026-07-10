import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useHooksStore from '../../stores/hooksStore'
import { useResizable } from '@shared/hooks/useResizable'
import HooksSidebar from './HooksSidebar'
import LifecycleGraph from './LifecycleGraph'
import HookDetailPanel from './HookDetailPanel'

// Content-only Hooks view (rendered inside PluginsView). Left column is the hook
// event list (grouped by phase); the middle shows the lifecycle graph; a detail
// drawer slides in from the right on selection. No fixed sidebar / navbar
// coupling — the persistent app sidebar owns that chrome now (mirrors SkillsPanel).
export default function HooksPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const selectedHookId = useHooksStore((s) => s.selectedHookId)
  const listWidth = useHooksStore((s) => s.listWidth)
  const setListWidth = useHooksStore((s) => s.setListWidth)
  const detailWidth = useHooksStore((s) => s.detailWidth)
  const setDetailWidth = useHooksStore((s) => s.setDetailWidth)

  const { dragging: listDragging, onMouseDown: onListResizeDown } = useResizable({
    initial: listWidth,
    min: 220,
    max: 420,
    direction: 'right',
    onResize: setListWidth,
  })

  const { dragging: detailDragging, onMouseDown: onDetailResizeDown } = useResizable({
    initial: detailWidth,
    min: 280,
    max: 600,
    direction: 'left',
    onResize: setDetailWidth,
  })

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
        {t('tabs.hooks')}
      </span>
    </div>
  )

  return (
    <div className="flex flex-1" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}>
      {/* Left column — hook event list */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', borderRight: '1px solid var(--border)', minHeight: 0 }}
      >
        <div className="flex items-center flex-shrink-0 px-3" style={{ height: 40, borderBottom: '1px solid var(--border-subtle)' }}>
          {headerStart}
        </div>
        <HooksSidebar />
        {/* Resize handle */}
        <div
          onMouseDown={onListResizeDown}
          style={{
            position: 'absolute', right: 0, top: 0, bottom: 0, width: 4,
            cursor: 'col-resize', zIndex: 10,
            background: listDragging ? 'var(--blue)' : 'transparent',
            transition: 'background 100ms ease',
          }}
          onMouseEnter={(e) => { if (!listDragging) e.currentTarget.style.background = 'var(--blue)' }}
          onMouseLeave={(e) => { if (!listDragging) e.currentTarget.style.background = 'transparent' }}
        />
      </div>

      {/* Middle — lifecycle graph + right detail drawer */}
      <div className="flex flex-1 overflow-hidden" style={{ minWidth: 0 }}>
        <div className="flex flex-col flex-1 overflow-hidden" style={{ minWidth: 0 }}>
          {/* Graph title bar */}
          <div
            className="flex items-center gap-3 px-4 flex-shrink-0"
            style={{ height: 48, borderBottom: '1px solid var(--border-subtle)' }}
          >
            <span
              className="uppercase font-semibold"
              style={{ fontSize: 12, color: 'var(--text-dim)', letterSpacing: '0.06em' }}
            >
              {t('hooks.graphTitle')}
            </span>
            <span style={{ fontSize: 12, color: 'var(--text-dim)' }}>
              {t('hooks.graphSubtitle')}
            </span>
          </div>

          {/* Graph — fills remaining space, zoom/pan handled internally */}
          <div className="flex-1 overflow-hidden">
            <LifecycleGraph />
          </div>
        </div>

        {/* Detail drawer — slides in from right, resizable */}
        <div
          className="relative flex-shrink-0 overflow-hidden"
          style={{
            width: selectedHookId ? detailWidth : 0,
            transition: detailDragging ? 'none' : 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
            borderLeft: selectedHookId ? '1px solid var(--border)' : 'none',
          }}
        >
          {selectedHookId && (
            <div
              onMouseDown={onDetailResizeDown}
              style={{
                position: 'absolute', left: 0, top: 0, bottom: 0, width: 4,
                cursor: 'col-resize',
                background: detailDragging ? 'var(--blue)' : 'transparent',
                transition: 'background 100ms ease', zIndex: 10,
              }}
              onMouseEnter={(e) => { if (!detailDragging) e.currentTarget.style.background = 'var(--blue)' }}
              onMouseLeave={(e) => { if (!detailDragging) e.currentTarget.style.background = 'transparent' }}
            />
          )}
          <div className="flex flex-col h-full" style={{ width: detailWidth }}>
            <HookDetailPanel />
          </div>
        </div>
      </div>
    </div>
  )
}
