import useChatStore from '../stores/chatStore'
import useTaskStore from '../stores/taskStore'
import useFileOpsStore from '../stores/fileOpsStore'
import useFileBrowserStore from '../stores/fileBrowserStore'
import useWorkflowStore from '../stores/workflowStore'
import useUiStore from '@shared/stores/uiStore'

function pickChatState(s) {
  return {
    messages: s.messages,
    subagentContent: s.subagentContent,
    sessionId: s.sessionId,
    inputText: s.inputText,
    isStreaming: s.isStreaming,
    isCompacting: s.isCompacting,
    pendingAskUser: s.pendingAskUser,
    streamId: s.streamId,
    pendingPermission: s.pendingPermission,
    permissionQueue: s.permissionQueue,
    permissionMode: s.permissionMode,
    mcpServers: s.mcpServers,
    cwdDraft: s.cwdDraft,
    addDirs: s.addDirs,
    pendingPlanApproval: s.pendingPlanApproval,
    queuedUserMessages: s.queuedUserMessages,
    attachments: s.attachments,
    quotedText: s.quotedText,
    fileReference: s.fileReference,
    fileReferenceTemplate: s.fileReferenceTemplate,
    selectedXlsxReference: s.selectedXlsxReference,
    selectedFileReference: s.selectedFileReference,
    enableFileCheckpointing: s.enableFileCheckpointing,
    checkpoints: s.checkpoints,
    forkParentId: s.forkParentId,
    rewindMarker: s.rewindMarker,
    retryState: s.retryState,
    lastUserPrompt: s.lastUserPrompt,
  }
}

function pickTaskState(s) {
  return {
    tasks: s.tasks,
    taskOrder: s.taskOrder,
    todos: s.todos,
    todoWriteInfo: s.todoWriteInfo,
    activeTaskId: s.activeTaskId,
    activeTodoId: s.activeTodoId,
    activeSubagentId: s.activeSubagentId,
    subagentFocusTargetId: s.subagentFocusTargetId,
    subagentFocusRevision: s.subagentFocusRevision,
    inspectorFocusTarget: s.inspectorFocusTarget,
    inspectorFocusRevision: s.inspectorFocusRevision,
  }
}

function pickUiState(s) {
  return {
    canvasVisible: s.canvasVisible,
    canvasMinimized: s.canvasMinimized,
    activeCanvasTab: s.activeCanvasTab,
    planContent: s.planContent,
    planFilePath: s.planFilePath,
  }
}

export function sessionSnapshot() {
  return {
    chat: pickChatState(useChatStore.getState()),
    task: pickTaskState(useTaskStore.getState()),
    fileOps: {
      fileOps: useFileOpsStore.getState().fileOps,
      selectedFileOpId: useFileOpsStore.getState().selectedFileOpId,
      roundCounter: useFileOpsStore.getState().roundCounter,
    },
    fileBrowser: {
      tabs: useFileBrowserStore.getState().tabs,
      activeTabId: useFileBrowserStore.getState().activeTabId,
    },
    workflow: {
      workflows: useWorkflowStore.getState().workflows,
      workflowOrder: useWorkflowStore.getState().workflowOrder,
      taskIdIndex: useWorkflowStore.getState().taskIdIndex,
      activeWorkflowId: useWorkflowStore.getState().activeWorkflowId,
      activeAgentIndex: useWorkflowStore.getState().activeAgentIndex,
      inspectorFocusTarget: useWorkflowStore.getState().inspectorFocusTarget,
      inspectorFocusRevision: useWorkflowStore.getState().inspectorFocusRevision,
    },
    ui: pickUiState(useUiStore.getState()),
  }
}

export function applySessionSnapshot(data) {
  if (!data) return
  if (data.chat) useChatStore.setState(data.chat)
  if (data.task) useTaskStore.setState(data.task)
  if (data.fileOps) useFileOpsStore.setState(data.fileOps)
  if (data.fileBrowser) useFileBrowserStore.setState(data.fileBrowser)
  if (data.workflow) useWorkflowStore.setState(data.workflow)
  if (data.ui) useUiStore.setState(data.ui)
}

export function subscribeSessionSnapshot(publish) {
  return [
    useChatStore.subscribe(publish),
    useTaskStore.subscribe(publish),
    useFileOpsStore.subscribe(publish),
    useFileBrowserStore.subscribe(publish),
    useWorkflowStore.subscribe(publish),
    useUiStore.subscribe(publish),
  ]
}

