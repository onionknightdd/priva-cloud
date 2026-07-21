import { PanelRight, Plus } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import { useSlidingUnderline } from '@shared/motion/useSlidingUnderline'
import { useCanvasTabItems } from './CanvasTabMenu'

const MAIN_AREA_HEADER_HEIGHT = 30

function CountedTabLabel({ label, count }) {
  if (!count) return label
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, height: 16, lineHeight: '16px' }}>
      <span>{label}</span>
      <span
        aria-label={`(${count})`}
        style={{
          font: 'inherit',
          lineHeight: 'inherit',
        }}
      >
        ({count})
      </span>
    </span>
  )
}

export default function CanvasHeader() {
  const { t } = useTranslation()
  const canvasWidth = useUiStore((s) => s.canvasWidth)
  const compact = canvasWidth < 380
  const activeCanvasTab = useUiStore((s) => s.activeCanvasTab)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)
  const hideCanvas = useUiStore((s) => s.hideCanvas)
  const showCanvasMenu = useUiStore((s) => s.showCanvasMenu)
  const canvasOpenTabs = useUiStore((s) => s.canvasOpenTabs)
  const tabItems = useCanvasTabItems().filter((tab) => !tab.action && canvasOpenTabs.includes(tab.id))
  const activeTabKey = activeCanvasTab === 'files' ? 'changes' : activeCanvasTab
  const headerUnderline = useSlidingUnderline(activeTabKey)

  return (
    <div
      className={`flex items-center gap-1 flex-shrink-0 ${compact ? 'px-2' : 'px-3'}`}
      style={{
        height: MAIN_AREA_HEADER_HEIGHT,
        borderBottom: '1px solid var(--border)',
        background: 'var(--bg-surface)',
      }}
    >
      <div className="flex items-center min-w-0 flex-1 overflow-x-auto scrollbar-hidden">
        <div
          className={`flex items-center ${compact ? 'gap-2' : 'gap-4'}`}
          style={{ height: '100%', position: 'relative' }}
        >
          <span
            ref={headerUnderline.indicatorRef}
            aria-hidden="true"
            style={{
              position: 'absolute',
              left: 0,
              bottom: 0,
              width: 0,
              height: 2,
              opacity: 0,
              background: 'var(--blue)',
              pointerEvents: 'none',
              zIndex: 2,
            }}
          />
          {tabItems.map((tab) => {
            const active = tab.id === activeTabKey
            return (
              <button
                key={tab.id}
                ref={headerUnderline.setItemRef(tab.id)}
                type="button"
                onClick={() => setActiveCanvasTab(tab.id)}
                style={{
                  position: 'relative',
                  zIndex: 1,
                  height: MAIN_AREA_HEADER_HEIGHT,
                  display: 'inline-flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  padding: 0,
                  border: 'none',
                  background: 'transparent',
                  color: active ? 'var(--text-primary)' : 'var(--text-dim)',
                  cursor: 'pointer',
                  fontSize: 11,
                  fontWeight: 600,
                  lineHeight: '16px',
                  letterSpacing: '0.06em',
                  textTransform: 'uppercase',
                  whiteSpace: 'nowrap',
                  flexShrink: 0,
                  transition: 'color 150ms ease',
                }}
              >
                <CountedTabLabel label={tab.label} count={tab.count} />
              </button>
            )
          })}
          <button
            type="button"
            onClick={showCanvasMenu}
            title={t('canvas.openPanel')}
            className="inline-flex items-center justify-center flex-shrink-0"
            style={{
              width: 24,
              height: MAIN_AREA_HEADER_HEIGHT,
              padding: 0,
              border: 'none',
              background: 'transparent',
              color: 'var(--text-dim)',
              cursor: 'pointer',
              transition: 'color 150ms ease, background 150ms ease',
            }}
            onMouseEnter={(event) => {
              event.currentTarget.style.color = 'var(--text-secondary)'
              event.currentTarget.style.background = 'var(--bg-elevated)'
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.color = 'var(--text-dim)'
              event.currentTarget.style.background = 'transparent'
            }}
          >
            <Plus size={14} strokeWidth={1.5} />
          </button>
        </div>
      </div>
      <button
        type="button"
        onClick={hideCanvas}
        title={t('canvas.close')}
        className="inline-flex items-center justify-center flex-shrink-0"
        style={{
          minWidth: 26,
          width: 26,
          height: 26,
          padding: 0,
          border: 'none',
          background: 'transparent',
          color: 'var(--text-dim)',
          cursor: 'pointer',
          transition: 'color 150ms ease, background 150ms ease',
        }}
        onMouseEnter={(event) => {
          event.currentTarget.style.color = 'var(--text-secondary)'
          event.currentTarget.style.background = 'var(--bg-elevated)'
        }}
        onMouseLeave={(event) => {
          event.currentTarget.style.color = 'var(--text-dim)'
          event.currentTarget.style.background = 'transparent'
        }}
      >
        <PanelRight size={16} strokeWidth={1.5} />
      </button>
    </div>
  )
}
