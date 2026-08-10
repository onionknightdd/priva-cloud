import { Terminal } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import ResizeHandle from '@shared/components/shared/ResizeHandle'
import useCommandsStore from '../../stores/commandsStore'
import CommandsSidebar from './CommandsSidebar'
import CommandEditor from './CommandEditor'
import CommandScopePicker from './CommandScopePicker'

// Content-only Commands (slash-commands) view (rendered inside PluginsView).
// Left column is the command list grouped by scope; the middle is the editor
// or an empty state. Mirrors the SubAgents panel shell (no test column).
export default function CommandsPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const formDraft = useCommandsStore((s) => s.formDraft)
  const listWidth = useCommandsStore((s) => s.listWidth)
  const setListWidth = useCommandsStore((s) => s.setListWidth)
  const openScopePicker = useCommandsStore((s) => s.openScopePicker)

  const { dragging, onMouseDown } = useResizable({
    initial: listWidth, min: 220, max: 420, direction: 'right', onResize: setListWidth,
  })

  const resolvedBackTitle = backTitle || t('split.backToSessions', { defaultValue: '返回 session view' })

  return (
    <div
      className="flex flex-1"
      style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}
    >
      {/* Left — command list */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', minHeight: 0 }}
      >
        <CommandsSidebar backTitle={resolvedBackTitle} onBack={onBack} />
        <ResizeHandle
          onMouseDown={onMouseDown}
          dragging={dragging}
          edge="end"
          style={{ right: 0, top: 0, bottom: 0, zIndex: 10 }}
        />
      </div>

      {/* Middle — editor / empty state */}
      <div className="flex flex-col flex-1 overflow-hidden" style={{ minWidth: 0 }}>
        {formDraft ? (
          <CommandEditor />
        ) : (
          <div className="flex flex-1 flex-col items-center justify-center gap-3" style={{ color: 'var(--text-dim)', minHeight: 0 }}>
            <Terminal size={28} strokeWidth={1.5} />
            <span style={{ fontSize: 13 }}>{t('commands.emptyState')}</span>
            <button
              onClick={() => openScopePicker()}
              className="px-3 py-1 text-xs font-semibold uppercase"
              style={{ background: 'var(--blue)', border: 'none', borderRadius: 4, color: 'var(--text-inverse)', cursor: 'pointer', letterSpacing: '0.06em' }}
            >
              {t('commands.new')}
            </button>
          </div>
        )}
      </div>

      <CommandScopePicker />
    </div>
  )
}
