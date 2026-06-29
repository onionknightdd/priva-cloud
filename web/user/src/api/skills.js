import { sandboxGet, sandboxPut, sandboxDelete, getBackendOrigin } from '@shared/api/client'
import { getToken } from '@shared/api/tokenStore'

const BASE_URL = '/api/sandbox'

function getAuthHeaders() {
  const token = getToken()
  if (token) {
    return { Authorization: `Bearer ${token}` }
  }
  return {}
}

// Build a ?scope=&cwd=&name=… query string. ``cwd`` is omitted for personal skills.
function skillQuery({ scope, cwd, name, path } = {}) {
  const params = new URLSearchParams()
  if (name != null) params.set('name', name)
  if (path != null) params.set('path', path)
  if (scope != null) params.set('scope', scope)
  if (cwd != null) params.set('cwd', cwd)
  return params.toString()
}

// Returns { personal: SkillSummary[], groups: [{ cwd, skills: SkillSummary[] }] }
export const listSkills = () => sandboxGet('/resource/skills/')

export const getSkillDetail = (scope, cwd, name) =>
  sandboxGet(`/resource/skills/detail?${skillQuery({ scope, cwd, name })}`)

export const getSkillFile = (scope, cwd, name, path) =>
  sandboxGet(`/resource/skills/file?${skillQuery({ scope, cwd, name, path })}`)

export const uploadSkill = async (scope, cwd, file) => {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('scope', scope)
  if (cwd != null) formData.append('cwd', cwd)
  const res = await fetch(`${BASE_URL}/resource/skills/upload`, {
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

export const deleteSkill = (scope, cwd, name) =>
  sandboxDelete(`/resource/skills/item?${skillQuery({ scope, cwd, name })}`)

export async function downloadSkill(scope, cwd, name) {
  const res = await fetch(`${BASE_URL}/resource/skills/download?${skillQuery({ scope, cwd, name })}`, {
    headers: { ...getAuthHeaders() },
  })
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:unauthorized'))
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Download error ${res.status}: ${text}`)
  }
  return res.blob()
}

export const getSkillsConfig = () => sandboxGet('/resource/skills/config')

export const updateSkillsConfig = (skillExclude) =>
  sandboxPut('/resource/skills/config', { skill_exclude: Array.isArray(skillExclude) ? skillExclude : [] })

export async function getHealthInfo() {
  const res = await fetch(`${getBackendOrigin()}/health`)
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Health error ${res.status}: ${text}`)
  }
  return res.json()
}
