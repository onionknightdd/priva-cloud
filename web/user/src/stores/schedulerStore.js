import { create } from 'zustand'
import * as api from '../api/scheduler'

// Scheduler page state (design §9.1 master-detail). Selection is a job id or
// the ALL_RUNS sentinel (cross-job run list — the only reachable home of
// deleted jobs' runs). Run pages are keyset-cursored: `runs` accumulates
// forward via [Load more]; changing selection or filter resets the list.

export const ALL_RUNS = '__all_runs__'

const useSchedulerStore = create((set, get) => ({
  jobs: [],
  jobsLoading: false,
  jobsLoaded: false,
  selectedId: null, // job id | ALL_RUNS

  runs: [],
  runsLoading: false,
  runsNextCursor: null,
  runsTotal: null,
  runStatusFilter: null, // null = all
  runJobFilter: null,    // ALL_RUNS view only: narrow to one job id

  drawerOpen: false,
  editingJob: null, // null = create mode; job object = edit mode

  // Derived from one global first-page runs fetch (newest-first): the jobs
  // list's status borders + "● running" second lines without per-job queries.
  liveByJob: {},  // job_id → running run
  lastByJob: {},  // job_id → latest terminal run

  async _loadOverview() {
    try {
      const page = await api.fetchRuns({ limit: 50 })
      const liveByJob = {}
      const lastByJob = {}
      for (const run of page.runs || []) {
        if (!run.job_id) continue
        if (run.status === 'running') {
          if (!liveByJob[run.job_id]) liveByJob[run.job_id] = run
        } else if (!lastByJob[run.job_id]) {
          lastByJob[run.job_id] = run
        }
      }
      set({ liveByJob, lastByJob })
    } catch { /* borders degrade to idle */ }
  },

  async loadJobs({ keepSelection = true } = {}) {
    set({ jobsLoading: !get().jobsLoaded })
    try {
      const data = await api.fetchJobs()
      const jobs = data.jobs || []
      const { selectedId } = get()
      let next = keepSelection ? selectedId : null
      const stillThere = next === ALL_RUNS || jobs.some((j) => j.id === next)
      if (!next || !stillThere) next = jobs.length ? jobs[0].id : ALL_RUNS
      set({ jobs, jobsLoading: false, jobsLoaded: true })
      get()._loadOverview()
      if (next !== selectedId) get().select(next)
    } catch (err) {
      console.warn('[scheduler] loadJobs failed', err)
      set({ jobsLoading: false, jobsLoaded: true })
    }
  },

  select(id) {
    if (id === get().selectedId) return
    set({
      selectedId: id, runs: [], runsNextCursor: null, runsTotal: null,
      runStatusFilter: null, runJobFilter: null,
    })
    get().loadRuns()
  },

  setRunStatusFilter(status) {
    set({ runStatusFilter: status, runs: [], runsNextCursor: null, runsTotal: null })
    get().loadRuns()
  },

  setRunJobFilter(jobId) {
    set({ runJobFilter: jobId, runs: [], runsNextCursor: null, runsTotal: null })
    get().loadRuns()
  },

  async loadRuns({ more = false } = {}) {
    const { selectedId, runStatusFilter, runJobFilter, runsNextCursor, runs } = get()
    if (!selectedId) return
    const jobId = selectedId === ALL_RUNS ? runJobFilter : selectedId
    set({ runsLoading: true })
    try {
      const page = await api.fetchRuns({
        jobId, status: runStatusFilter,
        before: more ? runsNextCursor : null,
      })
      set({
        runs: more ? [...runs, ...(page.runs || [])] : (page.runs || []),
        runsNextCursor: page.next_cursor || null,
        runsTotal: page.total ?? null,
        runsLoading: false,
      })
    } catch (err) {
      console.warn('[scheduler] loadRuns failed', err)
      set({ runsLoading: false })
    }
  },

  // Refresh both panes in place (poll tick / after a mutation). Silent: no
  // skeleton flash — the lists update under the cursor.
  async refresh() {
    try {
      const data = await api.fetchJobs()
      set({ jobs: data.jobs || [], jobsLoaded: true })
    } catch { /* keep the stale list */ }
    get()._loadOverview()
    const { selectedId, runStatusFilter, runJobFilter } = get()
    if (!selectedId) return
    const jobId = selectedId === ALL_RUNS ? runJobFilter : selectedId
    try {
      const page = await api.fetchRuns({ jobId, status: runStatusFilter })
      set({
        runs: page.runs || [],
        runsNextCursor: page.next_cursor || null,
        runsTotal: page.total ?? null,
      })
    } catch { /* keep the stale list */ }
  },

  openCreateDrawer: () => set({ drawerOpen: true, editingJob: null }),
  openEditDrawer: (job) => set({ drawerOpen: true, editingJob: job }),
  closeDrawer: () => set({ drawerOpen: false, editingJob: null }),

  async saveJob(payload) {
    const { editingJob } = get()
    const saved = editingJob
      ? await api.updateJob(editingJob.id, payload)
      : await api.createJob(payload)
    set({ drawerOpen: false, editingJob: null })
    await get().loadJobs()
    if (!editingJob && saved?.id) get().select(saved.id)
    return saved
  },

  async removeJob(jobId) {
    await api.deleteJob(jobId)
    if (get().selectedId === jobId) set({ selectedId: null })
    await get().loadJobs({ keepSelection: false })
  },

  async pauseResume(job) {
    const saved = job.status === 'paused'
      ? await api.resumeJob(job.id)
      : await api.pauseJob(job.id)
    set({ jobs: get().jobs.map((j) => (j.id === job.id ? saved : j)) })
    return saved
  },

  async runNow(jobId) {
    await api.triggerJob(jobId)
    // The fire is detached (claim → wake → dispatch); give StartRun a moment
    // to land, then refresh so the RUNNING row appears.
    setTimeout(() => { get().refresh() }, 1500)
  },

  async stopRun(runId) {
    await api.abortRun(runId)
    setTimeout(() => { get().refresh() }, 1200)
  },
}))

export default useSchedulerStore
