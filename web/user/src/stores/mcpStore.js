import { create } from 'zustand'
import safeStorage from '@shared/utils/safeStorage'
import * as mcpApi from '../api/mcp'

const useMcpStore = create((set, get) => ({
  // Server list
  servers: [],
  serversLoading: true,
  serversLoaded: false,
  searchQuery: '',

  // Selected server
  selectedServer: null, // { level, name, cwd }
  serverDetail: null,
  detailLoading: false,

  // Capabilities (right panel)
  capabilities: null, // { tools, prompts, resources, server_name, server_version }
  capabilitiesLoading: false,
  capabilitiesError: null,
  activeDetailTab: 'tools', // 'tools' | 'prompts' | 'resources'

  // Selected tool (for tool drawer)
  selectedTool: null, // tool object from capabilities

  // List column width (persisted) — content-only left column
  listWidth: safeStorage.getNumber('mcp-list-width', 280, { min: 220, max: 420 }),

  // Meta panel width (persisted)
  metaPanelWidth: safeStorage.getNumber('mcp-meta-width', 320, { min: 240, max: 480 }),

  // Tool drawer width (persisted)
  toolDrawerWidth: safeStorage.getNumber('mcp-tool-drawer-width', 360, { min: 280, max: 600 }),

  // Add-server scope picker (chooses project vs global before the form, like Skills)
  scopePickerOpen: false,

  // Add/edit dialog
  addDialogOpen: false,
  addDialogLevel: 'project',
  addDialogCwd: null, // workdir for a project server being added (null = default workspace / global)
  editMode: false,
  editInitialData: null,
  validating: false,
  validateResult: null,

  // Actions
  fetchServers: async () => {
    set({ serversLoading: true })
    try {
      const data = await mcpApi.listMcpServers()
      set({ servers: data.servers, serversLoading: false, serversLoaded: true })
    } catch {
      set({ serversLoading: false, serversLoaded: true })
    }
  },

  selectServer: async (level, name, cwd = null) => {
    set({
      selectedServer: { level, name, cwd: cwd ?? null },
      detailLoading: true,
      capabilities: null,
      capabilitiesError: null,
      selectedTool: null,
    })
    try {
      const detail = await mcpApi.getMcpServerDetail(level, name, cwd)
      set({ serverDetail: detail, detailLoading: false })
      // Auto-load capabilities
      get().fetchCapabilities(level, name, cwd)
    } catch {
      set({ detailLoading: false })
    }
  },

  fetchCapabilities: async (level, name, cwd = null) => {
    const target = level && name ? { level, name, cwd } : get().selectedServer
    if (!target) return
    set({ capabilitiesLoading: true, capabilitiesError: null })
    try {
      const caps = await mcpApi.getMcpServerCapabilities(target.level, target.name, target.cwd)
      set({ capabilities: caps, capabilitiesLoading: false })
    } catch (e) {
      set({ capabilitiesLoading: false, capabilitiesError: e.message || 'Failed to load capabilities' })
    }
  },

  createServer: async (data) => {
    const result = await mcpApi.createMcpServer(data)
    get().fetchServers()
    return result
  },

  updateServer: async (level, name, cwd, data) => {
    const result = await mcpApi.updateMcpServer(level, name, cwd, data)
    const { selectedServer } = get()
    if (selectedServer?.level === level && selectedServer?.name === name && (selectedServer?.cwd || null) === (cwd || null)) {
      set({ serverDetail: result })
    }
    get().fetchServers()
    return result
  },

  deleteServer: async (level, name, cwd) => {
    await mcpApi.deleteMcpServer(level, name, cwd)
    const { selectedServer } = get()
    if (selectedServer?.level === level && selectedServer?.name === name && (selectedServer?.cwd || null) === (cwd || null)) {
      set({ selectedServer: null, serverDetail: null, capabilities: null })
    }
    get().fetchServers()
  },

  validateServer: async (data) => {
    set({ validating: true, validateResult: null })
    try {
      const result = await mcpApi.validateMcpServer(data)
      set({ validating: false, validateResult: result })
      return result
    } catch (e) {
      const errorResult = { success: false, error: e.message || 'Validation failed' }
      set({ validating: false, validateResult: errorResult })
      return errorResult
    }
  },

  testTool: async (toolName, toolArguments) => {
    const { serverDetail } = get()
    if (!serverDetail) return null
    try {
      const result = await mcpApi.validateMcpTool({
        type: serverDetail.type,
        url: serverDetail.url,
        headers: serverDetail.headers || [],
        timeout: serverDetail.timeout || 30,
        tool_name: toolName,
        tool_arguments: toolArguments || {},
      })
      return result
    } catch (e) {
      return { success: false, error: e.message || 'Tool test failed' }
    }
  },

  // UI state setters
  setSearchQuery: (q) => set({ searchQuery: q }),
  setActiveDetailTab: (t) => set({ activeDetailTab: t }),
  selectTool: (tool) => set({ selectedTool: tool }),
  closeTool: () => set({ selectedTool: null }),
  setListWidth: (w) => {
    safeStorage.setItem('mcp-list-width', String(w))
    set({ listWidth: w })
  },
  setToolDrawerWidth: (w) => {
    safeStorage.setItem('mcp-tool-drawer-width', String(w))
    set({ toolDrawerWidth: w })
  },
  setMetaPanelWidth: (w) => {
    safeStorage.setItem('mcp-meta-width', String(w))
    set({ metaPanelWidth: w })
  },

  openScopePicker: () => set({ scopePickerOpen: true }),
  closeScopePicker: () => set({ scopePickerOpen: false }),

  openAddDialog: (level, cwd = null) => set({
    scopePickerOpen: false,
    addDialogOpen: true,
    addDialogLevel: level || 'project',
    addDialogCwd: cwd ?? null,
    editMode: false,
    editInitialData: null,
    validateResult: null,
    validating: false,
  }),

  openEditDialog: (detail) => set({
    addDialogOpen: true,
    addDialogLevel: detail.level,
    addDialogCwd: detail.cwd ?? null,
    editMode: true,
    editInitialData: detail,
    validateResult: null,
    validating: false,
  }),

  closeAddDialog: () => set({
    addDialogOpen: false,
    validateResult: null,
    validating: false,
    editMode: false,
    editInitialData: null,
  }),

  clearSelection: () => set({
    selectedServer: null,
    serverDetail: null,
    capabilities: null,
    capabilitiesError: null,
    selectedTool: null,
  }),

  reset: () => set({
    servers: [], serversLoading: true, serversLoaded: false, searchQuery: '',
    selectedServer: null, serverDetail: null, detailLoading: false,
    capabilities: null, capabilitiesLoading: false, capabilitiesError: null,
    activeDetailTab: 'tools', selectedTool: null,
    scopePickerOpen: false,
    addDialogOpen: false, validating: false, validateResult: null,
    editMode: false, editInitialData: null,
  }),
}))

export default useMcpStore
