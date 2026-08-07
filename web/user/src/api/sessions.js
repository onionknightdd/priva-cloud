import { getAuthHeaders, handleAPIResponse, fetchWithWake, sandboxRead } from '@shared/api/client'

const BASE_URL = '/api/sandbox'

// Grouped-by-cwd listing (no cwd param) — { groups: [{cwd,total,sessions,has_more,last_activity}], active_cwd }.
// sandboxRead: a busy account's grouped list runs well past ~8KB; ride the
// control-panel lane so the GIE/EPP ext_proc doesn't truncate it.
export async function fetchSessionsGrouped() {
  return sandboxRead('/agent/sessions')
}

// One cwd's page — backs the per-group "more in this dir" loader.
// Returns { cwd, sessions, total, limit, offset }.
export async function fetchCwdSessions(cwd, limit = 20, offset = 0) {
  const params = new URLSearchParams()
  params.set('cwd', cwd)
  params.set('limit', String(limit))
  params.set('offset', String(offset))
  return sandboxRead(`/agent/sessions?${params}`)
}

// Runs still executing on the backend (RunRegistry). Returns
// { running: [{ session_id, run_id, status, started_at, last_seq,
//   first_user_uuid, pending_permission }] }.
export async function fetchRunningSessions() {
  return sandboxRead('/agent/sessions/running')
}

// One-line recap of what a session is about — { recap, turns }, both null/0
// until the backend has generated one. `turns` is the message count the text
// was derived from, so a poll can tell a fresh recap from a stale one.
export async function fetchSessionRecap(sessionId) {
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/recap`,
    { headers: getAuthHeaders() }
  )
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
  const suffix = `${encodeURIComponent(sessionId)}/messages${qs ? '?' + qs : ''}`
  // Large/workflow transcripts run 35-300KB. sandboxRead rides the control-panel
  // "/" lane (no GIE/EPP ~8KB ext_proc truncation), falling back to the direct
  // sandbox lane on 404 (route not deployed yet) or network error.
  return sandboxRead(`/agent/sessions/${suffix}`)
}

// Full prompt + result for one workflow sub-agent, recovered from its on-disk
// transcript (agent-<id>.jsonl). The live task_progress stream only carries
// truncated promptPreview/resultPreview; the Canvas inspector fetches this
// lazily on row-expand to show the complete text. Rides the control-panel "/"
// lane (full body, no GIE/EPP ~8KB truncation), falling back to the direct
// sandbox lane on 404 (route not deployed yet) — returns { agentId, prompt, result }.
export async function fetchWorkflowAgentTranscript(agentId) {
  return sandboxRead(`/agent/workflow-agent/${encodeURIComponent(agentId)}`)
}

// Persisted workflow snapshot (phases + agents + status) for one run, from
// workflows/<runId>.json. task_progress events aren't saved to the transcript,
// so on session reload the workflow card rehydrates from this. Rides the
// control-panel "/" lane (snapshots run tens of KB, past the ~8KB EPP cap).
export async function fetchWorkflowState(runId) {
  return sandboxRead(`/agent/workflow-state/${encodeURIComponent(runId)}`)
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

export async function tagSession(sessionId, tags) {
  const normalized = Array.isArray(tags) ? tags : []
  const res = await fetchWithWake(
    `${BASE_URL}/agent/sessions/${encodeURIComponent(sessionId)}/tag`,
    {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
      // Send the first tag too so a rolling deployment with an older runner
      // still preserves the legacy single-tag behavior.
      body: JSON.stringify({ tag: normalized[0] ?? null, tags: normalized }),
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
// sandboxRead: the full archive can be large; ride the control-panel lane.
export async function fetchArchivedSessions() {
  return sandboxRead('/agent/sessions?archived=true')
}
