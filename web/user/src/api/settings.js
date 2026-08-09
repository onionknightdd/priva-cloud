import { sandboxGet, sandboxPost, sandboxPatch, sandboxPut, sandboxDelete, sandboxRead, sandboxReadPost } from '@shared/api/client'

const PROFILE_PATH = '/credentials/profiles'

export const listLlmProfiles = () => sandboxRead(PROFILE_PATH)
export const getLlmProfile = (profileId) => sandboxGet(`${PROFILE_PATH}/${encodeURIComponent(profileId)}`)
export const createLlmProfile = (profile) => sandboxPost(PROFILE_PATH, profile)
export const updateLlmProfile = (profileId, patch) => sandboxPatch(`${PROFILE_PATH}/${encodeURIComponent(profileId)}`, patch)
export const setDefaultLlmProfile = (profileId) => sandboxPut(`${PROFILE_PATH}/${encodeURIComponent(profileId)}/default`, {})
export const deleteLlmProfile = (profileId) => sandboxDelete(`${PROFILE_PATH}/${encodeURIComponent(profileId)}`)
export const fetchProfileModels = (profileId) => sandboxRead(`${PROFILE_PATH}/${encodeURIComponent(profileId)}/models`)
export const testLlmProfile = (profileId) => sandboxReadPost(`${PROFILE_PATH}/${encodeURIComponent(profileId)}/test`, {})
export const testLlmProfileDraft = (profile) => sandboxPost(`${PROFILE_PATH}/test`, profile)
export const probeProfileImageCapability = (profileId, modelId) => sandboxPost(
  `${PROFILE_PATH}/${encodeURIComponent(profileId)}/image-capability/probe`,
  { model_id: modelId },
)

export function getQuickActions() {
  return sandboxGet('/resource/quickactions')
}

export function updateQuickActions(quickactions) {
  return sandboxPut('/resource/quickactions', { quickactions })
}

export function getRecapSetting() {
  return sandboxGet('/resource/recap-setting')
}

export function updateRecapSetting(enabled) {
  return sandboxPut('/resource/recap-setting', { recap_enabled: enabled })
}
