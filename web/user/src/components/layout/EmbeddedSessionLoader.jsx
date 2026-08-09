import { useEffect, useRef } from 'react'
import { fetchSessionMessages } from '../../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../../utils/sessionTransform'
import { hasSessionSummaryActivity } from '../../utils/sessionSummary'
import useChatStore from '../../stores/chatStore'
import useTaskStore from '../../stores/taskStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useWorkflowStore from '../../stores/workflowStore'
import useUiStore from '@shared/stores/uiStore'
import { getSplitParams } from '../../utils/splitMode'
import { applySessionSnapshot, sessionSnapshot, subscribeSessionSnapshot } from '../../utils/sessionSnapshot'
import { stopSessionStream } from '../../hooks/useSSE'

function hydrateCanvas(
  parsed,
  fileOps,
  fileBrowserTabs,
  tasks,
  sdkTaskTracker,
  subagentContent,
  { embedded = false } = {},
) {
  const fileOpsStore = useFileOpsStore.getState()
  for (const op of fileOps) fileOpsStore.addFileOp(op)
  useFileBrowserStore.getState().setTabs(fileBrowserTabs)
  const taskStore = useTaskStore.getState()
  for (const task of tasks) taskStore.addTask(task)
  taskStore.hydrateSdkTaskTracker(sdkTaskTracker)
  const ui = useUiStore.getState()
  const preferSummary = hasSessionSummaryActivity({
    fileBrowserTabs,
    fileOps,
    messages: parsed,
    subagentContent,
  })
  const canvasTab = !preferSummary && hasCanvasInspectorItems(parsed, sdkTaskTracker)
    ? 'tasks'
    : null
  if (preferSummary) {
    ui.hideCanvas()
    ui.setCanvasMinimized(false)
    ui.showSessionSummary()
  } else if (canvasTab) {
    ui.hideSessionSummary()
    ui.setActiveCanvasTab(canvasTab)
    if (embedded) {
      ui.hideCanvas()
      ui.setCanvasMinimized(false)
    } else {
      ui.showCanvas()
    }
  } else {
    ui.hideCanvas()
    ui.hideSessionSummary()
  }
}

export default function EmbeddedSessionLoader() {
  const { sessionId, paneId } = getSplitParams()
  const applyingRemoteRef = useRef(false)
  const suppressPublishRef = useRef(true)
  const loadedRef = useRef(null)
  const channelRef = useRef(null)

  useEffect(() => {
    if (!paneId || typeof window === 'undefined') return undefined
    const notifyFocus = () => {
      window.parent?.postMessage({ type: 'priva:split-pane-focus', paneId }, window.location.origin)
      channelRef.current?.postMessage({ type: 'focus', paneId })
    }
    notifyFocus()
    window.addEventListener('pointerdown', notifyFocus, true)
    window.addEventListener('focus', notifyFocus, true)
    return () => {
      window.removeEventListener('pointerdown', notifyFocus, true)
      window.removeEventListener('focus', notifyFocus, true)
    }
  }, [paneId])

  useEffect(() => {
    if (!sessionId || loadedRef.current === sessionId) return undefined
    let cancelled = false
    loadedRef.current = sessionId
    suppressPublishRef.current = true
    useChatStore.getState().clearMessages()
    useTaskStore.getState().clearTasks()
    useFileOpsStore.getState().clearFileOps()
    useFileBrowserStore.getState().clear()
    useUiStore.getState().clearPlanContent()

    fetchSessionMessages(sessionId)
      .then((data) => {
        if (cancelled) return
        const {
          messages,
          fileOps,
          fileBrowserTabs,
          tasks,
          sdkTaskTracker,
          subagentContent,
        } = transformSessionMessages(data.messages || [])
        useChatStore.getState().loadSession(
          sessionId,
          messages,
          null,
          subagentContent,
          data.add_dirs || [],
          data.run_mode,
        )
        hydrateCanvas(
          messages,
          fileOps,
          fileBrowserTabs,
          tasks,
          sdkTaskTracker,
          subagentContent,
          { embedded: true },
        )
        window.setTimeout(() => { suppressPublishRef.current = false }, 250)
      })
      .catch((err) => {
        console.error('Failed to load embedded split session:', err)
        suppressPublishRef.current = false
      })
    return () => { cancelled = true }
  }, [sessionId])

  useEffect(() => {
    if (!sessionId || typeof BroadcastChannel === 'undefined') return undefined
    const channel = new BroadcastChannel(`priva-session:${sessionId}`)
    channelRef.current = channel
    let timer = null
    const publish = () => {
      if (applyingRemoteRef.current || suppressPublishRef.current) return
      if (timer) window.clearTimeout(timer)
      timer = window.setTimeout(() => {
        timer = null
        channel.postMessage({ type: 'state', paneId, state: sessionSnapshot() })
      }, 80)
    }
    const unsubscribers = subscribeSessionSnapshot(publish)
    channel.onmessage = (event) => {
      if (!event.data || event.data.paneId === paneId) return
      if (event.data.type === 'ready') {
        publish()
        return
      }
      if (event.data.type === 'stop-request') {
        // The iframe realm hosts exactly this session; resolve by session id
        // (falls back to the loader's draft-keyed runtime).
        stopSessionStream(sessionId, { broadcast: false })
        publish()
        return
      }
      if (event.data.type === 'focus') return
      if (event.data.type !== 'state' || !event.data.state) return
      applyingRemoteRef.current = true
      applySessionSnapshot(event.data.state)
      window.setTimeout(() => { applyingRemoteRef.current = false }, 0)
    }
    channel.postMessage({ type: 'ready', paneId })
    return () => {
      if (timer) window.clearTimeout(timer)
      unsubscribers.forEach((unsubscribe) => unsubscribe())
      if (channelRef.current === channel) channelRef.current = null
      channel.close()
    }
  }, [paneId, sessionId])

  return null
}
