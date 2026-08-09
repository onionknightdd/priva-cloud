import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import {
  fetchAgents,
  fetchAgent,
  fetchCatalog,
  createAgent as createAgentApi,
  updateAgent as updateAgentApi,
  deleteAgent as deleteAgentApi,
  streamAgentTest,
} from '../api/subagents'

const DEFAULT_TEST_WIDTH = 480
const STORAGE_KEY = 'subagents-test-width'

const DEFAULT_ALLOWED_TOOLS = [
  'Read', 'Write', 'Edit', 'Bash',
  'TaskCreate', 'TaskGet', 'TaskList', 'TaskUpdate', 'SendMessage',
]

const emptyDraft = () => ({
  __mode: 'create', // 'create' or 'edit'
  __originalName: null,
  scope: 'project', // 'user' | 'project'
  cwd: null, // workdir for project scope (null = default workspace)
  name: '',
  description: '',
  prompt: '',
  tools: [...DEFAULT_ALLOWED_TOOLS],
  disallowedTools: [],
  model: '',
  permissionMode: '',
  maxTurns: null,
  skills: [],
  mcpServers: [],
  memory: '',
  background: false,
})

const detailToDraft = (detail) => ({
  __mode: 'edit',
  __originalName: detail.name,
  scope: detail.scope || 'project',
  cwd: detail.cwd ?? null,
  name: detail.name,
  description: detail.description || '',
  prompt: detail.prompt || '',
  tools: Array.isArray(detail.tools) ? [...detail.tools] : [],
  disallowedTools: Array.isArray(detail.disallowedTools) ? [...detail.disallowedTools] : [],
  model: detail.model || '',
  permissionMode: detail.permissionMode || '',
  maxTurns: detail.maxTurns ?? null,
  skills: Array.isArray(detail.skills) ? [...detail.skills] : [],
  mcpServers: Array.isArray(detail.mcpServers) ? [...detail.mcpServers] : [],
  memory: detail.memory || '',
  background: !!detail.background,
})

const draftToBody = (draft) => {
  const body = {
    name: draft.name,
    description: draft.description,
    prompt: draft.prompt,
    tools: draft.tools,
    disallowedTools: draft.disallowedTools,
    skills: draft.skills,
    mcpServers: draft.mcpServers,
  }
  if (draft.model) body.model = draft.model
  if (draft.permissionMode) body.permissionMode = draft.permissionMode
  if (draft.maxTurns != null && draft.maxTurns !== '') body.maxTurns = Number(draft.maxTurns)
  if (draft.memory) body.memory = draft.memory
  if (typeof draft.background === 'boolean') body.background = draft.background
  return body
}

const useSubagentsStore = create((set, get) => ({
  list: [],
  listLoading: false,
  selectedName: null,
  selectedScope: null,
  selectedCwd: null,
  detail: null,
  detailLoading: false,
  formDraft: null,
  dirty: false,

  // Scope picker (chooses user vs a project workdir before the editor, like MCP)
  scopePickerOpen: false,
  scopePickerTemplate: null, // template to seed after scope is chosen, or null = blank
  catalog: { tools: [], skills: [], mcp_servers: [], reserved_names: [] },
  catalogLoaded: false,

  listWidth: safeStorage.getNumber('subagents-list-width', 260, { min: 220, max: 420 }),
  testWidth: safeStorage.getNumber(STORAGE_KEY, DEFAULT_TEST_WIDTH, { min: 320, max: 720 }),
  testRunning: false,
  testEvents: [],
  testAbort: null,

  setListWidth: (w) => {
    safeStorage.setItem('subagents-list-width', String(w))
    set({ listWidth: w })
  },

  setTestWidth: (w) => {
    safeStorage.setItem(STORAGE_KEY, String(w))
    set({ testWidth: w })
  },

  loadList: async () => {
    set({ listLoading: true })
    try {
      const data = await fetchAgents()
      set({ list: data.agents || [] })
    } catch (e) {
      console.error('Failed to load subagents list:', e)
    } finally {
      set({ listLoading: false })
    }
  },

  loadCatalog: async () => {
    if (get().catalogLoaded) return
    try {
      const data = await fetchCatalog()
      set({ catalog: data, catalogLoaded: true })
    } catch (e) {
      console.error('Failed to load subagents catalog:', e)
    }
  },

  selectAgent: async (scope, cwd, name) => {
    set({ selectedName: name, selectedScope: scope, selectedCwd: cwd ?? null, detailLoading: true, dirty: false })
    try {
      const detail = await fetchAgent(scope, cwd, name)
      set({ detail, formDraft: detailToDraft(detail) })
    } catch (e) {
      console.error('Failed to load agent detail:', e)
      set({ detail: null, formDraft: null })
    } finally {
      set({ detailLoading: false })
    }
  },

  clearSelection: () =>
    set({ selectedName: null, selectedScope: null, selectedCwd: null, detail: null, formDraft: null, dirty: false, testEvents: [] }),

  // --- Scope picker → new-agent flow (choose user/project before the editor) ---
  openScopePicker: (template = null) => set({ scopePickerOpen: true, scopePickerTemplate: template }),
  closeScopePicker: () => set({ scopePickerOpen: false, scopePickerTemplate: null }),
  chooseScope: (scope, cwd = null) => {
    const template = get().scopePickerTemplate
    set({ scopePickerOpen: false, scopePickerTemplate: null })
    if (template) get().startFromTemplate(template, scope, cwd)
    else get().startNewAgent(scope, cwd)
  },

  startNewAgent: (scope = 'project', cwd = null) => {
    set({
      selectedName: null,
      selectedScope: null,
      selectedCwd: null,
      detail: null,
      formDraft: { ...emptyDraft(), scope, cwd: scope === 'project' ? (cwd ?? null) : null },
      dirty: false,
    })
  },

  startFromTemplate: (template, scope = 'project', cwd = null) => {
    set({
      selectedName: null,
      selectedScope: null,
      selectedCwd: null,
      detail: null,
      formDraft: {
        ...emptyDraft(),
        ...template,
        scope,
        cwd: scope === 'project' ? (cwd ?? null) : null,
        __mode: 'create',
        __originalName: null,
      },
      dirty: true,
    })
  },

  setFormField: (key, value) => {
    const draft = get().formDraft
    if (!draft) return
    const next = { ...draft, [key]: value }

    // Allowed / Disallowed are mutually exclusive — picking a tool in one
    // pops it out of the other. Skills are independent (the SDK auto-injects
    // the Skill tool when ``options.skills`` is set on the parent run).
    if (key === 'tools') {
      const allowed = Array.isArray(value) ? value : []
      next.disallowedTools = (next.disallowedTools || []).filter((t) => !allowed.includes(t))
    }

    if (key === 'disallowedTools') {
      const disallowed = Array.isArray(value) ? value : []
      next.tools = (next.tools || []).filter((t) => !disallowed.includes(t))
    }

    set({ formDraft: next, dirty: true })
  },

  discardDraft: () => {
    const { detail } = get()
    if (detail) {
      set({ formDraft: detailToDraft(detail), dirty: false })
    } else {
      set({ formDraft: null, dirty: false })
    }
  },

  saveDraft: async () => {
    const { formDraft } = get()
    if (!formDraft) return null

    const body = draftToBody(formDraft)
    const scope = formDraft.scope || 'project'
    const cwd = scope === 'project' ? (formDraft.cwd ?? null) : null
    let detail = null
    if (formDraft.__mode === 'create') {
      detail = await createAgentApi({ ...body, scope, cwd })
    } else {
      const updateBody = { ...body }
      if (formDraft.name !== formDraft.__originalName) {
        updateBody.new_name = formDraft.name
      }
      detail = await updateAgentApi(scope, cwd, formDraft.__originalName, updateBody)
    }

    set({
      detail,
      selectedName: detail.name,
      selectedScope: detail.scope || scope,
      selectedCwd: detail.cwd ?? null,
      formDraft: detailToDraft(detail),
      dirty: false,
    })
    await get().loadList()
    return detail
  },

  deleteSelected: async () => {
    const { selectedName, selectedScope, selectedCwd } = get()
    if (!selectedName) return
    await deleteAgentApi(selectedScope, selectedCwd, selectedName)
    set({
      selectedName: null,
      selectedScope: null,
      selectedCwd: null,
      detail: null,
      formDraft: null,
      dirty: false,
    })
    await get().loadList()
  },

  runTest: (prompt) => {
    const { selectedName, selectedScope, selectedCwd, testAbort } = get()
    if (!selectedName) return
    if (testAbort) testAbort.abort?.()

    set({ testRunning: true, testEvents: [] })
    const abort = streamAgentTest(
      selectedScope,
      selectedCwd,
      selectedName,
      prompt,
      (event, data) => {
        set((s) => ({ testEvents: [...s.testEvents, { event, data, ts: Date.now() }] }))
      },
      () => {
        set({ testRunning: false, testAbort: null })
      }
    )
    set({ testAbort: abort })
  },

  stopTest: () => {
    const { testAbort } = get()
    if (testAbort) testAbort.abort?.()
    set({ testAbort: null, testRunning: false })
  },

  clearTestEvents: () => set({ testEvents: [] }),

  reset: () =>
    set({
      list: [],
      listLoading: false,
      selectedName: null,
      selectedScope: null,
      selectedCwd: null,
      detail: null,
      formDraft: null,
      dirty: false,
      scopePickerOpen: false,
      scopePickerTemplate: null,
      testRunning: false,
      testEvents: [],
      testAbort: null,
    }),
}))

export default useSubagentsStore
