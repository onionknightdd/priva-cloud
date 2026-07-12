import { sandboxGet, sandboxRead, sandboxPost, sandboxPut, sandboxDelete } from '@shared/api/client'

// User scheduler API (runner /api/sandbox/scheduler/*). Jobs are small and ride
// the plain lane; run pages can exceed the ~8KB EPP cap → sandboxRead.

export const fetchJobs = () => sandboxGet('/scheduler/jobs')

export const createJob = (payload) => sandboxPost('/scheduler/jobs', payload)

export const updateJob = (jobId, payload) => sandboxPut(`/scheduler/jobs/${jobId}`, payload)

export const deleteJob = (jobId) => sandboxDelete(`/scheduler/jobs/${jobId}`)

export const pauseJob = (jobId) => sandboxPost(`/scheduler/jobs/${jobId}/pause`, {})

export const resumeJob = (jobId) => sandboxPost(`/scheduler/jobs/${jobId}/resume`, {})

// Run-now: proxied to the scheduler's internal API — the synthetic fire goes
// through the same exactly-once claim (double-click safe). 202 = accepted.
export const triggerJob = (jobId) => sandboxPost(`/scheduler/jobs/${jobId}/trigger`, {})

// Drawer live preview / custom-cron blur check. Always 200:
// { valid, next_run_time?, error? }.
export const validateTrigger = (trigger, timezone) =>
  sandboxPost('/scheduler/validate-trigger', { trigger, timezone })

// Stop a live run (HTTP twin of the WS abort frame) — records `cancelled`.
export const abortRun = (runId) => sandboxPost(`/agent/scheduled-run/${runId}/abort`, {})

// Keyset-paginated run history; jobId=null → all jobs (the ALL RUNS view).
export const fetchRuns = ({ jobId = null, status = null, limit = 30, before = null } = {}) => {
  const params = new URLSearchParams()
  params.set('limit', String(limit))
  if (jobId) params.set('job_id', jobId)
  if (status) params.set('status', status)
  if (before) params.set('before', before)
  return sandboxRead(`/scheduler/runs?${params}`)
}
