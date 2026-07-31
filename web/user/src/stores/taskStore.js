import { createStore } from 'zustand/vanilla'
import { makeFacade, registerSliceFactory } from './runtime/registry'
import {
  beginSdkTaskRound as reduceBeginSdkTaskRound,
  createSdkTaskTrackerState,
  recordSdkTaskToolResult as reduceSdkTaskToolResult,
  recordSdkTaskToolUse as reduceSdkTaskToolUse,
} from '../utils/sdkTaskTracker'

// One task slice per session runtime — see runtime/registry.js.
export const createTaskStore = (getSibling) => createStore((set) => ({
  // Background-shell and OpenClaw task tracking. Kept for live BashOutput
  // append paths; no longer rendered as a flat TASKS panel.
  tasks: {},
  taskOrder: [],
  todos: [],
  todoWriteInfo: null,  // { tool_use_id, name, input, status, startTime, endTime, result }
  // Claude Agent SDK TaskCreate / TaskUpdate / TaskGet / TaskList tracker.
  // This is deliberately separate from background shell tasks above:
  // messages never render these tool calls; Composer and Canvas consume the
  // round-aware aggregate below.
  sdkTaskTracker: createSdkTaskTrackerState(),

  // Focus ids for two-way click-to-scroll between the message card timeline
  // and Canvas inspectors. Each setter scrolls the target element via a
  // window event; consumers listen for `priva:scroll-to` events.
  activeTaskId: null,        // Focused inline tool_use / subagent tool card
  activeTodoId: null,        // Focused todo index/id in TodoInspector
  activeSubagentId: null,    // Focused subagent (Agent/Task tool_use id)
  subagentFocusTargetId: null,
  subagentFocusRevision: 0,  // Increments for explicit chat-card -> inspector jumps
  inspectorFocusTarget: null, // { type: 'subagent' | 'todo', id }
  inspectorFocusRevision: 0,

  setActiveTaskId: (id) => set({ activeTaskId: id, activeSubagentId: null }),
  setActiveTodoId: (id) => set({ activeTodoId: id }),
  setActiveSubagentId: (id) => set({ activeTaskId: id, activeTodoId: null, activeSubagentId: id }),
  focusSubagent: (id) => set((s) => ({
    activeTaskId: id,
    activeTodoId: null,
    activeSubagentId: id,
    subagentFocusTargetId: id,
    subagentFocusRevision: s.subagentFocusRevision + 1,
    inspectorFocusTarget: { type: 'subagent', id },
    inspectorFocusRevision: s.inspectorFocusRevision + 1,
  })),
  focusTodoWrite: (id) => set((s) => ({
    activeTaskId: id,
    activeTodoId: null,
    activeSubagentId: null,
    inspectorFocusTarget: { type: 'todo', id },
    inspectorFocusRevision: s.inspectorFocusRevision + 1,
  })),

  addTask: (task) => set((s) => ({
    tasks: { ...s.tasks, [task.tool_use_id]: task },
    taskOrder: s.taskOrder.includes(task.tool_use_id)
      ? s.taskOrder
      : [...s.taskOrder, task.tool_use_id],
  })),

  updateTask: (id, data) => set((s) => ({
    tasks: {
      ...s.tasks,
      [id]: { ...s.tasks[id], ...data },
    },
  })),

  setTodos: (todos) => set({ todos }),

  setTodoWriteInfo: (info) => set((s) => ({
    todoWriteInfo: { ...(s.todoWriteInfo || {}), ...info },
  })),

  beginSdkTaskRound: (round) => set((s) => ({
    sdkTaskTracker: reduceBeginSdkTaskRound(s.sdkTaskTracker, round),
  })),

  recordSdkTaskToolUse: (block, options) => set((s) => ({
    sdkTaskTracker: reduceSdkTaskToolUse(s.sdkTaskTracker, block, options),
  })),

  recordSdkTaskToolResult: (toolUseId, resultBlock, toolUseResult) => set((s) => ({
    sdkTaskTracker: reduceSdkTaskToolResult(
      s.sdkTaskTracker,
      toolUseId,
      resultBlock,
      toolUseResult,
    ),
  })),

  hydrateSdkTaskTracker: (tracker) => set({
    sdkTaskTracker: tracker || createSdkTaskTrackerState(),
  }),

  clearTasks: () => {
    // Workflows are part of the same per-session transient run state — clear
    // them at every session boundary (new chat / session switch / load).
    getSibling('workflow').getState().clear()
    set({
      tasks: {}, taskOrder: [], todos: [], todoWriteInfo: null,
      sdkTaskTracker: createSdkTaskTrackerState(),
      activeTaskId: null, activeTodoId: null, activeSubagentId: null,
      subagentFocusTargetId: null, subagentFocusRevision: 0,
      inspectorFocusTarget: null, inspectorFocusRevision: 0,
    })
  },

  abortRunningTasks: () => set((s) => {
    const tasks = { ...s.tasks }
    let changed = false
    for (const id of Object.keys(tasks)) {
      if (tasks[id].status === 'running') {
        tasks[id] = { ...tasks[id], status: 'error', endTime: Date.now(), interrupted: true }
        changed = true
      }
    }
    return changed ? { tasks } : {}
  }),

  reset: () => set({
    tasks: {}, taskOrder: [], todos: [], todoWriteInfo: null,
    sdkTaskTracker: createSdkTaskTrackerState(),
    activeTaskId: null, activeTodoId: null, activeSubagentId: null,
    subagentFocusTargetId: null, subagentFocusRevision: 0,
    inspectorFocusTarget: null, inspectorFocusRevision: 0,
  }),
}))

registerSliceFactory('tasks', createTaskStore)

const useTaskStore = makeFacade('tasks')

export default useTaskStore
