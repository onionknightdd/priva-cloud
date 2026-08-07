import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import { DEVELOPER_MODE_KEY, DEBUG_LOGGING_KEY } from '@shared/utils/debugLog'
import {
  listLlmProfiles,
  getLlmProfile,
  createLlmProfile,
  updateLlmProfile,
  setDefaultLlmProfile,
  deleteLlmProfile,
  fetchProfileModels,
  testLlmProfile,
  getQuickActions,
  updateQuickActions as updateQuickActionsAPI,
  getRecapSetting as getRecapSettingAPI,
  updateRecapSetting as updateRecapSettingAPI,
} from '../api/settings'
import { getMyApiKey, generateMyApiKey, revokeMyApiKey } from '@shared/api/auth'
import { getPresetPrompt, updatePresetPrompt } from '@shared/api/admin'

const useSettingsStore = create((set, get) => ({
  env: null,
  hasEnv: null,
  profiles: [],
  defaultProfileId: null,
  profilesLoaded: false,
  profilesLoading: false,
  profileError: null,
  modelsByProfile: {},
  activeSettingsProfileId: null,
  models: [],
  modelsLoading: false,
  modelsLoaded: false,
  modelsError: null,
  quickActions: [],
  quickActionsLoaded: false,
  selectedModel: null,
  defaultModel: null,
  apiKey: null,
  apiKeyLoading: false,
  presetPrompt: null,
  presetPromptLoading: false,
  visionModel: null,
  // Server-side, not localStorage: this gates a per-turn model call the backend
  // makes, so the pod is the one that has to know. Optimistic default matches
  // the backend's "absent means on".
  recapEnabled: true,
  transport: safeStorage.getItem('priva-transport') || 'ws',
  // Developer Mode is the master gate; Debug Logging is one switch under it.
  // Persisted so the plain-JS API layer (debugLog) can read the flags directly.
  developerMode: safeStorage.getItem(DEVELOPER_MODE_KEY) === '1',
  debugMode: safeStorage.getItem(DEBUG_LOGGING_KEY) === '1',

  fetchProfiles: async () => {
    set({ profilesLoading: true, profileError: null })
    try {
      const data = await listLlmProfiles()
      const profiles = data.profiles || []
      const defaultProfileId = data.default_profile_id || null
      set({ profiles, defaultProfileId, profilesLoaded: true, profilesLoading: false, hasEnv: profiles.length > 0 })
      if (!get().activeSettingsProfileId && profiles[0]) set({ activeSettingsProfileId: defaultProfileId || profiles[0].id })
      return data
    } catch (err) {
      set({ profilesLoaded: true, profilesLoading: false, hasEnv: false, profileError: err.message })
      return null
    }
  },

  // Kept as a local store API for existing bootstrap callers; it now reads the
  // default profile, never the removed /credentials endpoint.
  fetchEnvStatus: async () => {
    const data = await get().fetchProfiles()
    return !!data?.default_profile_id
  },

  fetchEnv: async () => {
    try {
      const profiles = get().profiles.length ? get().profiles : (await get().fetchProfiles())?.profiles || []
      const id = get().defaultProfileId || profiles[0]?.id
      if (!id) { set({ env: null, hasEnv: false }); return { env: null, has_env: false } }
      const profile = await getLlmProfile(id)
      const env = {
        ANTHROPIC_BASE_URL: profile.base_url,
        ANTHROPIC_AUTH_TOKEN: profile.auth_token,
        ANTHROPIC_MODEL: profile.default_model || '',
        ANTHROPIC_DEFAULT_OPUS_MODEL: profile.opus_model || '',
        ANTHROPIC_DEFAULT_SONNET_MODEL: profile.sonnet_model || '',
        ANTHROPIC_DEFAULT_HAIKU_MODEL: profile.haiku_model || '',
      }
      set({ env, hasEnv: true })
      return { env, has_env: true }
    } catch {
      set({ env: null, hasEnv: false })
      return null
    }
  },

  saveEnv: async (envData) => {
    let profiles = get().profiles
    if (!profiles.length) await get().fetchProfiles()
    profiles = get().profiles
    const id = get().defaultProfileId || 'default'
    const existing = profiles.find((p) => p.id === id)
    const payload = {
      id,
      label: existing?.label || 'Default',
      base_url: envData.ANTHROPIC_BASE_URL || existing?.base_url || '',
      auth_token: envData.ANTHROPIC_AUTH_TOKEN || existing?.auth_token || '',
      default_model: envData.ANTHROPIC_MODEL ?? existing?.default_model ?? null,
      opus_model: envData.ANTHROPIC_DEFAULT_OPUS_MODEL ?? existing?.opus_model ?? null,
      sonnet_model: envData.ANTHROPIC_DEFAULT_SONNET_MODEL ?? existing?.sonnet_model ?? null,
      haiku_model: envData.ANTHROPIC_DEFAULT_HAIKU_MODEL ?? existing?.haiku_model ?? null,
      vision_model: existing?.vision_model || null,
    }
    const profile = existing ? await updateLlmProfile(id, payload) : await createLlmProfile(payload)
    if (!existing) set({ defaultProfileId: id })
    set({ env: envData, hasEnv: true, profiles: existing ? get().profiles.map((p) => p.id === id ? { ...p, ...profile } : p) : [...get().profiles, profile] })
    return { env: envData, has_env: true }
  },

  fetchModels: async () => {
    set({ modelsLoading: true, modelsError: null })
    try {
      const profileId = get().defaultProfileId || get().profiles[0]?.id
      if (!profileId) throw new Error('No LLM profile configured')
      const data = await fetchProfileModels(profileId)
      const models = data.models || []
      set({ models, modelsLoading: false, modelsLoaded: true, modelsByProfile: { ...get().modelsByProfile, [profileId]: { models, loading: false, loaded: true, error: null } } })
      return models
    } catch (err) {
      set({ modelsLoading: false, modelsLoaded: true, modelsError: err.message })
      return []
    }
  },

  fetchModelsForProfile: async (profileId, force = false) => {
    const cached = get().modelsByProfile[profileId]
    if (cached?.loaded && !force) return cached.models || []
    set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: { ...(cached || {}), loading: true, error: null } } })
    try {
      const data = await fetchProfileModels(profileId)
      const models = data.models || []
      set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: { models, loading: false, loaded: true, error: null } } })
      return models
    } catch (err) {
      set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: { ...(get().modelsByProfile[profileId] || {}), loading: false, loaded: true, error: err.message } } })
      return []
    }
  },

  getProfile: async (profileId) => getLlmProfile(profileId),
  createProfile: async (profile) => { const created = await createLlmProfile(profile); await get().fetchProfiles(); return created },
  updateProfile: async (profileId, patch) => { const updated = await updateLlmProfile(profileId, patch); await get().fetchProfiles(); set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: undefined } }); return updated },
  setDefaultProfile: async (profileId) => { await setDefaultLlmProfile(profileId); set({ defaultProfileId: profileId }); await get().fetchProfiles() },
  deleteProfile: async (profileId) => { await deleteLlmProfile(profileId); set({ modelsByProfile: Object.fromEntries(Object.entries(get().modelsByProfile).filter(([id]) => id !== profileId)) }); await get().fetchProfiles() },
  testProfile: async (profileId) => { const data = await testLlmProfile(profileId); const models = data.models || []; set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: { models, loading: false, loaded: true, error: null } } }); return models },

  fetchQuickActions: async () => {
    try {
      const data = await getQuickActions()
      set({ quickActions: data.quickactions || [], quickActionsLoaded: true })
      return data.quickactions || []
    } catch {
      set({ quickActions: [], quickActionsLoaded: true })
      return []
    }
  },

  saveQuickActions: async (actions) => {
    const data = await updateQuickActionsAPI(actions)
    set({ quickActions: data.quickactions || [], quickActionsLoaded: true })
    return data
  },

  fetchVisionModel: async () => {
    try {
      const id = get().defaultProfileId || get().profiles[0]?.id
      const profile = id ? await getLlmProfile(id) : null
      const value = profile?.vision_model || null
      set({ visionModel: value })
      return value
    } catch {
      return null
    }
  },

  saveVisionModel: async (model) => {
    const id = get().defaultProfileId || get().profiles[0]?.id
    if (id) await updateLlmProfile(id, { vision_model: model || null })
    set({ visionModel: model || null })
  },

  fetchRecapEnabled: async () => {
    try {
      const data = await getRecapSettingAPI()
      set({ recapEnabled: data.recap_enabled !== false })
      return data.recap_enabled !== false
    } catch {
      return get().recapEnabled
    }
  },

  saveRecapEnabled: async (enabled) => {
    const next = !!enabled
    const prev = get().recapEnabled
    set({ recapEnabled: next })
    try {
      await updateRecapSettingAPI(next)
    } catch (e) {
      // The toggle drives backend behaviour, so a failed write must not leave
      // the UI claiming a state the pod never got.
      set({ recapEnabled: prev })
      throw e
    }
  },

  setTransport: (t) => {
    safeStorage.setItem('priva-transport', t)
    set({ transport: t })
  },

  setDeveloperMode: (on) => {
    safeStorage.setItem(DEVELOPER_MODE_KEY, on ? '1' : '0')
    set({ developerMode: on })
  },

  setDebugMode: (on) => {
    safeStorage.setItem(DEBUG_LOGGING_KEY, on ? '1' : '0')
    set({ debugMode: on })
  },

  setSelectedModel: (model) => set({ selectedModel: model }),

  fetchApiKey: async () => {
    set({ apiKeyLoading: true })
    try {
      const data = await getMyApiKey()
      set({ apiKey: data, apiKeyLoading: false })
      return data
    } catch {
      set({ apiKey: null, apiKeyLoading: false })
      return null
    }
  },

  generateApiKey: async () => {
    set({ apiKeyLoading: true })
    try {
      const data = await generateMyApiKey()
      set({ apiKey: data, apiKeyLoading: false })
      return data
    } catch {
      set({ apiKeyLoading: false })
      return null
    }
  },

  revokeApiKey: async () => {
    set({ apiKeyLoading: true })
    try {
      const data = await revokeMyApiKey()
      set({ apiKey: data, apiKeyLoading: false })
      return data
    } catch {
      set({ apiKeyLoading: false })
      return null
    }
  },

  fetchPresetPrompt: async () => {
    set({ presetPromptLoading: true })
    try {
      const data = await getPresetPrompt()
      set({ presetPrompt: data, presetPromptLoading: false })
      return data
    } catch {
      set({ presetPromptLoading: false })
      return null
    }
  },

  savePresetPrompt: async (data) => {
    const result = await updatePresetPrompt(data)
    set({ presetPrompt: result })
    return result
  },

  reset: () => set({
    env: null,
    hasEnv: null,
    profiles: [],
    defaultProfileId: null,
    profilesLoaded: false,
    profilesLoading: false,
    profileError: null,
    modelsByProfile: {},
    activeSettingsProfileId: null,
    models: [],
    modelsLoading: false,
    modelsLoaded: false,
    modelsError: null,
    quickActions: [],
    quickActionsLoaded: false,
    selectedModel: null,
    defaultModel: null,
    apiKey: null,
    apiKeyLoading: false,
    presetPrompt: null,
    presetPromptLoading: false,
    visionModel: null,
    recapEnabled: true,
    transport: safeStorage.getItem('priva-transport') || 'ws',
    developerMode: safeStorage.getItem(DEVELOPER_MODE_KEY) === '1',
    debugMode: safeStorage.getItem(DEBUG_LOGGING_KEY) === '1',
  }),
}))

export default useSettingsStore
