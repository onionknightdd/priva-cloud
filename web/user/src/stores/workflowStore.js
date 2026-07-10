import { createStore } from 'zustand/vanilla'
import { makeFacade, registerSliceFactory } from './runtime/registry'
import { parseWorkflowMeta } from '../utils/workflowScript'

// Drop keys whose value is `undefined` so a sparse delta never wipes a field
// an earlier event already filled in (start carries promptPreview only; done
// carries resultPreview/tokens — merging must be additive per key).
const strip = (o) => Object.fromEntries(Object.entries(o || {}).filter(([, v]) => v !== undefined))

const isTerminal = (s) => s === 'completed' || s === 'failed' || s === 'stopped'

// Raw task statuses (task_notification.status / task_updated.patch.status) that
// mean the task has ACTUALLY finished. The SDK emits a `task_updated` on every
// background-task state change — including non-terminal `pending` / `running` /
// `paused` — so completion must be gated on this set. Treating an unknown or
// intermediate status as done is what froze the card at DONE ~3s in (with
// 0 agents complete). Mirrors the SDK's TERMINAL_TASK_STATUSES, plus the
// transport-side aborted/cancelled aliases.
export const TERMINAL_RAW_STATUSES = new Set([
  'completed', 'failed', 'stopped', 'killed', 'aborted', 'cancelled',
])

export const isTerminalRawStatus = (raw) =>
  typeof raw === 'string' && TERMINAL_RAW_STATUSES.has(raw)

// Map a (known-terminal) raw status onto the generic taskStore status vocab
// ('success' | 'failed' | 'stopped'). Used by the non-workflow task_updated
// fallback in useSSE so it never defaults an unknown/missing status to success.
export function rawToTaskStatus(raw) {
  if (raw === 'completed') return 'success'
  if (raw === 'failed') return 'failed'
  return 'stopped' // stopped | killed | aborted | cancelled
}

// Agent completion tally for a workflow (across all phases). `done` counts
// agents in state 'done'; `failed` counts error/failed.
function agentTally(wf) {
  let done = 0
  let failed = 0
  for (const i of wf.agentOrder) {
    const st = wf.agents[i]?.state
    if (st === 'done') done += 1
    else if (st === 'error' || st === 'failed') failed += 1
  }
  return { total: wf.agentOrder.length, done, failed }
}

// rawStatus (already known terminal) → workflow.status bucket.
function normalizeStatus(rawStatus) {
  if (rawStatus === 'failed') return 'failed'
  if (rawStatus === 'stopped' || rawStatus === 'killed' || rawStatus === 'aborted' || rawStatus === 'cancelled') return 'stopped'
  return 'completed'
}

const emptyWorkflow = (toolUseId) => ({
  toolUseId,
  taskId: null,
  workflowName: null,
  description: null,
  script: null,
  status: 'pending',
  startedAt: null,
  endedAt: null,
  summary: null,
  lastToolName: null,
  totals: { tokens: 0, toolCalls: 0, durationMs: 0 },
  phases: [],               // [{ index, title, detail }]
  agents: {},               // index -> agent delta-merged
  agentOrder: [],           // insertion order of global agent indices
  completionSource: null,   // 'task_notification' | 'task_updated' | 'stream_end' | 'abort'
})

/**
 * phaseRollup — group a workflow's agents by their (global) phaseIndex and
 * summarize. Non-stored selector: pass the workflow object + a phase index.
 * Agent `index` is global (phase 1 → [1,2], phase 2 → [3,4]); never assume a
 * per-phase reset — always group by `agent.phaseIndex`.
 */
export function phaseRollup(wf, phaseIndex) {
  const agentIndices = wf.agentOrder.filter((i) => wf.agents[i]?.phaseIndex === phaseIndex)
  let done = 0
  let running = 0
  let failed = 0
  for (const i of agentIndices) {
    const st = wf.agents[i]?.state
    if (st === 'done') done += 1
    else if (st === 'error' || st === 'failed') failed += 1
    else running += 1 // start | progress | undefined
  }
  const total = agentIndices.length
  let status = 'pending'
  if (failed > 0) status = 'error'
  else if (total > 0 && done === total) status = 'success'
  else if (total > 0 && (done > 0 || running > 0)) status = 'running'
  return { total, done, running, failed, status, agentIndices }
}

/**
 * workflowAgentTotals — the card/inspector header tally. DONE is cumulative
 * (agents that actually finished). TOTAL is the workflow's FULL agent count:
 * when the script's meta declares a per-phase `agents` count, their sum gives
 * the true total upfront (e.g. 0/6 while only phase 1's agents exist); otherwise
 * it falls back to the agents discovered so far (grows as phases start). Never
 * below the seen count, so an under-declared meta can't hide running agents.
 */
export function workflowAgentTotals(wf) {
  const done = wf.agentOrder.filter((i) => wf.agents[i]?.state === 'done').length
  const seen = wf.agentOrder.length
  const declared = wf.phases.reduce((sum, p) => sum + (p.declaredAgents || 0), 0)
  return { done, total: Math.max(seen, declared) }
}

/**
 * seedFromScript — fill workflowName / description / phases from the script's
 * `meta` literal, but ONLY where still empty. Live data always wins: phases
 * are merged by index so a seeded `detail` survives a later title-only
 * `workflow_phase` delta. The SDK guarantees `meta` is a pure literal, so this
 * lets the card paint name + phase rail (with details) the instant the
 * `Workflow` tool_use arrives — before the first `task_progress`. Returns the
 * same object reference when nothing changed (cheap no-op for re-seeds).
 */
function seedFromScript(wf) {
  if (!wf?.script) return wf
  const meta = parseWorkflowMeta(wf.script)
  if (!meta) return wf

  let changed = false
  const next = { ...wf }

  if (!next.workflowName && meta.name) { next.workflowName = meta.name; changed = true }
  if (!next.description && meta.description) { next.description = meta.description; changed = true }

  if (meta.phases.length > 0) {
    const phases = [...next.phases]
    for (const mp of meta.phases) {
      const at = phases.findIndex((p) => p.index === mp.index)
      if (at < 0) {
        phases.push({ index: mp.index, title: mp.title, detail: mp.detail, declaredAgents: mp.agents })
        changed = true
      } else {
        // Fill only the gaps — never clobber a live title/detail already set.
        // declaredAgents comes only from meta, so set it whenever meta has it.
        const merged = { ...phases[at] }
        let rowChanged = false
        if (!merged.title && mp.title) { merged.title = mp.title; rowChanged = true }
        if (!merged.detail && mp.detail) { merged.detail = mp.detail; rowChanged = true }
        if (merged.declaredAgents == null && mp.agents != null) { merged.declaredAgents = mp.agents; rowChanged = true }
        if (rowChanged) { phases[at] = merged; changed = true }
      }
    }
    if (changed) {
      phases.sort((a, b) => a.index - b.index)
      next.phases = phases
    }
  }

  return changed ? next : wf
}

// One workflow slice per session runtime — see runtime/registry.js.
export const createWorkflowStore = () => createStore((set, get) => ({
  workflows: {},        // toolUseId -> Workflow
  workflowOrder: [],    // toolUseId[]
  taskIdIndex: {},      // taskId -> toolUseId  (task_updated has no tool_use_id)

  // Focus bridge — mirrors taskStore so a chat-card click scrolls the Canvas
  // mirror, and vice versa.
  activeWorkflowId: null,
  activeAgentIndex: null,
  inspectorFocusTarget: null,   // { type:'workflow-agent', workflowId, agentIndex }
  inspectorFocusRevision: 0,

  // toolUseId if the workflow exists, else its taskId mapping, else null.
  resolveId: (toolUseId, taskId) => {
    const { workflows, taskIdIndex } = get()
    if (toolUseId && workflows[toolUseId]) return toolUseId
    if (taskId && taskIdIndex[taskId]) return taskIdIndex[taskId]
    return null
  },

  // Idempotent: create the workflow keyed by tool_use_id, or merge a seed into
  // an existing one without clobbering filled-in fields. Order-independent so
  // tool_use / task_started can arrive in any order.
  ensureWorkflow: (toolUseId, seed = {}) => set((s) => {
    if (!toolUseId) return {}
    const existing = s.workflows[toolUseId]
    const cleanSeed = strip(seed)
    if (existing) {
      // Never downgrade a terminal/running status back to pending via a seed.
      let next = { ...existing, ...cleanSeed }
      if (cleanSeed.status === 'pending' && existing.status !== 'pending') {
        next.status = existing.status
      }
      // tool_use carries the script — paint the meta scaffold immediately.
      next = seedFromScript(next)
      return { workflows: { ...s.workflows, [toolUseId]: next } }
    }
    const created = seedFromScript({ ...emptyWorkflow(toolUseId), ...cleanSeed })
    return {
      workflows: { ...s.workflows, [toolUseId]: created },
      workflowOrder: s.workflowOrder.includes(toolUseId)
        ? s.workflowOrder
        : [...s.workflowOrder, toolUseId],
    }
  }),

  // task_started — record taskId (builds taskIdIndex for task_updated),
  // workflow_name/description/script, and flip to running.
  applyStart: (toolUseId, fields = {}) => {
    get().ensureWorkflow(toolUseId, { status: 'running' })
    set((s) => {
      const base = s.workflows[toolUseId]
      if (!base) return {}
      const clean = strip(fields)
      const wf = seedFromScript({
        ...base,
        ...clean,
        startedAt: base.startedAt ?? (clean.startedAt ?? Date.now()),
        status: isTerminal(base.status) ? base.status : 'running',
      })
      const taskIdIndex = wf.taskId
        ? { ...s.taskIdIndex, [wf.taskId]: toolUseId }
        : s.taskIdIndex
      return { workflows: { ...s.workflows, [toolUseId]: wf }, taskIdIndex }
    })
  },

  // task_progress — keyed accumulation of incremental deltas.
  applyProgress: (toolUseId, { taskId, usage, summary, lastToolName, workflowProgress } = {}) => {
    if (!toolUseId) return
    get().ensureWorkflow(toolUseId, { status: 'running' })
    set((s) => {
      const base = s.workflows[toolUseId]
      if (!base) return {}

      let phases = [...base.phases]
      let phasesChanged = false
      const agents = { ...base.agents }
      const agentOrder = [...base.agentOrder]

      for (const item of workflowProgress ?? []) {
        if (item?.type === 'workflow_phase') {
          const at = phases.findIndex((p) => p.index === item.index)
          if (at >= 0) phases[at] = { ...phases[at], ...strip(item) }
          else phases.push({ ...item })
          phasesChanged = true
        } else if (item?.type === 'workflow_agent') {
          const prev = agents[item.index]
          agents[item.index] = prev ? { ...prev, ...strip(item) } : { ...item }
          if (!agentOrder.includes(item.index)) agentOrder.push(item.index)
          // Backfill a placeholder phase row for an agent whose phaseIndex has
          // no phase yet, so the phase-rail (which iterates wf.phases) can't
          // hide the agent or disagree with the agentOrder-based header counts.
          if (item.phaseIndex != null && !phases.some((p) => p.index === item.phaseIndex)) {
            phases.push({ index: item.phaseIndex })
            phasesChanged = true
          }
        }
      }
      if (phasesChanged) phases.sort((a, b) => a.index - b.index)
      else phases = base.phases

      const toolCallSum = agentOrder.reduce((acc, i) => acc + (agents[i]?.toolCalls || 0), 0)
      const totals = {
        tokens: usage?.total_tokens ?? base.totals.tokens,
        toolCalls: toolCallSum || base.totals.toolCalls,
        durationMs: usage?.duration_ms ?? base.totals.durationMs,
      }

      // Seed taskId from progress too (not just task_started) so a terminal
      // task_updated — which carries only task_id — can still resolve the
      // workflow even if task_started was missed.
      const resolvedTaskId = base.taskId ?? taskId ?? null
      const taskIdIndex = (resolvedTaskId && !s.taskIdIndex[resolvedTaskId])
        ? { ...s.taskIdIndex, [resolvedTaskId]: toolUseId }
        : s.taskIdIndex

      const wf = {
        ...base,
        taskId: resolvedTaskId,
        phases,
        agents,
        agentOrder,
        totals,
        summary: summary ?? base.summary,
        lastToolName: lastToolName ?? base.lastToolName,
        status: isTerminal(base.status) ? base.status : 'running',
      }
      return { workflows: { ...s.workflows, [toolUseId]: wf }, taskIdIndex }
    })
  },

  // Session reload: task_progress isn't persisted, so a historical workflow
  // starts with only the meta scaffold (phases, no agents). Replay the persisted
  // workflows/<runId>.json snapshot — same shape as the live task_progress
  // stream — to repaint phases + agents + terminal status in one shot. No-op for
  // a live workflow already ticking from SSE (never clobbers running agents).
  hydrateFromSnapshot: (toolUseId, snap = {}) => set((s) => {
    const base = s.workflows[toolUseId]
    if (!base) return {}
    if (base.status === 'running' && base.agentOrder.length > 0) return {}

    let phases = [...base.phases]
    const agents = { ...base.agents }
    const agentOrder = [...base.agentOrder]
    for (const item of snap.workflowProgress ?? []) {
      if (item?.type === 'workflow_phase') {
        const at = phases.findIndex((p) => p.index === item.index)
        if (at >= 0) phases[at] = { ...phases[at], ...strip(item) }
        else phases.push({ ...item })
      } else if (item?.type === 'workflow_agent') {
        const prev = agents[item.index]
        agents[item.index] = prev ? { ...prev, ...strip(item) } : { ...item }
        if (!agentOrder.includes(item.index)) agentOrder.push(item.index)
        if (item.phaseIndex != null && !phases.some((p) => p.index === item.phaseIndex)) {
          phases.push({ index: item.phaseIndex })
        }
      }
    }
    phases.sort((a, b) => a.index - b.index)

    const raw = snap.status
    const terminal = isTerminalRawStatus(raw)
    const resolvedTaskId = base.taskId ?? snap.taskId ?? null
    const taskIdIndex = (resolvedTaskId && !s.taskIdIndex[resolvedTaskId])
      ? { ...s.taskIdIndex, [resolvedTaskId]: toolUseId }
      : s.taskIdIndex
    const wf = {
      ...base,
      taskId: resolvedTaskId,
      workflowName: base.workflowName ?? snap.workflowName ?? null,
      summary: base.summary ?? snap.summary ?? null,
      phases,
      agents,
      agentOrder,
      startedAt: base.startedAt ?? snap.startTime ?? null,
      endedAt: base.endedAt ?? (terminal
        ? ((snap.startTime && snap.durationMs) ? snap.startTime + snap.durationMs : Date.now())
        : null),
      totals: {
        tokens: snap.totalTokens ?? base.totals.tokens,
        toolCalls: snap.totalToolCalls ?? base.totals.toolCalls,
        durationMs: snap.durationMs ?? base.totals.durationMs,
      },
      status: terminal ? normalizeStatus(raw) : base.status,
      completionSource: base.completionSource ?? (terminal ? 'reload' : null),
    }
    return { workflows: { ...s.workflows, [toolUseId]: wf }, taskIdIndex }
  }),

  // task_notification / system:task_updated — authoritative completion.
  // Resolve by toolUseId OR taskId (task_updated has no tool_use_id).
  markCompletion: (idOrTaskId, { rawStatus, source, endedAt, summary } = {}) => {
    // Only a genuinely terminal status finishes the workflow. Intermediate
    // task_updated events (pending/running/paused) and unknown statuses must
    // NOT flip the card to done — they leave it running so agents keep ticking.
    if (!isTerminalRawStatus(rawStatus)) return false
    const id = get().resolveId(idOrTaskId, idOrTaskId)
    if (!id) return false
    set((s) => {
      const base = s.workflows[id]
      if (!base) return {}
      const wf = {
        ...base,
        status: normalizeStatus(rawStatus),
        endedAt: endedAt ?? base.endedAt ?? Date.now(),
        summary: summary ?? base.summary,
        completionSource: source ?? base.completionSource,
      }
      return { workflows: { ...s.workflows, [id]: wf } }
    })
    return true
  },

  // Stream end fallback: finalize any still-'running' workflow HONESTLY (never
  // 'pending', so an eventless Workflow block stays neutral). A clean stream
  // close does NOT prove success — only mark 'completed' when every agent
  // actually finished; if an agent errored mark 'failed'; otherwise 'stopped'
  // (the stream that fed us events is gone, so we can't claim a green DONE).
  finalizeRunning: () => set((s) => {
    const workflows = { ...s.workflows }
    let changed = false
    for (const id of Object.keys(workflows)) {
      const wf = workflows[id]
      if (wf.status !== 'running') continue
      const { total, done, failed } = agentTally(wf)
      let next
      if (failed > 0) next = 'failed'
      else if (total > 0 && done === total) next = 'completed'
      else next = 'stopped'
      workflows[id] = {
        ...wf,
        status: next,
        endedAt: wf.endedAt ?? Date.now(),
        completionSource: wf.completionSource ?? 'stream_end',
      }
      changed = true
    }
    return changed ? { workflows } : {}
  }),

  // Stop / transport error: mark any running OR still-pending workflow as
  // stopped. Flipping 'pending' too means a workflow aborted before it started
  // renders as a stopped workflow card (not misclassified as a failed launch).
  abortRunning: () => set((s) => {
    const workflows = { ...s.workflows }
    let changed = false
    for (const id of Object.keys(workflows)) {
      const st = workflows[id].status
      if (st === 'running' || st === 'pending') {
        workflows[id] = {
          ...workflows[id],
          status: 'stopped',
          endedAt: workflows[id].endedAt ?? Date.now(),
          completionSource: workflows[id].completionSource ?? 'abort',
        }
        changed = true
      }
    }
    return changed ? { workflows } : {}
  }),

  // Click an agent (chat card or canvas) → focus + scroll the mirror.
  focusWorkflowAgent: (workflowId, agentIndex) => set((s) => ({
    activeWorkflowId: workflowId,
    activeAgentIndex: agentIndex,
    inspectorFocusTarget: { type: 'workflow-agent', workflowId, agentIndex },
    inspectorFocusRevision: s.inspectorFocusRevision + 1,
  })),

  // Selection without scroll (e.g. hover sync).
  setActiveWorkflowAgent: (workflowId, agentIndex) => set({
    activeWorkflowId: workflowId,
    activeAgentIndex: agentIndex,
  }),

  clear: () => set({
    workflows: {}, workflowOrder: [], taskIdIndex: {},
    activeWorkflowId: null, activeAgentIndex: null,
    inspectorFocusTarget: null, inspectorFocusRevision: 0,
  }),

  reset: () => set({
    workflows: {}, workflowOrder: [], taskIdIndex: {},
    activeWorkflowId: null, activeAgentIndex: null,
    inspectorFocusTarget: null, inspectorFocusRevision: 0,
  }),
}))

registerSliceFactory('workflow', createWorkflowStore)

const useWorkflowStore = makeFacade('workflow')

export default useWorkflowStore
