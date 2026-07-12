import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import {
  fetchCommands,
  fetchCommand,
  createCommand as createCommandApi,
  updateCommand as updateCommandApi,
  deleteCommand as deleteCommandApi,
} from '../api/commands'

const emptyDraft = () => ({
  __mode: 'create',
  __originalName: null,
  scope: 'project',
  cwd: null,
  name: '',
  description: '',
  argumentHint: '',
  allowedToolsText: '',
  model: '',
  prompt: '',
})

const detailToDraft = (d) => ({
  __mode: 'edit',
  __originalName: d.name,
  scope: d.scope || 'project',
  cwd: d.cwd ?? null,
  name: d.name,
  description: d.description || '',
  argumentHint: d.argument_hint || '',
  allowedToolsText: (d.allowed_tools || []).join(', '),
  model: d.model || '',
  prompt: d.prompt || '',
})

const draftToBody = (draft) => {
  const body = {
    name: draft.name,
    description: draft.description,
    argument_hint: draft.argumentHint,
    allowed_tools: draft.allowedToolsText.split(',').map((s) => s.trim()).filter(Boolean),
    prompt: draft.prompt,
  }
  if (draft.model) body.model = draft.model
  return body
}

const useCommandsStore = create((set, get) => ({
  list: [],
  listLoading: false,
  selectedName: null,
  selectedScope: null,
  selectedCwd: null,
  formDraft: null,
  dirty: false,
  saving: false,
  error: null,

  scopePickerOpen: false,

  listWidth: safeStorage.getNumber('commands-list-width', 260, { min: 220, max: 420 }),
  setListWidth: (w) => { safeStorage.setItem('commands-list-width', String(w)); set({ listWidth: w }) },

  loadList: async () => {
    set({ listLoading: true })
    try {
      const data = await fetchCommands()
      set({ list: data.commands || [] })
    } catch (e) {
      console.error('Failed to load commands list:', e)
    } finally {
      set({ listLoading: false })
    }
  },

  selectCommand: async (scope, cwd, name) => {
    set({ selectedName: name, selectedScope: scope, selectedCwd: cwd ?? null, dirty: false, error: null })
    try {
      const detail = await fetchCommand(scope, cwd, name)
      set({ formDraft: detailToDraft(detail) })
    } catch (e) {
      console.error('Failed to load command detail:', e)
      set({ formDraft: null, error: e.message })
    }
  },

  clearSelection: () =>
    set({ selectedName: null, selectedScope: null, selectedCwd: null, formDraft: null, dirty: false }),

  // --- New-command scope picker → editor ---
  openScopePicker: () => set({ scopePickerOpen: true }),
  closeScopePicker: () => set({ scopePickerOpen: false }),
  chooseScope: (scope, cwd = null) => {
    set({ scopePickerOpen: false })
    get().startNew(scope, cwd)
  },

  startNew: (scope = 'project', cwd = null) => {
    set({
      selectedName: null, selectedScope: null, selectedCwd: null,
      formDraft: { ...emptyDraft(), scope, cwd: scope === 'project' ? (cwd ?? null) : null },
      dirty: false, error: null,
    })
  },

  setFormField: (key, value) => {
    const draft = get().formDraft
    if (!draft) return
    set({ formDraft: { ...draft, [key]: value }, dirty: true })
  },

  discardDraft: () => {
    const { selectedName, selectedScope, selectedCwd } = get()
    if (selectedName) get().selectCommand(selectedScope, selectedCwd, selectedName)
    else set({ formDraft: null, dirty: false })
  },

  saveDraft: async () => {
    const { formDraft } = get()
    if (!formDraft) return null
    const body = draftToBody(formDraft)
    const scope = formDraft.scope || 'project'
    const cwd = scope === 'project' ? (formDraft.cwd ?? null) : null
    set({ saving: true, error: null })
    try {
      let detail
      if (formDraft.__mode === 'create') {
        detail = await createCommandApi({ ...body, scope, cwd })
      } else {
        const updateBody = { ...body }
        if (formDraft.name !== formDraft.__originalName) updateBody.new_name = formDraft.name
        detail = await updateCommandApi(scope, cwd, formDraft.__originalName, updateBody)
      }
      set({
        selectedName: detail.name,
        selectedScope: detail.scope || scope,
        selectedCwd: detail.cwd ?? null,
        formDraft: detailToDraft(detail),
        dirty: false,
      })
      await get().loadList()
      return detail
    } catch (e) {
      set({ error: e.message })
      return null
    } finally {
      set({ saving: false })
    }
  },

  deleteSelected: async () => {
    const { selectedName, selectedScope, selectedCwd } = get()
    if (!selectedName) return
    await deleteCommandApi(selectedScope, selectedCwd, selectedName)
    set({ selectedName: null, selectedScope: null, selectedCwd: null, formDraft: null, dirty: false })
    await get().loadList()
  },

  reset: () => set({
    list: [], listLoading: false, selectedName: null, selectedScope: null, selectedCwd: null,
    formDraft: null, dirty: false, saving: false, error: null, scopePickerOpen: false,
  }),
}))

export default useCommandsStore
