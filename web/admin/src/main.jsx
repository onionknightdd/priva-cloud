import React from 'react'
import ReactDOM from 'react-dom/client'
import '@shared/i18n'
import { setResetStores } from '@shared/stores/authStore'
import useAdminStore from './stores/adminStore'
import AdminApp from './AdminApp'
import '@shared/index.css'

// The admin SPA only owns the admin store; nothing from the user app is reset here.
setResetStores([useAdminStore])

ReactDOM.createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <AdminApp />
  </React.StrictMode>
)
