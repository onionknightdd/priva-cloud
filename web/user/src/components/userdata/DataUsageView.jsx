import { ArrowLeft } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useUserDataStore from '../../stores/userDataStore'
import UserUsage from './UserUsage'
import UserAuditLog from './UserAuditLog'
import UserAnalytics from './UserAnalytics'
import UserFiles from './UserFiles'
import FileManagerTab from '../settings/FileManagerTab'

const SECTION_LABELS = {
  usage: 'userData.usage',
  analytics: 'userData.analytics',
  audit: 'userData.auditLog',
  files: 'userData.uploadedFiles',
  fileexplorer: 'userData.fileExplorer',
}

function DataSectionHeader({ title, backTitle, onBack }) {
  return (
    <div
      className="flex items-center px-3 flex-shrink-0"
      style={{
        height: 40,
        background: 'var(--bg-base)',
        borderBottom: '1px solid var(--border-subtle)',
      }}
    >
      <div className="inline-flex items-center min-w-0" style={{ gap: 10, flex: '1 1 auto' }}>
        <button
          type="button"
          onClick={onBack}
          className="inline-flex items-center justify-center flex-shrink-0"
          aria-label={backTitle}
          title={backTitle}
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
          {title}
        </span>
      </div>
    </div>
  )
}

// Content-only "Data & Usage" view. Section navigation now lives in the persistent
// sidebar (Data & Usage submenu), so this renders just the active section's body —
// no fixed sidebar of its own (unlike the former UserDataPanel).
export default function DataUsageView({ backTitle, onBack }) {
  const { t } = useTranslation()
  const activeSection = useUserDataStore((s) => s.activeSection)
  const title = t(SECTION_LABELS[activeSection] || 'sidebar.dataUsage')

  return (
    <div
      className="flex flex-1 flex-col"
      style={{ background: 'var(--bg-base)', fontSize: 16, minHeight: 0, minWidth: 0, overflow: 'hidden' }}
    >
      <DataSectionHeader title={title} backTitle={backTitle} onBack={onBack} />
      <div className="flex flex-1 min-w-0 min-h-0 overflow-hidden">
        {activeSection === 'usage' && <UserUsage />}
        {activeSection === 'analytics' && <UserAnalytics />}
        {activeSection === 'audit' && <UserAuditLog />}
        {activeSection === 'files' && <UserFiles />}
        {activeSection === 'fileexplorer' && <FileManagerTab />}
      </div>
    </div>
  )
}
