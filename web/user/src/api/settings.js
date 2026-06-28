import { getJSON, putJSON, sandboxGet, sandboxPut } from '@shared/api/client'

export function getUserEnv() {
  return getJSON('/auth/me/env')
}

export function updateUserEnv(env) {
  return putJSON('/auth/me/env', env)
}

export function getUserEnvStatus() {
  return getJSON('/auth/me/env/status')
}

export function fetchModels() {
  return getJSON('/resource/models')
}

export function getQuickActions() {
  return sandboxGet('/resource/quickactions')
}

export function updateQuickActions(quickactions) {
  return sandboxPut('/resource/quickactions', { quickactions })
}

export function getVisionModel() {
  return sandboxGet('/resource/vision-model')
}

export function updateVisionModel(visionModel) {
  return sandboxPut('/resource/vision-model', { vision_model: visionModel })
}
