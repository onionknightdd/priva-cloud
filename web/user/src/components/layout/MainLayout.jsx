import { Suspense } from 'react'
import Sidebar from './Sidebar'
import ChatPanel from '../chat/ChatPanel'
import useSidebarStore from '../../stores/sidebarStore'
import useUiStore from '@shared/stores/uiStore'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'

const CanvasPanel = lazyWithChunkReload(() => import('./CanvasPanel'))
const DataUsageView = lazyWithChunkReload(() => import('../userdata/DataUsageView'))
const PluginsView = lazyWithChunkReload(() => import('../plugins/PluginsView'))
const WebTerminalDrawer = lazyWithChunkReload(() => import('../terminal/WebTerminalDrawer'))

function LazyPanel({ children }) {
  return (
    <Suspense fallback={<div className="flex-1" style={{ background: 'var(--bg-base)' }} />}>
      {children}
    </Suspense>
  )
}

export default function MainLayout() {
  const sidebarWidth = useSidebarStore((s) => s.width)
  const collapsed = useSidebarStore((s) => s.collapsed)
  const activeNavTab = useUiStore((s) => s.activeNavTab)
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const terminalOpen = useUiStore((s) => s.terminalOpen)

  const effectiveSidebarWidth = collapsed ? 48 : sidebarWidth
  // The NavBar was removed; the sidebar is the only persistent chrome. The content
  // area swaps between the chat view, Data & Usage, and Plugins/Customize.
  const isData = activeNavTab === 'userdata'
  const isPlugins = activeNavTab === 'plugins'

  return (
    <div
      className="flex flex-col flex-1"
      style={{
        marginTop: 'var(--navbar-height)',
        marginLeft: effectiveSidebarWidth,
        transition: 'margin-left 220ms cubic-bezier(0.16, 1, 0.3, 1)',
        height: 'calc(100vh - var(--navbar-height))',
        overflow: 'hidden',
        minWidth: 0,
      }}
    >
      {/* Persistent sidebar (position: fixed; offset above via marginLeft) */}
      <Sidebar />

      <div
        className="flex"
        style={{
          flex: '1 1 0%',
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        {isData ? (
          <LazyPanel><DataUsageView /></LazyPanel>
        ) : isPlugins ? (
          <LazyPanel><PluginsView /></LazyPanel>
        ) : (
          <>
            <ChatPanel />
            {canvasVisible && (
              <Suspense fallback={null}>
                <CanvasPanel />
              </Suspense>
            )}
          </>
        )}
      </div>
      {!isData && !isPlugins && terminalOpen && (
        <Suspense fallback={null}>
          <WebTerminalDrawer />
        </Suspense>
      )}
    </div>
  )
}
