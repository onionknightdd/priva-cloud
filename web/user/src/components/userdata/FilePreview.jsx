import { useCallback, useMemo } from 'react'
import { getAuthHeaders } from '@shared/api/client'
import FilePreviewRenderer from '../shared/FilePreviewRenderer'

async function fetchUserFile(uuid) {
  const res = await fetch(`/api/sandbox/agent-attachments/${encodeURIComponent(uuid)}`, {
    headers: { ...getAuthHeaders() },
  })
  if (!res.ok) {
    const text = await res.text().catch(() => '')
    throw new Error(`${res.status}: ${text || res.statusText}`)
  }
  return res
}

export default function FilePreview({ file }) {
  const previewFile = useMemo(
    () => ({
      name: file.original_name,
      original_name: file.original_name,
      path: file.path,
      mime_type: file.mime_type,
      ext: file.ext,
      uuid: file.uuid,
    }),
    [file.ext, file.mime_type, file.original_name, file.path, file.uuid]
  )

  const loadText = useCallback(async () => {
    const res = await fetchUserFile(file.uuid)
    return res.text()
  }, [file.uuid])

  const loadArrayBuffer = useCallback(async () => {
    const res = await fetchUserFile(file.uuid)
    return res.arrayBuffer()
  }, [file.uuid])

  const loadBlob = useCallback(async () => {
    const res = await fetchUserFile(file.uuid)
    return res.blob()
  }, [file.uuid])

  return (
    <FilePreviewRenderer
      file={previewFile}
      cacheKey={file.uuid}
      loadText={loadText}
      loadArrayBuffer={loadArrayBuffer}
      loadBlob={loadBlob}
    />
  )
}
