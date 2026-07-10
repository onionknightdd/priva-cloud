import { createStore } from 'zustand/vanilla'
import { makeFacade, registerSliceFactory } from './runtime/registry'

// One file-ops slice per session runtime — see runtime/registry.js.
export const createFileOpsStore = () => createStore((set, get) => ({
  fileOps: [],
  selectedFileOpId: null,
  roundCounter: 0,

  addFileOp: (op) =>
    set((s) => ({
      fileOps: [...s.fileOps, { ...op, roundId: s.roundCounter }],
    })),

  updateFileOp: (id, data) =>
    set((s) => ({
      fileOps: s.fileOps.map((op) => (op.id === id ? { ...op, ...data } : op)),
    })),

  setSelectedFileOpId: (id) => set({ selectedFileOpId: id }),

  incrementRound: () => set((s) => ({ roundCounter: s.roundCounter + 1 })),

  clearFileOps: () => set({ fileOps: [], selectedFileOpId: null, roundCounter: 0 }),

  reset: () => set({ fileOps: [], selectedFileOpId: null, roundCounter: 0 }),
}))

registerSliceFactory('fileOps', createFileOpsStore)

const useFileOpsStore = makeFacade('fileOps')

export default useFileOpsStore
