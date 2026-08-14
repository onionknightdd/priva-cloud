export const MODEL_CONTEXT_1M = '1m'

const MODEL_CONTEXT_1M_SUFFIX = '[1m]'

export function splitModelContext(value) {
  if (typeof value !== 'string') return { id: null, context: null }
  const model = value.trim()
  if (!model) return { id: null, context: null }
  if (model.toLowerCase().endsWith(MODEL_CONTEXT_1M_SUFFIX)) {
    const id = model.slice(0, -MODEL_CONTEXT_1M_SUFFIX.length).trim()
    return { id: id || null, context: id ? MODEL_CONTEXT_1M : null }
  }
  return { id: model, context: null }
}

export function normalizeModelCapabilities(capabilities, fallbackContext = null) {
  const context = capabilities?.context === MODEL_CONTEXT_1M
    ? MODEL_CONTEXT_1M
    : (fallbackContext === MODEL_CONTEXT_1M ? MODEL_CONTEXT_1M : null)
  return { context }
}

export function normalizeLastResponseModel(responseModel) {
  if (!responseModel || typeof responseModel !== 'object') return null

  const nestedModel = responseModel.model
  const rawModelId = nestedModel && typeof nestedModel === 'object'
    ? nestedModel.id
    : (responseModel.model_id ?? responseModel.modelId)
  const parsed = splitModelContext(rawModelId)
  if (!parsed.id) return null

  const capabilities = normalizeModelCapabilities(
    nestedModel && typeof nestedModel === 'object' ? nestedModel.capabilities : null,
    parsed.context,
  )
  const rawObservedAt = responseModel.observed_at ?? responseModel.observedAt
  return {
    profileId: responseModel.profile_id ?? responseModel.profileId ?? null,
    model: {
      id: parsed.id,
      capabilities,
    },
    observedAt: Number.isFinite(rawObservedAt) ? rawObservedAt : null,
  }
}

export function modelSelectionFromResponse(responseModel, profiles, defaultProfileId) {
  const normalized = normalizeLastResponseModel(responseModel)
  const modelId = normalized?.model?.id
  if (!modelId) return null
  const availableProfiles = Array.isArray(profiles) ? profiles : []

  const profileId = normalized.profileId
  if (!profileId) {
    const defaultProfile = availableProfiles.find((profile) => profile.id === defaultProfileId)
    return defaultProfile?.default_model === modelId ? null : modelId
  }

  // A profile-qualified reference remains valid while profiles are loading.
  // Once loaded, never restore a deleted profile as the next-run selection.
  if (availableProfiles.length > 0 && !availableProfiles.some((profile) => profile.id === profileId)) return null
  const profile = availableProfiles.find((item) => item.id === profileId)
  if (profileId === defaultProfileId && profile?.default_model === modelId) return null
  return `${profileId}:${modelId}`
}

export function modelCapabilitiesFromResponse(responseModel) {
  const normalized = normalizeLastResponseModel(responseModel)
  return normalizeModelCapabilities(normalized?.model?.capabilities)
}

export function modelReferenceForRequest(selection, capabilities, profiles, defaultProfileId) {
  const availableProfiles = Array.isArray(profiles) ? profiles : []
  let reference = typeof selection === 'string' ? selection.trim() : ''
  if (reference) {
    const parsedSelection = splitModelContext(reference)
    reference = parsedSelection.id || ''
  }
  if (capabilities?.context !== MODEL_CONTEXT_1M) return reference || null

  if (!reference) {
    const effectiveDefaultProfileId = defaultProfileId || availableProfiles[0]?.id || null
    const profile = availableProfiles.find((item) => item.id === effectiveDefaultProfileId)
    const modelId = typeof profile?.default_model === 'string'
      ? profile.default_model.trim()
      : ''
    if (!modelId) return null
    // Qualifying the default makes model ids containing colons unambiguous;
    // the backend strips the profile before passing ``model-id[1m]`` onward.
    reference = effectiveDefaultProfileId
      ? `${effectiveDefaultProfileId}:${modelId}`
      : modelId
  }

  return `${reference}${MODEL_CONTEXT_1M_SUFFIX}`
}
