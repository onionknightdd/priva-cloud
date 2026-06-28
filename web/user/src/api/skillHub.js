import { sandboxGet, sandboxPost, sandboxDelete } from '@shared/api/client'
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

export const getHubSkillFile = (name, path) =>
  sandboxGet(`/resource/skill-hub/${encodeURIComponent(name)}/file?path=${encodeURIComponent(path)}`)

export const deliverHubSkill = (name) =>
  sandboxPost(`/resource/skill-hub/${encodeURIComponent(name)}/deliver`, {})

export const uploadHubSkill = async (file) => {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${BASE_URL}/resource/skill-hub/upload`, {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
  })
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
