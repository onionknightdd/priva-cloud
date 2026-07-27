import { sandboxGet, sandboxRead, sandboxPost, sandboxDelete, fetchWithWake } from '@shared/api/client'
import { getToken } from '@shared/api/tokenStore'

const BASE_URL = '/api/sandbox'

function getAuthHeaders() {
  const token = getToken()
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

export const listHubSkills = () => sandboxGet('/resource/skill-hub/')

export const getHubSkillDetail = (name) =>
  sandboxGet(`/resource/skill-hub/${encodeURIComponent(name)}`)

// sandboxRead: bundled-skill file content runs up to 1MB — far past the ~8KB EPP cap.
export const getHubSkillFile = (name, path) =>
  sandboxRead(`/resource/skill-hub/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`)

export const deliverHubSkill = (name) =>
  sandboxPost(`/resource/skill-hub/${encodeURIComponent(name)}/deliver`, {})

// cp-proxy: bundled-skill archives are multipart bodies far past the ~8KB EPP
// request cap — the direct lane mangles them into a 422 "file field required".
export const uploadHubSkill = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  const init = { method: 'POST', headers: { ...getAuthHeaders() }, body: formData }
  let res
  try {
    res = await fetchWithWake('/api/cp-proxy/resource/skill-hub/upload', init)
    if (res.status === 404) {
      res = await fetchWithWake(`${BASE_URL}/resource/skill-hub/upload`, init)
    }
  } catch {
    res = await fetchWithWake(`${BASE_URL}/resource/skill-hub/upload`, init)
  }
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:unauthorized'))
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`API error ${res.status}: ${text}`)
  }
  return res.json()
}

export const deleteHubSkill = (name) =>
  sandboxDelete(`/resource/skill-hub/${encodeURIComponent(name)}`)
