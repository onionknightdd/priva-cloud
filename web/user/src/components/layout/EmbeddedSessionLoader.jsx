import { useEffect, useRef } from 'react'
import { fetchSessionMessages } from '../../api/sessions'
import { hasCanvasInspectorItems, transformSessionMessages } from '../../utils/sessionTransform'
import useChatStore from '../../stores/chatStore'
import useTaskStore from '../../stores/taskStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import useWorkflowStore from '../../stores/workflowStore'
import useUiStore from '@shared/stores/uiStore'
import { getSplitParams } from '../../utils/splitMode'
import { applySessionSnapshot, sessionSnapshot, subscribeSessionSnapshot } from '../../utils/sessionSnapshot'
import { stopActiveStream } from '../../hooks/useSSE'

function hydrateCanvas(parsed, fileOps, fileBrowserTabs, tasks, { embedded = false } = {}) {
  const fileOpsStore = useFileOpsStore.getState()
  for (const op of fileOps) fileOpsStore.addFileOp(op)
  useFileBrowserStore.getState().setTabs(fileBrowserTabs)
  const taskStore = useTaskStore.getState()
  for (const task of tasks) taskStore.addTask(task)
  const canvasTab = fileBrowserTabs.length > 0
    ? 'file-browser'
    : fileOps.length > 0
      ? 'changes'
      : hasCanvasInspectorItems(parsed)
        ? 'tasks'
        : null
  if (canvasTab) {
    const ui = useUiStore.getState()
    ui.setActiveCanvasTab(canvasTab)
    if (embedded) {
      ui.hideCanvas()
      ui.setCanvasMinimized(false)
    } else {
      ui.showCanvas()
    }
  } else {
    useUiStore.getState().hideCanvas()
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
        const { messages, fileOps, fileBrowserTabs, tasks, subagentContent } = transformSessionMessages(data.messages || [])
        useChatStore.getState().loadSession(sessionId, messages, null, subagentContent, data.add_dirs || [])
        hydrateCanvas(messages, fileOps, fileBrowserTabs, tasks, { embedded: true })
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
        stopActiveStream({ broadcast: false })
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
