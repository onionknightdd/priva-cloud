import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import * as skillsApi from '../api/skills'

// Stable identity for a skill across the personal group + every workdir group.
export const skillKey = (s) => (s ? `${s.scope}:${s.cwd || ''}:${s.name}` : '')

let selectionRequestId = 0
let fileRequestId = 0

function matchesSelection(state, scope, cwd, name) {
  const selected = state.selectedSkill
  return !!selected
    && selected.scope === scope
    && selected.cwd === (cwd ?? null)
    && selected.name === name
}

const useSkillsStore = create((set, get) => ({
  // Skill list — grouped: personal (~/.claude/skills) + one group per workdir.
  personal: [],            // SkillSummary[]
  groups: [],              // [{ cwd, skills: SkillSummary[] }]
  skillsLoading: true,
  searchQuery: '',

  // Skill denylist: names excluded from agent runs (flat, by name).
  skillExclude: [],
  configLoaded: false,

  // Selected (and inline-expanded) skill — one at a time.
  selectedSkill: null,     // { scope, cwd, name }
  skillDetail: null,
  detailLoading: false,
  detailError: null,

  // Selected file (shown in the viewer)
  selectedFile: null,      // path string
  fileContent: null,
  fileLoading: false,
  viewerMode: 'skill',     // 'skill' | 'file'

  // Left list column width (persisted)
  listWidth: safeStorage.getNumber('skill-list-width', 300, { min: 220, max: 560 }),

  // View mode for markdown files
  viewMode: 'preview',     // 'preview' | 'source'

  // Upload
  uploading: false,

  fetchSkills: async () => {
    set({ skillsLoading: true })
    try {
      const data = await skillsApi.listSkills()
      set({
        personal: Array.isArray(data?.personal) ? data.personal : [],
        groups: Array.isArray(data?.groups) ? data.groups : [],
        skillsLoading: false,
      })
    } catch {
      set({ skillsLoading: false })
    }
  },

  fetchSkillsConfig: async () => {
    if (get().configLoaded) return
    try {
      const data = await skillsApi.getSkillsConfig()
      set({
        skillExclude: Array.isArray(data?.skill_exclude) ? data.skill_exclude : [],
        configLoaded: true,
      })
    } catch {
      set({ configLoaded: true })
    }
  },

  toggleSkill: async (skillName) => {
    const { skillExclude } = get()
    const prev = Array.isArray(skillExclude) ? skillExclude : []
    const isExcluded = prev.includes(skillName)
    const next = isExcluded
      ? prev.filter((n) => n !== skillName)
      : [...prev, skillName]

    // Optimistic update — reflect on the enabled field across all groups.
    const mark = (list) => list.map((s) => (s.name === skillName ? { ...s, enabled: !next.includes(s.name) } : s))
    set((state) => ({
      skillExclude: next,
      personal: mark(state.personal),
      groups: state.groups.map((g) => ({ ...g, skills: mark(g.skills) })),
    }))

    try {
      await skillsApi.updateSkillsConfig(next)
    } catch {
      set({ skillExclude: prev })
      get().fetchSkills()
    }
  },

  // Toggle the inline tree for a skill. Selecting loads detail + caches
  // SKILL.md for the detail view; files are selected only by explicit tree click.
  selectSkill: async (scope, cwd, name) => {
    const cur = get().selectedSkill
    if (cur && cur.scope === scope && cur.cwd === (cwd ?? null) && cur.name === name) {
      selectionRequestId += 1
      fileRequestId += 1
      set({ selectedSkill: null, skillDetail: null, detailError: null, selectedFile: null, fileContent: null, viewerMode: 'skill' })
      return
    }
    const requestId = ++selectionRequestId
    const skillFileRequestId = ++fileRequestId
    set({
      selectedSkill: { scope, cwd: cwd ?? null, name },
      detailLoading: true,
      detailError: null,
      skillDetail: null,
      selectedFile: null,
      fileContent: null,
      fileLoading: false,
      viewerMode: 'skill',
      viewMode: 'preview',
    })
    try {
      const detail = await skillsApi.getSkillDetail(scope, cwd, name)
      if (requestId !== selectionRequestId || !matchesSelection(get(), scope, cwd, name)) return

      const hasSkillMd = (detail?.tree || []).some((n) => n.type === 'file' && n.name === 'SKILL.md')
      const detailSkillMd = typeof detail?.skill_md_content === 'string'
        ? { path: 'SKILL.md', content: detail.skill_md_content, language: 'markdown', is_binary: false }
        : null
      set({
        skillDetail: detail,
        detailLoading: false,
        detailError: null,
        selectedFile: null,
        fileContent: detailSkillMd,
        fileLoading: hasSkillMd && !detailSkillMd,
        viewerMode: 'skill',
      })

      if (!hasSkillMd || detailSkillMd) return

      try {
        const skillMd = await skillsApi.getSkillFile(scope, cwd, name, 'SKILL.md')
        if (
          requestId !== selectionRequestId
          || skillFileRequestId !== fileRequestId
          || !matchesSelection(get(), scope, cwd, name)
        ) return
        set({
          fileContent: skillMd,
          fileLoading: false,
          viewerMode: 'skill',
        })
      } catch {
        if (
          requestId === selectionRequestId
          && skillFileRequestId === fileRequestId
          && matchesSelection(get(), scope, cwd, name)
        ) {
          set({ fileLoading: false })
        }
      }
    } catch (e) {
      if (requestId === selectionRequestId && matchesSelection(get(), scope, cwd, name)) {
        console.error('[skills] Failed to load skill detail', { scope, cwd: cwd ?? null, name, error: e })
        set({ detailLoading: false, fileLoading: false, detailError: e?.message || 'Failed to load skill detail' })
      }
    }
  },

  selectFile: async (path, viewerMode = 'file') => {
    const { selectedSkill } = get()
    if (!selectedSkill) return
    const requestId = ++fileRequestId
    const { scope, cwd, name } = selectedSkill
    set({ selectedFile: path, fileLoading: true, viewerMode, viewMode: 'preview' })
    try {
      const data = await skillsApi.getSkillFile(scope, cwd, name, path)
      if (requestId !== fileRequestId || !matchesSelection(get(), scope, cwd, name)) return
      set({ fileContent: data, fileLoading: false })
    } catch {
      if (requestId === fileRequestId && matchesSelection(get(), scope, cwd, name)) {
        set({ fileLoading: false })
      }
    }
  },

  cacheSkillFile: async (path) => {
    const { selectedSkill } = get()
    if (!selectedSkill) return
    const requestId = ++fileRequestId
    const { scope, cwd, name } = selectedSkill
    set({ fileLoading: true, viewerMode: 'skill', viewMode: 'preview' })
    try {
      const data = await skillsApi.getSkillFile(scope, cwd, name, path)
      if (requestId !== fileRequestId || !matchesSelection(get(), scope, cwd, name)) return
      set({ fileContent: data, fileLoading: false, viewerMode: 'skill' })
    } catch {
      if (requestId === fileRequestId && matchesSelection(get(), scope, cwd, name)) {
        set({ fileLoading: false })
      }
    }
  },

  uploadSkill: async (scope, cwd, file) => {
    set({ uploading: true })
    try {
      await skillsApi.uploadSkill(scope, cwd, file)
      set({ uploading: false })
      get().fetchSkills()
    } catch (e) {
      set({ uploading: false })
      throw e
    }
  },

  downloadSkill: async (scope, cwd, name) => {
    const blob = await skillsApi.downloadSkill(scope, cwd, name)
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `${name}.tar.gz`
    a.click()
    URL.revokeObjectURL(url)
  },

  deleteSkill: async (scope, cwd, name) => {
    await skillsApi.deleteSkill(scope, cwd, name)
    const { selectedSkill } = get()
    if (selectedSkill?.scope === scope && selectedSkill?.cwd === (cwd ?? null) && selectedSkill?.name === name) {
      set({ selectedSkill: null, skillDetail: null, detailError: null, selectedFile: null, fileContent: null, viewerMode: 'skill' })
    }
    get().fetchSkills()
  },

  setSearchQuery: (query) => set({ searchQuery: query }),
  setViewMode: (mode) => set({ viewMode: mode }),
  setListWidth: (width) => {
    safeStorage.setItem('skill-list-width', String(width))
    set({ listWidth: width })
  },

  clearSelection: () => set({
    selectedSkill: null,
    skillDetail: null,
    detailError: null,
    selectedFile: null,
    fileContent: null,
    viewerMode: 'skill',
  }),

  reset: () => set({
    personal: [], groups: [], skillsLoading: true, searchQuery: '',
    selectedSkill: null, skillDetail: null, detailLoading: false, detailError: null,
    selectedFile: null, fileContent: null, fileLoading: false,
    viewerMode: 'skill',
    viewMode: 'preview', uploading: false,
    skillExclude: [], configLoaded: false,
  }),
}))

export default useSkillsStore
