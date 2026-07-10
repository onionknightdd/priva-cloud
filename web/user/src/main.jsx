import React from 'react'
import ReactDOM from 'react-dom/client'
import '@shared/i18n'
import { setResetStores } from '@shared/stores/authStore'
import { resetAllRuntimes } from './stores/runtime/registry'
import useSidebarStore from './stores/sidebarStore'
import useSessionStatusStore from './stores/sessionStatusStore'
import useSkillsStore from './stores/skillsStore'
import useSettingsStore from './stores/settingsStore'
import useUiStore from '@shared/stores/uiStore'
import useUserDataStore from './stores/userDataStore'
import useHooksStore from './stores/hooksStore'
import useSkillHubStore from './stores/skillHubStore'
import useSplitStore from './stores/splitStore'
import App from './App'
import '@shared/index.css'

// Stores cleared on logout for the user SPA (admin store is not part of this app).
// The five per-session stores (chat/tasks/fileOps/fileBrowser/workflow) live in
// session runtimes now — the shim below aborts every live stream and drops every
// runtime in one shot.
setResetStores([
  { getState: () => ({ reset: resetAllRuntimes }) },
  useSidebarStore,
  useSessionStatusStore,
  useSkillsStore,
  useSettingsStore,
  useUiStore,
  useUserDataStore,
  useHooksStore,
  useSkillHubStore,
  useSplitStore,
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
