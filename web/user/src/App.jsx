import { Suspense, useCallback, useEffect, useRef, useState } from 'react'
import MainLayout from './components/layout/MainLayout'
import ConfirmDialog from './components/shared/ConfirmDialog'
import ErrorBoundary from './components/shared/ErrorBoundary'
import SetupPage from './components/auth/SetupPage'
import LoginPage from '@shared/components/auth/LoginPage'
import useAuthStore from '@shared/stores/authStore'
import useSettingsStore from './stores/settingsStore'
import useUiStore, { THEME_CHANNEL } from '@shared/stores/uiStore'
import SettingsOverlay from './components/settings/SettingsOverlay'
import ToastStack from './components/ui/ToastStack'
import ConnectionBanner from './components/ui/ConnectionBanner'
import { getTerminalCapability } from '@shared/api/admin'
import safeStorage from '@shared/utils/safeStorage'
import lazyWithChunkReload from '@shared/utils/lazyWithChunkReload'
import { restoreRunningSessions } from './session/attachBoot'

const IntroPanel = lazyWithChunkReload(() => import('./components/intro/IntroPanel'))
const SetupWizardModal = lazyWithChunkReload(() => import('./components/chat/SetupWizardModal'))

const INTRO_SEEN_KEY_PREFIX = 'priva-intro-seen'
const TERMINAL_CONNECTABLE_PHASES = new Set(['Zero', 'Waking', 'Running'])

function getIntroSeenKey(user) {
  return user?.username ? `${INTRO_SEEN_KEY_PREFIX}:${user.username}` : null
}

export default function App() {
  const loading = useAuthStore((s) => s.loading)
  const needsSetup = useAuthStore((s) => s.needsSetup)
  const user = useAuthStore((s) => s.user)
  const initialize = useAuthStore((s) => s.initialize)
  const logout = useAuthStore((s) => s.logout)
  const hasEnv = useSettingsStore((s) => s.hasEnv)
  const fetchEnvStatus = useSettingsStore((s) => s.fetchEnvStatus)
  const fetchEnv = useSettingsStore((s) => s.fetchEnv)
  const fetchVisionModel = useSettingsStore((s) => s.fetchVisionModel)
  const openIntro = useUiStore((s) => s.openIntro)
  const introOpen = useUiStore((s) => s.introOpen)
  const setTheme = useUiStore((s) => s.setTheme)
  const setTerminalFeatureEnabled = useUiStore((s) => s.setTerminalFeatureEnabled)
  const setTerminalMaxSessions = useUiStore((s) => s.setTerminalMaxSessions)
  const setTerminalOpen = useUiStore((s) => s.setTerminalOpen)
  const [showSetupWizard, setShowSetupWizard] = useState(false)
  // Sticky latch: once the wizard has been shown, keep its (lazy) chunk
  // mounted so the modal can play its exit animation after close — the
  // wrapper renders null once fully exited.
  const setupWizardShownRef = useRef(false)
  if (showSetupWizard) setupWizardShownRef.current = true

  useEffect(() => {
    const params = new URLSearchParams(window.location.search)
    const requestedTheme = params.get('theme')
    setTheme(requestedTheme || safeStorage.getItem('theme') || 'light', {
      persist: !requestedTheme,
      broadcast: false,
    })

    const onStorage = (event) => {
      if (event.key !== 'theme' || !event.newValue) return
      setTheme(event.newValue, { persist: false, broadcast: false })
    }
    window.addEventListener('storage', onStorage)

    let channel = null
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        channel = new BroadcastChannel(THEME_CHANNEL)
        channel.onmessage = (event) => {
          if (event.data?.type !== 'theme') return
          setTheme(event.data.theme, { persist: false, broadcast: false })
        }
      } catch {
        channel = null
      }
    }

    return () => {
      window.removeEventListener('storage', onStorage)
      channel?.close()
    }
  }, [setTheme])

  useEffect(() => {
    initialize()
  }, [initialize])

  useEffect(() => {
    const handler = () => logout()
    window.addEventListener('auth:unauthorized', handler)
    return () => window.removeEventListener('auth:unauthorized', handler)
  }, [logout])

  // Check env status after login
  useEffect(() => {
    if (user) {
      fetchEnvStatus().then((has) => {
        if (has) {
          // Also fetch full env for model selector defaults
          fetchEnv()
        }
      })
      fetchVisionModel()
      // Re-attach to runs that survived a refresh in the backend RunRegistry
      // (no-op on registry-less backends).
      restoreRunningSessions()
    }
  }, [user, fetchEnvStatus, fetchEnv, fetchVisionModel])

  // Keep the platform switch live: 0% removes the Agent UI affordance and closes
  // an open drawer without requiring the user to sign out and back in.
  useEffect(() => {
    if (!user) {
      setTerminalFeatureEnabled(false)
      setTerminalOpen(false)
      return undefined
    }
    let active = true
    let firstLoad = true
    const refresh = async () => {
      try {
        const data = await getTerminalCapability()
        if (!active) return
        // Defense in depth for rolling deploys: an older Control Panel may report the
        // desired Terminal policy as enabled while the Operator says the Runner still
        // needs a restart. Never render an affordance whose WS upgrade must 503.
        const enabled = data?.enabled === true && TERMINAL_CONNECTABLE_PHASES.has(data?.phase)
        setTerminalFeatureEnabled(enabled)
        setTerminalMaxSessions(data?.max_sessions || 2)
        if (!enabled) setTerminalOpen(false)
      } catch {
        // Initial discovery fails closed. A later transient poll failure keeps the
        // last known policy so an active session does not flap on a network blip.
        if (active && firstLoad) {
          setTerminalFeatureEnabled(false)
          setTerminalOpen(false)
        }
      } finally {
        firstLoad = false
      }
    }
    refresh()
    const timer = window.setInterval(refresh, 30_000)
    return () => {
      active = false
      window.clearInterval(timer)
    }
  }, [user, setTerminalFeatureEnabled, setTerminalMaxSessions, setTerminalOpen])

  const maybeAutoOpenIntro = useCallback((currentUser) => {
    const key = getIntroSeenKey(currentUser)
    if (!key || safeStorage.getItem(key)) return

    safeStorage.setItem(key, String(Date.now()))
    openIntro()
  }, [openIntro])

  // Show setup wizard if user has no env configured.
  // After the initial setup flow is completed or skipped, auto-open the intro once.
  useEffect(() => {
    if (!user) {
      setShowSetupWizard(false)
      return
    }

    if (hasEnv == null) return

    if (hasEnv === false) {
      const dismissed = safeStorage.getItem('env-setup-dismissed')
      if (dismissed) {
        const elapsed = Date.now() - parseInt(dismissed, 10)
        if (elapsed < 24 * 60 * 60 * 1000) {
          setShowSetupWizard(false)
          maybeAutoOpenIntro(user)
          return
        }
      }

      if (!showSetupWizard) {
        setShowSetupWizard(true)
      }
      return
    }

    if (showSetupWizard) return
    maybeAutoOpenIntro(user)
  }, [user, hasEnv, maybeAutoOpenIntro, showSetupWizard])

  const handleSetupWizardComplete = useCallback(() => {
    setShowSetupWizard(false)
    maybeAutoOpenIntro(user)
  }, [maybeAutoOpenIntro, user])

  if (loading) {
    return <div style={{ minHeight: '100vh', background: 'var(--bg-base)' }} />
  }

  if (needsSetup && !user) {
    return <SetupPage />
  }

  if (!user) {
    return <LoginPage />
  }

  return (
    <>
      <ConnectionBanner />
      <ErrorBoundary resetKey={user?.username}>
        <MainLayout />
      </ErrorBoundary>
      <ConfirmDialog />
      <SettingsOverlay />
      <ToastStack />
      {introOpen && (
        <Suspense fallback={null}>
          <IntroPanel />
        </Suspense>
      )}
      {(showSetupWizard || setupWizardShownRef.current) && (
        <Suspense fallback={null}>
          <SetupWizardModal open={showSetupWizard} onComplete={handleSetupWizardComplete} />
        </Suspense>
      )}
    </>
  )
}
