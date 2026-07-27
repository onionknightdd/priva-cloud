import { sandboxGet, sandboxRead, sandboxPut, sandboxDelete, getBackendOrigin, fetchWithWake } from '@shared/api/client'
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
// Skill lists/details can exceed the agentgateway ext_proc response buffer when
// SKILL.md bodies are included, so read them through the control-panel proxy.
export const listSkills = () => sandboxRead('/resource/skills/')

export const getSkillDetail = (scope, cwd, name) =>
  sandboxRead(`/resource/skills/detail?${skillQuery({ scope, cwd, name })}`)

export const getSkillFile = (scope, cwd, name, path) =>
  sandboxRead(`/resource/skills/file?${skillQuery({ scope, cwd, name, path })}`)

// cp-proxy: skill archives are multipart bodies far past the ~8KB EPP request cap —
// the direct lane mangles them into a 422 "file field required".
export const uploadSkill = async (scope, cwd, file) => {
  const formData = new FormData()
  formData.append('file', file)
  formData.append('scope', scope)
  if (cwd != null) formData.append('cwd', cwd)
  const init = { method: 'POST', headers: { ...getAuthHeaders() }, body: formData }
  let res
  try {
    res = await fetchWithWake('/api/cp-proxy/resource/skills/upload', init)
    if (res.status === 404) {
      res = await fetchWithWake(`${BASE_URL}/resource/skills/upload`, init)
    }
  } catch {
    res = await fetchWithWake(`${BASE_URL}/resource/skills/upload`, init)
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

export const deleteSkill = (scope, cwd, name) =>
  sandboxDelete(`/resource/skills/item?${skillQuery({ scope, cwd, name })}`)

// cp-proxy: skill tar.gz archives run well past the ~8KB EPP response cap.
export async function downloadSkill(scope, cwd, name) {
  const qs = skillQuery({ scope, cwd, name })
  const init = { headers: { ...getAuthHeaders() } }
  let res
  try {
    res = await fetchWithWake(`/api/cp-proxy/resource/skills/download?${qs}`, init)
    if (res.status === 404) {
      res = await fetchWithWake(`${BASE_URL}/resource/skills/download?${qs}`, init)
    }
  } catch {
    res = await fetchWithWake(`${BASE_URL}/resource/skills/download?${qs}`, init)
  }
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
