import {
  SANDBOX_BASE,
  fetchWithWake,
  getAuthHeaders,
  handleAPIResponse,
  sandboxPost,
  sandboxRead,
} from '@shared/api/client'
import { getToken } from '@shared/api/tokenStore'

function buildFileQuery(path, cacheBustKey = null) {
  const query = new URLSearchParams({ path })
  if (cacheBustKey !== null && cacheBustKey !== undefined && cacheBustKey !== '') {
    query.set('_priva_refresh', String(cacheBustKey))
  }
  return query
}

// sandboxRead: big directories (node_modules-scale) run well past the ~8KB EPP cap.
export function listDirectory(path, options = {}) {
  const { silentNotFound = false } = options
  return sandboxRead(
    `/files/list?path=${encodeURIComponent(path)}`,
    silentNotFound ? { silentStatuses: [404] } : undefined,
  )
}

export function createDirectory(directory, name) {
  return sandboxPost('/files/mkdir', { directory, name })
}

// sandboxRead: text previews carry up to 1MB of file content in JSON.
export function previewFile(path, options = {}) {
  const { cacheBustKey = null, silentNotFound = false } = options
  return sandboxRead(
    `/files/preview?${buildFileQuery(path, cacheBustKey).toString()}`,
    silentNotFound ? { silentStatuses: [404] } : undefined,
  )
}

/**
 * Passive existence/metadata probe for assistant inline-code references.
 *
 * This intentionally uses only the cp-proxy lane: a missing candidate is the
 * expected outcome for most inline code and must not trigger the normal 404
 * fallback, wake/ready UI, or an API error toast.
 */
export async function probeFilePreview(path, options = {}) {
  const { signal } = options
  let res
  try {
    res = await fetchWithWake(
      `/api/cp-proxy/files/preview?${buildFileQuery(path).toString()}`,
      { headers: { ...getAuthHeaders() }, signal },
      { surfaceLifecycle: false },
    )
  } catch {
    return null
  }

  if (res.status !== 200) {
    try {
      // Preserve global auth-revocation handling while keeping this passive
      // enhancement silent for every ordinary non-200 response.
      await handleAPIResponse(res, { silent: true })
    } catch { /* non-200 is not a file link */ }
    return null
  }

  try {
    const data = await handleAPIResponse(res, { silent: true })
    if (!data || typeof data !== 'object') return null
    return {
      path: typeof data.path === 'string' ? data.path : path,
      name: typeof data.name === 'string' ? data.name : null,
      mimeType: typeof data.mime_type === 'string' ? data.mime_type : null,
      size: typeof data.size === 'number' ? data.size : null,
    }
  } catch {
    return null
  }
}

export async function downloadFile(path, options = {}) {
  const { cacheBustKey = null, cacheMode } = options
  const query = buildFileQuery(path, cacheBustKey)
  const init = {
    headers: { ...getAuthHeaders() },
    cache: cacheMode || (cacheBustKey ? 'no-store' : 'default'),
  }

  let res
  try {
    res = await fetchWithWake(`/api/cp-proxy/files/download?${query.toString()}`, init)
    if (res.status === 404) {
      res = await fetchWithWake(`${SANDBOX_BASE}/files/download?${query.toString()}`, init)
    }
  } catch {
    res = await fetchWithWake(`${SANDBOX_BASE}/files/download?${query.toString()}`, init)
  }
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:unauthorized'))
  }
  if (!res.ok) {
    const text = await res.text()
    const error = new Error(`Download error ${res.status}: ${text}`)
    error.status = res.status
    throw error
  }
  return res.blob()
}

export function uploadUserFile(directory, file, onProgress) {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', '/api/cp-proxy/files/upload')
    const token = getToken()
    if (token) xhr.setRequestHeader('Authorization', `Bearer ${token}`)

    if (onProgress) {
      xhr.upload.onprogress = (e) => {
        if (e.lengthComputable) onProgress(e.loaded / e.total)
      }
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve(JSON.parse(xhr.responseText))
      } else {
        reject(new Error(`Upload error ${xhr.status}: ${xhr.responseText}`))
      }
    }
    xhr.onerror = () => reject(new Error('Upload failed'))

    const formData = new FormData()
    formData.append('file', file)
    formData.append('directory', directory)
    xhr.send(formData)
  })
}
