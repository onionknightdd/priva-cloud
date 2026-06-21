import safeStorage from '../utils/safeStorage'

// The localStorage key that holds THIS SPA's session token. It is injected at
// build time via Vite `define` (__PRIVA_TOKEN_KEY__) so the user SPA and the admin
// SPA persist their logins under different keys ('priva-user-token' vs
// 'priva-admin-token'). A login in one SPA therefore does NOT authenticate the
// other in the same browser. Falls back to the legacy key when unset (e.g. tests).
const TOKEN_KEY =
  typeof __PRIVA_TOKEN_KEY__ !== 'undefined' ? __PRIVA_TOKEN_KEY__ : 'priva-token'

export function getToken() {
  return safeStorage.getItem(TOKEN_KEY)
}

export function setToken(token) {
  if (token) safeStorage.setItem(TOKEN_KEY, token)
  else safeStorage.removeItem(TOKEN_KEY)
}

export function clearToken() {
  safeStorage.removeItem(TOKEN_KEY)
}

export { TOKEN_KEY }
