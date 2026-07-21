import { FileDiff, FileText, FolderTree, Globe2, PanelRight, SquareTerminal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUiStore from '@shared/stores/uiStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'

export function useCanvasTabItems() {
  const { t } = useTranslation()
  const fileBrowserCount = useFileBrowserStore((s) => s.tabs.length)
  const changeOpsCount = useFileOpsStore((s) => s.fileOps.filter((op) => op.type === 'write' || op.type === 'edit').length)
  const hasPlan = useUiStore((s) => !!s.planContent)
  const terminalFeatureEnabled = useUiStore((s) => s.terminalFeatureEnabled)
  const terminalOpen = useUiStore((s) => s.terminalOpen)
  const terminalActiveCount = useUiStore((s) => s.terminalActiveCount)
  const toggleTerminal = useUiStore((s) => s.toggleTerminal)

  return [
    { id: 'tasks', label: t('canvas.tasks'), icon: PanelRight },
    { id: 'file-browser', label: t('canvas.fileBrowser'), icon: FolderTree, count: fileBrowserCount },
    { id: 'changes', label: t('canvas.changeReview'), icon: FileDiff, count: changeOpsCount },
    ...(hasPlan ? [{ id: 'plan', label: t('canvas.plan'), icon: FileText }] : []),
    { id: 'browser', label: t('canvas.browserTab'), icon: Globe2 },
    ...(terminalFeatureEnabled ? [{
      id: 'terminal',
      label: terminalActiveCount > 0
        ? t('terminal.openWithCount', { count: terminalActiveCount })
        : t('terminal.open'),
      icon: SquareTerminal,
      action: toggleTerminal,
      danger: terminalOpen || terminalActiveCount > 0,
    }] : []),
  ]
}

export default function CanvasTabMenu() {
  const { t } = useTranslation()
  const openTabs = useUiStore((s) => s.canvasOpenTabs)
  const openCanvasTab = useUiStore((s) => s.openCanvasTab)
  const items = useCanvasTabItems()
  const availableItems = items.filter((item) => item.action || !openTabs.includes(item.id))

  if (availableItems.length === 0) {
    return (
      <div className="flex flex-1 items-center justify-center px-3 py-3" style={{ color: 'var(--text-dim)', fontSize: 12 }}>
        {t('canvas.allTabsOpen')}
      </div>
    )
  }

  return (
    <div className="flex flex-1 flex-col overflow-y-auto px-2 py-2" style={{ minHeight: 0 }}>
      {availableItems.map((item) => {
        const Icon = item.icon
        return (
          <button
            key={item.id}
            type="button"
            onClick={() => {
              if (item.action) item.action()
              else openCanvasTab(item.id)
            }}
            className="flex items-center gap-2 w-full"
            style={{
              minWidth: 0,
              height: 32,
              padding: '0 8px',
              background: 'transparent',
              border: 'none',
              borderLeft: '2px solid transparent',
              borderRadius: 2,
              color: item.danger ? 'var(--red)' : 'var(--text-secondary)',
              cursor: 'pointer',
              fontSize: 12,
              textAlign: 'left',
              transition: 'background 150ms ease, color 150ms ease',
            }}
            onMouseEnter={(event) => {
              event.currentTarget.style.background = 'var(--bg-elevated)'
              event.currentTarget.style.color = item.danger ? 'var(--red)' : 'var(--text-primary)'
            }}
            onMouseLeave={(event) => {
              event.currentTarget.style.background = 'transparent'
              event.currentTarget.style.color = item.danger ? 'var(--red)' : 'var(--text-secondary)'
            }}
          >
            <Icon size={14} strokeWidth={1.5} style={{ flexShrink: 0 }} />
            <span className="truncate" style={{ minWidth: 0 }}>{item.label}</span>
          </button>
        )
      })}
    </div>
  )
}
