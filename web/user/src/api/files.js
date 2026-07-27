import { getAuthHeaders, sandboxDelete, sandboxRead, fetchWithWake } from '@shared/api/client'

const BASE_URL = '/api/sandbox'

// cp-proxy: a multipart body past the ~8KB EPP cap gets mangled on the direct lane —
// the runner then 422s with "file field required". Same lane as userFiles.uploadUserFile.
export async function uploadFile(file) {
  const formData = new FormData()
  formData.append('file', file)
  const init = { method: 'POST', headers: { ...getAuthHeaders() }, body: formData }
  let res
  try {
    res = await fetchWithWake('/api/cp-proxy/agent-attachments/upload', init)
    if (res.status === 404) {
      res = await fetchWithWake(`${BASE_URL}/agent-attachments/upload`, init)
    }
  } catch {
    res = await fetchWithWake(`${BASE_URL}/agent-attachments/upload`, init)
  }
  if (res.status === 401) {
    window.dispatchEvent(new Event('auth:unauthorized'))
  }
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Upload error ${res.status}: ${text}`)
  }
  return res.json()
}

export async function deleteUploadedFile(uuid) {
  return sandboxDelete(`/agent-attachments/${encodeURIComponent(uuid)}`)
}

// sandboxRead: the unfiltered attachment index grows with account age past the ~8KB EPP cap.
export async function listUploadedFiles(date) {
  const query = date ? `?date=${encodeURIComponent(date)}` : ''
  return sandboxRead(`/agent-attachments/${query}`)
}

// cp-proxy: attachment bodies run up to 3MB — far past the ~8KB EPP response cap.
export async function downloadFile(uuid) {
  const init = { headers: { ...getAuthHeaders() } }
  let res
  try {
    res = await fetchWithWake(`/api/cp-proxy/agent-attachments/${encodeURIComponent(uuid)}`, init)
    if (res.status === 404) {
      res = await fetchWithWake(`${BASE_URL}/agent-attachments/${encodeURIComponent(uuid)}`, init)
    }
  } catch {
    res = await fetchWithWake(`${BASE_URL}/agent-attachments/${encodeURIComponent(uuid)}`, init)
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
