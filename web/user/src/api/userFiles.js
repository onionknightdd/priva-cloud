import { sandboxPost, sandboxRead, getAuthHeaders, fetchWithWake, SANDBOX_BASE } from '@shared/api/client'
import { getToken } from '@shared/api/tokenStore'

function buildFileQuery(path, cacheBustKey = null) {
  const query = new URLSearchParams({ path })
  if (cacheBustKey !== null && cacheBustKey !== undefined && cacheBustKey !== '') {
    query.set('_priva_refresh', String(cacheBustKey))
  }
  return query
}

// sandboxRead: big directories (node_modules-scale) run well past the ~8KB EPP cap.
export function listDirectory(path) {
  return sandboxRead(`/files/list?path=${encodeURIComponent(path)}`)
}

export function createDirectory(directory, name) {
  return sandboxPost('/files/mkdir', { directory, name })
}

// sandboxRead: text previews carry up to 1MB of file content in JSON.
export function previewFile(path, options = {}) {
  const { cacheBustKey = null } = options
  return sandboxRead(`/files/preview?${buildFileQuery(path, cacheBustKey).toString()}`)
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
    throw new Error(`Download error ${res.status}: ${text}`)
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
