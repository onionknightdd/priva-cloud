import { create } from 'zustand'
import { getAgentHealth } from '../api/userData'

// First-page bootstrap from the agent-runner's /api/health. The fetch goes
// through getJSON's wake-retry, so a cold sandbox drives the "waking"/"ready"
// toasts; on success we keep the real workspace path the UI renders (e.g. the
// cwd chip), so the first page's content comes from the agent-runner.
const useSandboxStore = create((set) => ({
  workspace: null,
  ready: false,

  fetchHealth: async () => {
    try {
      const data = await getAgentHealth()
      set({ workspace: data?.workspace || null, ready: true })
    } catch {
      // Leave state as-is; the waking/ready toasts are handled in client.js and a
      // genuine failure surfaces its own error toast.
    }
  },
}))

export default useSandboxStore
