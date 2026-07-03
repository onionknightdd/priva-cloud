import { getAuthHeaders, sandboxDelete, sandboxRead } from '@shared/api/client'

const BASE_URL = '/api/sandbox'

export async function uploadFile(file) {
  const formData = new FormData()
  formData.append('file', file)
  const res = await fetch(`${BASE_URL}/agent-attachments/upload`, {
    method: 'POST',
    headers: { ...getAuthHeaders() },
    body: formData,
  })
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

export async function downloadFile(uuid) {
  const res = await fetch(`${BASE_URL}/agent-attachments/${encodeURIComponent(uuid)}`, {
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
