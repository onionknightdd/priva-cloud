import { sandboxGet, sandboxRead, sandboxPut } from '@shared/api/client'

// BYOK creds now live in the account's agent-runner (settings.json), reached
// through agentgateway via the /api/sandbox/* face — not the control-panel.
export function getUserEnv() {
  return sandboxGet('/credentials')
}

export function updateUserEnv(env) {
  return sandboxPut('/credentials', env)
}

export function getUserEnvStatus() {
  return sandboxGet('/credentials/status')
}

// sandboxRead: aggregator providers return hundreds of model ids — can exceed the ~8KB EPP cap.
export function fetchModels() {
  return sandboxRead('/credentials/models')
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
