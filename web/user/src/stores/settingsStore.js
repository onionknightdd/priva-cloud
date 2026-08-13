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
  testLlmProfileDraft,
  probeProfileImageCapability,
  getQuickActions,
  updateQuickActions as updateQuickActionsAPI,
  getRecapSetting as getRecapSettingAPI,
  updateRecapSetting as updateRecapSettingAPI,
  getRuntimeSettings as getRuntimeSettingsAPI,
  updateRuntimeSettings as updateRuntimeSettingsAPI,
} from '../api/settings'
import { getMyApiKey, generateMyApiKey, revokeMyApiKey } from '@shared/api/auth'

const SESSION_MODEL_DRAFT_KEY = '__draft__'
const storedRunMode = safeStorage.getItem('priva-run-mode')
export const RUN_MODE_CHANNEL = 'priva-run-mode'

function hasOwn(object, key) {
  return Object.prototype.hasOwnProperty.call(object, key)
}

function runtimeFeatureState(data) {
  return {
    env: data?.extra_env || {},
    extraEnvEnabled: data?.extra_env_enabled === true,
    promptSuggestionEnabled: data?.prompt_suggestion_enabled === true,
    agentTeamsEnabled: data?.agent_teams_enabled === true,
    crossSessionInteractionEnabled: data?.cross_session_interaction_enabled === true,
  }
}

function modelSelectionFromResponse(lastResponseModel, profiles, defaultProfileId) {
  const modelId = lastResponseModel?.model_id || lastResponseModel?.modelId
  if (typeof modelId !== 'string' || !modelId.trim()) return null

  const profileId = lastResponseModel?.profile_id || lastResponseModel?.profileId
  if (!profileId) {
    const defaultProfile = profiles.find((profile) => profile.id === defaultProfileId)
    return defaultProfile?.default_model === modelId ? null : modelId
  }

  // A profile-qualified reference is valid even before the profile list has
  // finished loading. Once profiles are available, do not restore a deleted
  // profile as the next-run selection.
  if (profiles.length > 0 && !profiles.some((profile) => profile.id === profileId)) return null
  const profile = profiles.find((item) => item.id === profileId)
  if (profileId === defaultProfileId && profile?.default_model === modelId) return null
  return `${profileId}:${modelId}`
}

const useSettingsStore = create((set, get) => ({
  // User-defined runtime environment only. LLM credentials live exclusively
  // in profiles and must never be mixed into this map.
  env: {},
  extraEnvEnabled: false,
  promptSuggestionEnabled: false,
  agentTeamsEnabled: false,
  crossSessionInteractionEnabled: false,
  runtimeSettingsLoaded: false,
  runtimeSettingsLoading: false,
  runtimeSettingsSaving: false,
  runtimeSettingsError: null,
  draftRunMode: storedRunMode === 'code' ? 'code' : 'agent',
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
  // The model picker is rendered globally, but its selection belongs to the
  // active conversation. The server-side last_response_model seeds a session
  // after a refresh; an explicit local selection takes precedence thereafter.
  activeModelSessionKey: null,
  selectedModelBySession: {},
  selectedModelSourceBySession: {},
  defaultModel: null,
  apiKey: null,
  apiKeyLoading: false,
  visionModel: null,
  // Server-side, not localStorage: this gates a per-turn model call the backend
  // makes, so the pod is the one that has to know. Optimistic default matches
  // the backend's "absent means on".
  recapEnabled: true,
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
      const defaultProfile = profiles.find((profile) => profile.id === defaultProfileId) || profiles[0]
      set({ profiles, defaultProfileId, profilesLoaded: true, profilesLoading: false, hasEnv: profiles.length > 0, visionModel: defaultProfile?.vision_model || null })
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

  fetchDefaultProfileEnv: async () => {
    try {
      const profiles = get().profiles.length ? get().profiles : (await get().fetchProfiles())?.profiles || []
      const id = get().defaultProfileId || profiles[0]?.id
      if (!id) return { env: null, has_env: false }
      const profile = await getLlmProfile(id)
      const env = {
        ANTHROPIC_BASE_URL: profile.base_url,
        ANTHROPIC_AUTH_TOKEN: profile.auth_token,
        ANTHROPIC_MODEL: profile.default_model || '',
        ANTHROPIC_DEFAULT_OPUS_MODEL: profile.opus_model || '',
        ANTHROPIC_DEFAULT_SONNET_MODEL: profile.sonnet_model || '',
        ANTHROPIC_DEFAULT_HAIKU_MODEL: profile.haiku_model || '',
      }
      return { env, has_env: true }
    } catch {
      return null
    }
  },

  saveDefaultProfile: async (envData) => {
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
    set({ hasEnv: true, profiles: existing ? get().profiles.map((p) => p.id === id ? { ...p, ...profile } : p) : [...get().profiles, profile] })
    return { env: envData, has_env: true }
  },

  fetchRuntimeSettings: async () => {
    set({ runtimeSettingsLoading: true, runtimeSettingsError: null })
    try {
      const data = await getRuntimeSettingsAPI()
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsLoaded: true,
        runtimeSettingsLoading: false,
      })
      return data
    } catch (err) {
      set({
        runtimeSettingsLoaded: true,
        runtimeSettingsLoading: false,
        runtimeSettingsError: err?.message || String(err),
      })
      return null
    }
  },

  saveRuntimeEnv: async (envData, enabled = get().extraEnvEnabled) => {
    const previous = { env: get().env, extraEnvEnabled: get().extraEnvEnabled }
    set({
      env: { ...envData },
      extraEnvEnabled: !!enabled,
      runtimeSettingsSaving: true,
      runtimeSettingsError: null,
    })
    try {
      const data = await updateRuntimeSettingsAPI({
        extra_env: envData,
        extra_env_enabled: !!enabled,
      })
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsSaving: false,
        runtimeSettingsLoaded: true,
      })
      return data
    } catch (err) {
      set({
        ...previous,
        runtimeSettingsSaving: false,
        runtimeSettingsError: err?.message || String(err),
      })
      throw err
    }
  },

  setRuntimeEnvEnabled: async (enabled) => {
    const previous = get().extraEnvEnabled
    set({ extraEnvEnabled: !!enabled, runtimeSettingsError: null })
    try {
      const data = await updateRuntimeSettingsAPI({ extra_env_enabled: !!enabled })
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsLoaded: true,
      })
      return data
    } catch (err) {
      set({ extraEnvEnabled: previous, runtimeSettingsError: err?.message || String(err) })
      throw err
    }
  },

  setPromptSuggestionEnabled: async (enabled) => {
    const previous = get().promptSuggestionEnabled
    set({ promptSuggestionEnabled: !!enabled, runtimeSettingsError: null })
    try {
      const data = await updateRuntimeSettingsAPI({ prompt_suggestion_enabled: !!enabled })
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsLoaded: true,
      })
      return data
    } catch (err) {
      set({ promptSuggestionEnabled: previous, runtimeSettingsError: err?.message || String(err) })
      throw err
    }
  },

  setAgentTeamsEnabled: async (enabled) => {
    const previous = get().agentTeamsEnabled
    set({ agentTeamsEnabled: !!enabled, runtimeSettingsError: null })
    try {
      const data = await updateRuntimeSettingsAPI({ agent_teams_enabled: !!enabled })
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsLoaded: true,
      })
      return data
    } catch (err) {
      set({ agentTeamsEnabled: previous, runtimeSettingsError: err?.message || String(err) })
      throw err
    }
  },

  setCrossSessionInteractionEnabled: async (enabled) => {
    const previous = get().crossSessionInteractionEnabled
    set({
      crossSessionInteractionEnabled: !!enabled,
      runtimeSettingsSaving: true,
      runtimeSettingsError: null,
    })
    try {
      const data = await updateRuntimeSettingsAPI({
        cross_session_interaction_enabled: !!enabled,
      })
      set({
        ...runtimeFeatureState(data),
        runtimeSettingsSaving: false,
        runtimeSettingsLoaded: true,
      })
      return data
    } catch (err) {
      set({
        crossSessionInteractionEnabled: previous,
        runtimeSettingsSaving: false,
        runtimeSettingsError: err?.message || String(err),
      })
      throw err
    }
  },

  setDraftRunMode: (mode, { broadcast = true } = {}) => {
    const next = mode === 'code' ? 'code' : 'agent'
    safeStorage.setItem('priva-run-mode', next)
    set({ draftRunMode: next })
    if (!broadcast || typeof window === 'undefined') return
    window.dispatchEvent(new CustomEvent('priva:run-mode', { detail: next }))
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        const channel = new BroadcastChannel(RUN_MODE_CHANNEL)
        channel.postMessage({ type: 'run-mode', runMode: next })
        channel.close()
      } catch {
        // local state and storage persistence still succeeded
      }
    }
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
  testProfileDraft: async (profile) => { const data = await testLlmProfileDraft(profile); const models = data.models || []; const profileId = profile?.id; if (profileId) set({ modelsByProfile: { ...get().modelsByProfile, [profileId]: { models, loading: false, loaded: true, error: null } } }); return models },
  probeImageCapability: async (profileId, modelId) => {
    const result = await probeProfileImageCapability(profileId, modelId)
    await get().fetchProfiles()
    return result
  },

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
      let profiles = get().profiles
      if (!profiles.length) {
        await get().fetchProfiles()
        profiles = get().profiles
      }
      const id = get().defaultProfileId || profiles[0]?.id
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
    set({
      visionModel: model || null,
      profiles: get().profiles.map((profile) => profile.id === id ? { ...profile, vision_model: model || null } : profile),
    })
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

  setDeveloperMode: (on) => {
    safeStorage.setItem(DEVELOPER_MODE_KEY, on ? '1' : '0')
    set({ developerMode: on })
  },

  setDebugMode: (on) => {
    safeStorage.setItem(DEBUG_LOGGING_KEY, on ? '1' : '0')
    set({ debugMode: on })
  },

  activateSessionModel: (sessionKey, lastResponseModel = null, options = {}) => {
    const key = sessionKey || SESSION_MODEL_DRAFT_KEY
    const current = get()
    const selections = current.selectedModelBySession || {}
    const sources = current.selectedModelSourceBySession || {}
    if (hasOwn(selections, key) && sources[key] === 'explicit') {
      set({ activeModelSessionKey: key, selectedModel: selections[key] })
      return selections[key]
    }

    const selectedModel = options.preserveCurrent
      ? current.selectedModel
      : modelSelectionFromResponse(lastResponseModel, current.profiles, current.defaultProfileId)
    set({
      activeModelSessionKey: key,
      selectedModel,
      selectedModelBySession: { ...selections, [key]: selectedModel },
      selectedModelSourceBySession: { ...sources, [key]: 'server' },
    })
    return selectedModel
  },

  rekeySessionModel: (oldKey, newKey) => {
    if (!oldKey || !newKey || oldKey === newKey) return
    set((state) => {
      const selections = state.selectedModelBySession || {}
      const sources = state.selectedModelSourceBySession || {}
      if (!hasOwn(selections, oldKey)) {
        return state.activeModelSessionKey === oldKey
          ? { activeModelSessionKey: newKey }
          : {}
      }
      const next = { ...selections, [newKey]: selections[oldKey] }
      const nextSources = { ...sources, [newKey]: sources[oldKey] || 'server' }
      delete next[oldKey]
      delete nextSources[oldKey]
      return {
        activeModelSessionKey: state.activeModelSessionKey === oldKey
          ? newKey
          : state.activeModelSessionKey,
        selectedModelBySession: next,
        selectedModelSourceBySession: nextSources,
      }
    })
  },

  setSelectedModel: (model) => set((state) => {
    const key = state.activeModelSessionKey || SESSION_MODEL_DRAFT_KEY
    return {
      selectedModel: model,
      selectedModelBySession: {
        ...(state.selectedModelBySession || {}),
        [key]: model,
      },
      selectedModelSourceBySession: {
        ...(state.selectedModelSourceBySession || {}),
        [key]: 'explicit',
      },
    }
  }),

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

  reset: () => set({
    env: {},
    extraEnvEnabled: false,
    promptSuggestionEnabled: false,
    agentTeamsEnabled: false,
    crossSessionInteractionEnabled: false,
    runtimeSettingsLoaded: false,
    runtimeSettingsLoading: false,
    runtimeSettingsSaving: false,
    runtimeSettingsError: null,
    draftRunMode: safeStorage.getItem('priva-run-mode') === 'code' ? 'code' : 'agent',
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
    activeModelSessionKey: null,
    selectedModelBySession: {},
    selectedModelSourceBySession: {},
    defaultModel: null,
    apiKey: null,
    apiKeyLoading: false,
    visionModel: null,
    recapEnabled: true,
    developerMode: safeStorage.getItem(DEVELOPER_MODE_KEY) === '1',
    debugMode: safeStorage.getItem(DEBUG_LOGGING_KEY) === '1',
  }),
}))

export default useSettingsStore
