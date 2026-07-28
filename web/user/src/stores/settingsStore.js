import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import { DEVELOPER_MODE_KEY, DEBUG_LOGGING_KEY } from '@shared/utils/debugLog'
import {
  getUserEnv,
  updateUserEnv,
  getUserEnvStatus,
  fetchModels as fetchModelsAPI,
  getQuickActions,
  updateQuickActions as updateQuickActionsAPI,
  getVisionModel as getVisionModelAPI,
  updateVisionModel as updateVisionModelAPI,
  getRecapSetting as getRecapSettingAPI,
  updateRecapSetting as updateRecapSettingAPI,
} from '../api/settings'
import { getMyApiKey, generateMyApiKey, revokeMyApiKey } from '@shared/api/auth'
import { getPresetPrompt, updatePresetPrompt } from '@shared/api/admin'

const useSettingsStore = create((set, get) => ({
  env: null,
  hasEnv: null,
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

  fetchEnvStatus: async () => {
    try {
      const data = await getUserEnvStatus()
      set({ hasEnv: data.has_env })
      return data.has_env
    } catch {
      set({ hasEnv: false })
      return false
    }
  },

  fetchEnv: async () => {
    try {
      const data = await getUserEnv()
      set({ env: data.env, hasEnv: data.has_env })
      return data
    } catch {
      set({ env: null, hasEnv: false })
      return null
    }
  },

  saveEnv: async (envData) => {
    const data = await updateUserEnv(envData)
    set({ env: data.env, hasEnv: data.has_env })
    return data
  },

  fetchModels: async () => {
    set({ modelsLoading: true, modelsError: null })
    try {
      const data = await fetchModelsAPI()
      set({ models: data.models || [], modelsLoading: false, modelsLoaded: true })
      return data.models || []
    } catch (err) {
      set({ modelsLoading: false, modelsLoaded: true, modelsError: err.message })
      return []
    }
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
      const data = await getVisionModelAPI()
      set({ visionModel: data.vision_model || null })
      return data.vision_model || null
    } catch {
      return null
    }
  },

  saveVisionModel: async (model) => {
    await updateVisionModelAPI(model || null)
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
