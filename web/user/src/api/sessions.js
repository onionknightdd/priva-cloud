import { getAuthHeaders, handleAPIResponse, fetchWithWake } from '@shared/api/client'

const BASE_URL = '/api/sandbox'

// Grouped-by-cwd listing (no cwd param) — { groups: [{cwd,total,sessions,has_more,last_activity}], active_cwd }.
export async function fetchSessionsGrouped() {
  const res = await fetchWithWake(`${BASE_URL}/agent/sessions`, {
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

// One cwd's page — backs the per-group "more in this dir" loader.
// Returns { cwd, sessions, total, limit, offset }.
export async function fetchCwdSessions(cwd, limit = 20, offset = 0) {
  const params = new URLSearchParams()
  params.set('cwd', cwd)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  const res = await fetchWithWake(`${BASE_URL}/agent/sessions?${params}`, {
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}

// Persist a session's additional directories (SDK --add-dir), saved server-side
// so a resume on any device recovers them.
export async function setSessionAddDirs(sessionId, addDirs) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/add_dirs`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ add_dirs: addDirs || [] }),
    }
  )
  return handleAPIResponse(res)
}

export async function deleteSession(sessionId) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}`,
    { method: 'DELETE', headers: { ...getAuthHeaders() } }
  )
  return handleAPIResponse(res)
}

export async function fetchSessionMessages(sessionId, limit, offset) {
  const params = new URLSearchParams()
  if (limit != null) params.set('limit', String(limit))
  if (offset != null && offset !== 0) params.set('offset', String(offset))
  const qs = params.toString()
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/messages${qs ? '?' + qs : ''}`,
    { headers: { ...getAuthHeaders() } }
  )
  return handleAPIResponse(res)
}

async function handleJson(res) {
  return handleAPIResponse(res)
}

export async function rewindFiles(sessionId, checkpointUuid) {
  const res = await fetchWithWake(`${BASE_URL}/agent/rewind`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ session_id: sessionId, checkpoint_uuid: checkpointUuid }),
  })
  return handleJson(res)
}

export async function forkSession(sessionId, upToMessageUuid, title) {
  const body = { session_id: sessionId }
  if (upToMessageUuid) body.up_to_message_uuid = upToMessageUuid
  if (title) body.title = title
  const res = await fetchWithWake(`${BASE_URL}/agent/fork`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify(body),
  })
  return handleJson(res)
}

export async function renameSession(sessionId, title) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}`,
    {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ title }),
    }
  )
  return handleJson(res)
}

export async function tagSession(sessionId, tag) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/tag`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ tag: tag ?? null }),
    }
  )
  return handleJson(res)
}

// Pin/unpin a session — keeps it at the top of its workdir group.
export async function pinSession(sessionId, pinned) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/pin`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ pinned: !!pinned }),
    }
  )
  return handleJson(res)
}

// Archive/unarchive a session — hides it from the default list.
export async function archiveSession(sessionId, archived) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/archive`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      body: JSON.stringify({ archived: !!archived }),
    }
  )
  return handleJson(res)
}

// Pin/unpin a whole workdir — floats the cwd group toward the top.
export async function pinWorkdir(cwd, pinned) {
  const res = await fetchWithWake(`${BASE_URL}/agent/workdirs/pin`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ cwd, pinned: !!pinned }),
  })
  return handleJson(res)
}

// Archive a whole workdir — cascades archive to every session in it.
export async function archiveWorkdir(cwd) {
  const res = await fetchWithWake(`${BASE_URL}/agent/workdirs/archive`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
    body: JSON.stringify({ cwd }),
  })
  return handleJson(res)
}

// Every archived session across all cwds — backs Settings → Archived.
export async function fetchArchivedSessions() {
  const res = await fetchWithWake(`${BASE_URL}/agent/sessions?archived=true`, {
    headers: { ...getAuthHeaders() },
  })
  return handleAPIResponse(res)
}
