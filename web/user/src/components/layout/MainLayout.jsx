import { Suspense, useEffect } from 'react'
import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import Sidebar from './Sidebar'
import ChatPanel from '../chat/ChatPanel'
import useSidebarStore from '../../stores/sidebarStore'
import useUiStore from '@shared/stores/uiStore'
import useSplitStore from '../../stores/splitStore'
import SplitSessionView from './SplitSessionView'
import EmbeddedSessionLoader from './EmbeddedSessionLoader'
import { isSplitPane } from '../../utils/splitMode'
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

function ContentOverlay({ title, onBack, showHeader = true, children }) {
  return (
    <div
      className="absolute inset-0 flex flex-col"
      style={{
        zIndex: 40,
        background: 'var(--bg-base)',
        minWidth: 0,
        minHeight: 0,
      }}
    >
      {showHeader && (
        <div
          className="flex items-center px-3 flex-shrink-0"
          style={{
            height: 40,
            background: 'var(--bg-base)',
          }}
        >
          <button
            type="button"
            onClick={onBack}
            className="inline-flex items-center justify-center"
            aria-label={title}
            title={title}
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
        </div>
      )}
      <div className="flex flex-1 min-w-0 min-h-0 overflow-hidden">
        {children}
      </div>
    </div>
  )
}

export default function MainLayout() {
  const { t } = useTranslation()
  const embedded = isSplitPane()
  const sidebarWidth = useSidebarStore((s) => s.width)
  const collapsed = useSidebarStore((s) => s.collapsed)
  const activeNavTab = useUiStore((s) => s.activeNavTab)
  const activePluginSection = useUiStore((s) => s.activePluginSection)
  const canvasVisible = useUiStore((s) => s.canvasVisible)
  const terminalOpen = useUiStore((s) => s.terminalOpen)
  const terminalMode = useUiStore((s) => s.terminalMode)
  const setTerminalMode = useUiStore((s) => s.setTerminalMode)
  const setActiveNavTab = useUiStore((s) => s.setActiveNavTab)
  const panes = useSplitStore((s) => s.panes)

  const effectiveSidebarWidth = embedded ? 0 : (collapsed ? 48 : sidebarWidth)
  // The NavBar was removed; the sidebar is the only persistent chrome. The content
  // area swaps between the chat view, Data & Usage, and Plugins/Customize.
  const isData = activeNavTab === 'userdata'
  const isPlugins = activeNavTab === 'plugins'
  const isSkillsPlugin = isPlugins && activePluginSection === 'skills'
  const splitActive = panes.length > 0
  const backToSessionsTitle = t('split.backToSessions', { defaultValue: '返回 session view' })
  const backToSessions = () => setActiveNavTab('priva')

  useEffect(() => {
    if (embedded) return
    if (splitActive && terminalOpen && terminalMode !== 'float') {
      setTerminalMode('float')
    }
  }, [embedded, setTerminalMode, splitActive, terminalMode, terminalOpen])

  if (embedded) {
    return (
      <div
        className="user-main-scope flex"
        style={{
          height: '100vh',
          width: '100vw',
          overflow: 'hidden',
          minWidth: 0,
          minHeight: 0,
          background: 'var(--bg-base)',
        }}
      >
        <EmbeddedSessionLoader />
        <ChatPanel />
        {canvasVisible && (
          <Suspense fallback={null}>
            <CanvasPanel />
          </Suspense>
        )}
      </div>
    )
  }

  const sessionFallback = (
    <>
      <ChatPanel />
      {canvasVisible && (
        <Suspense fallback={null}>
          <CanvasPanel />
        </Suspense>
      )}
    </>
  )

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
        className="user-main-scope flex relative"
        style={{
          flex: '1 1 0%',
          minHeight: 0,
          minWidth: 0,
          overflow: 'hidden',
        }}
      >
        <SplitSessionView fallback={sessionFallback} />
        {isData && (
          <ContentOverlay
            title={backToSessionsTitle}
            onBack={backToSessions}
          >
            <LazyPanel><DataUsageView /></LazyPanel>
          </ContentOverlay>
        )}
        {isPlugins && (
          <ContentOverlay
            title={backToSessionsTitle}
            onBack={backToSessions}
            showHeader={!isSkillsPlugin}
          >
            <LazyPanel><PluginsView backTitle={backToSessionsTitle} onBack={backToSessions} /></LazyPanel>
          </ContentOverlay>
        )}
      </div>
      {terminalOpen && (!isData && !isPlugins || splitActive) && (
        <Suspense fallback={null}>
          <WebTerminalDrawer />
        </Suspense>
      )}
    </div>
  )
}
