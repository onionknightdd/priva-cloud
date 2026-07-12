import { create } from 'zustand'
import * as memoryApi from '../api/memory'

// Memory editor state. One unified `selection` drives the right-side editor and
// can point at either a CLAUDE.md scope (user-authored) or a single auto-memory
// file (Claude-authored):
//   { kind: 'claude', scope, cwd }   |   { kind: 'auto', cwd, name }
// `scopes` feeds the CLAUDE.md rows; `autoProjects` feeds the per-project Auto
// memory folders. Both lists key off the same set of workdirs on the backend.
function sameClaude(sel, scope, cwd) {
  return !!sel && sel.kind === 'claude' && sel.scope === scope && (sel.cwd || null) === (cwd || null)
}
function sameAuto(sel, cwd, name) {
  return !!sel && sel.kind === 'auto' && (sel.cwd || null) === (cwd || null) && sel.name === name
}

const useMemoryStore = create((set, get) => ({
  scopes: [],            // CLAUDE.md: [{scope, cwd, path, exists, size}]
  autoProjects: [],      // [{cwd, label, memory_dir, enabled, exists, files:[{name,path,size,is_index}]}]
  expanded: {},          // { [cwd]: bool } — which Auto memory folders are open
  loadingList: false,
  selection: null,       // see header
  content: '',
  savedContent: '',
  contentLoading: false,
  saving: false,
  error: null,

  sameClaude: (scope, cwd) => sameClaude(get().selection, scope, cwd),
  sameAuto: (cwd, name) => sameAuto(get().selection, cwd, name),

  loadList: async () => {
    set({ loadingList: true, error: null })
    try {
      const [mem, auto] = await Promise.all([
        memoryApi.fetchMemoryList(),
        memoryApi.fetchAutoMemoryList().catch(() => ({ projects: [] })),
      ])
      const scopes = Array.isArray(mem.scopes) ? mem.scopes : []
      const autoProjects = Array.isArray(auto.projects) ? auto.projects : []
      set({ scopes, autoProjects })
      if (get().selection == null && scopes.length) {
        const first = scopes.find((s) => s.exists) || scopes[0]
        await get().selectClaude(first.scope, first.cwd)
      }
    } catch (e) {
      set({ error: e.message })
    } finally {
      set({ loadingList: false })
    }
  },

  selectClaude: async (scope, cwd) => {
    set({ selection: { kind: 'claude', scope, cwd: cwd || null }, contentLoading: true, error: null })
    try {
      const data = await memoryApi.fetchMemoryContent(scope, cwd)
      set({ content: data.content || '', savedContent: data.content || '' })
    } catch (e) {
      set({ error: e.message, content: '', savedContent: '' })
    } finally {
      set({ contentLoading: false })
    }
  },

  selectAuto: async (cwd, name) => {
    set({ selection: { kind: 'auto', cwd: cwd || null, name }, contentLoading: true, error: null })
    try {
      const data = await memoryApi.fetchAutoMemoryFile(cwd, name)
      set({ content: data.content || '', savedContent: data.content || '' })
    } catch (e) {
      set({ error: e.message, content: '', savedContent: '' })
    } finally {
      set({ contentLoading: false })
    }
  },

  toggleExpand: (cwd) => set((s) => ({ expanded: { ...s.expanded, [cwd]: !s.expanded[cwd] } })),

  setContent: (content) => set({ content }),

  save: async () => {
    const { selection, content } = get()
    if (!selection) return
    set({ saving: true, error: null })
    try {
      if (selection.kind === 'claude') {
        const data = await memoryApi.updateMemoryContent(content, selection.scope, selection.cwd)
        const saved = data.content ?? content
        set((s) => ({
          savedContent: saved,
          scopes: s.scopes.map((sc) =>
            (sc.scope === selection.scope && (sc.cwd || null) === (selection.cwd || null))
              ? { ...sc, exists: true, size: saved.length } : sc),
        }))
      } else {
        const data = await memoryApi.updateAutoMemoryFile(selection.cwd, selection.name, content)
        const saved = data.content ?? content
        set((s) => ({
          savedContent: saved,
          autoProjects: s.autoProjects.map((p) =>
            p.cwd === selection.cwd
              ? { ...p, files: p.files.map((f) => f.name === selection.name ? { ...f, size: saved.length } : f) }
              : p),
        }))
      }
    } catch (e) {
      set({ error: e.message })
    } finally {
      set({ saving: false })
    }
  },

  deleteAutoFile: async (cwd, name) => {
    set({ error: null })
    try {
      await memoryApi.deleteAutoMemoryFile(cwd, name)
      set((s) => {
        const clearing = sameAuto(s.selection, cwd, name)
        return {
          autoProjects: s.autoProjects.map((p) =>
            p.cwd === cwd ? { ...p, files: p.files.filter((f) => f.name !== name) } : p),
          selection: clearing ? null : s.selection,
          content: clearing ? '' : s.content,
          savedContent: clearing ? '' : s.savedContent,
        }
      })
    } catch (e) {
      set({ error: e.message })
    }
  },

  toggleAuto: async (cwd, enabled) => {
    // Optimistic; revert on failure so the switch never lies about server state.
    set((s) => ({ autoProjects: s.autoProjects.map((p) => p.cwd === cwd ? { ...p, enabled } : p) }))
    try {
      await memoryApi.setAutoMemoryEnabled(cwd, enabled)
    } catch (e) {
      set((s) => ({
        autoProjects: s.autoProjects.map((p) => p.cwd === cwd ? { ...p, enabled: !enabled } : p),
        error: e.message,
      }))
    }
  },

  reset: () => set({
    scopes: [], autoProjects: [], expanded: {}, loadingList: false, selection: null,
    content: '', savedContent: '', contentLoading: false, saving: false, error: null,
  }),
}))

export default useMemoryStore
