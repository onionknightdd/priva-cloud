import { useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import ResizeHandle from '@shared/components/shared/ResizeHandle'
import useMcpStore from '../../stores/mcpStore'
import MCPListSidebar from './MCPListSidebar'
import MCPServerMeta from './MCPServerMeta'
import MCPServerDetail from './MCPServerDetail'
import MCPToolDrawer from './MCPToolDrawer'
import MCPAddDialog from './MCPAddDialog'
import MCPScopePicker from './MCPScopePicker'

// Content-only MCP view (rendered inside PluginsView). Left column is the server
// list; the rest shows the selected server's meta + capabilities, with a tool
// drawer that slides in from the right. No fixed sidebar / navbar coupling — the
// persistent app sidebar owns that chrome now (mirrors SkillsPanel).
export default function MCPPanel({ backTitle, onBack }) {
  const { t } = useTranslation()
  const fetchServers = useMcpStore((s) => s.fetchServers)
  const selectedServer = useMcpStore((s) => s.selectedServer)
  const selectedTool = useMcpStore((s) => s.selectedTool)
  const addDialogOpen = useMcpStore((s) => s.addDialogOpen)
  const listWidth = useMcpStore((s) => s.listWidth)
  const setListWidth = useMcpStore((s) => s.setListWidth)
  const toolDrawerWidth = useMcpStore((s) => s.toolDrawerWidth)
  const setToolDrawerWidth = useMcpStore((s) => s.setToolDrawerWidth)

  const toolDrawerOpen = !!selectedTool

  const { dragging: listDragging, onMouseDown: onListResizeDown } = useResizable({
    initial: listWidth,
    min: 220,
    max: 420,
    direction: 'right',
    onResize: setListWidth,
  })

  const { dragging: toolDragging, onMouseDown: onToolResizeDown } = useResizable({
    initial: toolDrawerWidth,
    min: 280,
    max: 600,
    direction: 'left',
    onResize: setToolDrawerWidth,
  })

  useEffect(() => {
    fetchServers()
  }, [fetchServers])

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
        {t('tabs.mcp')}
      </span>
    </div>
  )

  return (
    <div className="flex flex-1" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden', background: 'var(--bg-base)' }}>
      {/* Left column — server list */}
      <div
        className="flex flex-col flex-shrink-0 relative"
        style={{ width: listWidth, background: 'var(--bg-surface)', minHeight: 0 }}
      >
        <MCPListSidebar headerStart={headerStart} />
        {/* Resize handle */}
        <ResizeHandle
          onMouseDown={onListResizeDown}
          dragging={listDragging}
          edge="end"
          style={{ right: 0, top: 0, bottom: 0, zIndex: 10 }}
        />
      </div>

      {/* Right — selected server meta + capabilities + tool drawer */}
      {selectedServer ? (
        <>
          <MCPServerMeta />

          {/* Server detail — shrinks when tool drawer is open. MCPServerDetail must be
              a direct flex item here: wrapping it in a block box leaves its height
              auto, so its inner overflow-y-auto list never scrolls and just clips. */}
          <div className="flex-1 flex overflow-hidden" style={{ minWidth: 0, minHeight: 0 }}>
            <MCPServerDetail />

            {/* Tool drawer — slides in from right, resizable */}
            <div
              className="relative flex-shrink-0 overflow-hidden"
              style={{
                width: toolDrawerOpen ? toolDrawerWidth : 0,
                maxWidth: '100vw',
                transition: toolDragging ? 'none' : 'width 220ms cubic-bezier(0.16, 1, 0.3, 1)',
              }}
            >
              {toolDrawerOpen && (
                <>
                  {/* Resize handle on left edge */}
                  <ResizeHandle
                    onMouseDown={onToolResizeDown}
                    dragging={toolDragging}
                    edge="start"
                    style={{ left: 0, top: 0, bottom: 0, zIndex: 10 }}
                  />
                  <div style={{ width: toolDrawerWidth, height: '100%' }}>
                    <MCPToolDrawer />
                  </div>
                </>
              )}
            </div>
          </div>
        </>
      ) : (
        <div
          className="flex-1 flex items-center justify-center"
          style={{ background: 'var(--bg-base)', color: 'var(--text-dim)', fontSize: 13 }}
        >
          {t('mcp.selectServer')}
        </div>
      )}

      {/* Add-server scope picker → form */}
      <MCPScopePicker />
      {addDialogOpen && <MCPAddDialog />}
    </div>
  )
}
