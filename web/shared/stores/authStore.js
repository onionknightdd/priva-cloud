import { create } from 'zustand'
import { checkSetup, getMe } from '../api/auth'
import { getToken, setToken as persistToken, clearToken } from '../api/tokenStore'

// The auth store no longer imports any feature store. Each SPA injects the set of
// stores to clear on logout via setResetStores() at boot (see main.jsx /
// main-admin.jsx). This keeps the user SPA's stores out of the admin bundle and
// vice-versa — the auth store is the only thing both SPAs share.
let RESET_STORES = []

export function setResetStores(stores) {
  RESET_STORES = Array.isArray(stores) ? stores : []
}

const useAuthStore = create((set, get) => ({
  user: null,
  token: getToken() || null,
  needsSetup: null,
  loading: true,

  setToken: (token) => {
    persistToken(token)
    set({ token })
  },

  setUser: (user) => set({ user }),

  logout: () => {
    clearToken()
    set({ user: null, token: null })

    // Reset the SPA's feature stores to clear the previous user's data.
    for (const store of RESET_STORES) {
      try {
        store.getState().reset?.()
      } catch {
        /* a store without reset() is fine */
      }
    }
  },

  initialize: async () => {
    set({ loading: true })
    try {
      // Check if setup is needed
      const setupStatus = await checkSetup()
      if (setupStatus.needs_setup) {
        set({ needsSetup: true, loading: false })
        return
      }
      set({ needsSetup: false })

      // Validate existing token
      const token = get().token
      if (token) {
        try {
          const user = await getMe(token)
          set({ user, loading: false })
          return
        } catch {
          // Token invalid, clear it
          clearToken()
          set({ token: null })
        }
      }
      set({ loading: false })
    } catch {
      set({ loading: false })
    }
  },
}))

export default useAuthStore
