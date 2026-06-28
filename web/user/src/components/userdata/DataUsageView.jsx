import useUserDataStore from '../../stores/userDataStore'
import UserUsage from './UserUsage'
import UserAuditLog from './UserAuditLog'
import UserAnalytics from './UserAnalytics'
import UserFiles from './UserFiles'
import FileManagerTab from '../settings/FileManagerTab'

// Content-only "Data & Usage" view. Section navigation now lives in the persistent
// sidebar (Data & Usage submenu), so this renders just the active section's body —
// no fixed sidebar of its own (unlike the former UserDataPanel).
export default function DataUsageView() {
  const activeSection = useUserDataStore((s) => s.activeSection)

  return (
    <div
      className="flex flex-1 flex-col"
      style={{ background: 'var(--bg-base)', fontSize: 16, minHeight: 0, minWidth: 0, overflow: 'hidden' }}
    >
      {activeSection === 'usage' && <UserUsage />}
      {activeSection === 'analytics' && <UserAnalytics />}
      {activeSection === 'audit' && <UserAuditLog />}
      {activeSection === 'files' && <UserFiles />}
      {activeSection === 'fileexplorer' && <FileManagerTab />}
    </div>
  )
}
