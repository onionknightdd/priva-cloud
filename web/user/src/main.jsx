import React from 'react'
import ReactDOM from 'react-dom/client'
import '@shared/i18n'
import { setResetStores } from '@shared/stores/authStore'
import useTaskStore from './stores/taskStore'
import useChatStore from './stores/chatStore'
import useSidebarStore from './stores/sidebarStore'
import useFileOpsStore from './stores/fileOpsStore'
import useFileBrowserStore from './stores/fileBrowserStore'
import useSkillsStore from './stores/skillsStore'
import useSettingsStore from './stores/settingsStore'
import useUiStore from '@shared/stores/uiStore'
import useUserDataStore from './stores/userDataStore'
import useHooksStore from './stores/hooksStore'
import useSkillHubStore from './stores/skillHubStore'
import App from './App'
import '@shared/index.css'

// Stores cleared on logout for the user SPA (admin store is not part of this app).
setResetStores([
  useTaskStore,
  useChatStore,
  useSidebarStore,
  useFileOpsStore,
  useFileBrowserStore,
  useSkillsStore,
  useSettingsStore,
  useUiStore,
  useUserDataStore,
  useHooksStore,
  useSkillHubStore,
])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>
)
