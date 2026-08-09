import { useRef, useEffect, useState, useCallback } from 'react'
import { Square, Shield, Cable, ChevronRight, X, AlertTriangle, Cpu, CornerDownLeft, FolderPlus, RefreshCw } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import useUiStore from '@shared/stores/uiStore'
import useSettingsStore from '../../stores/settingsStore'
import useMcpStore from '../../stores/mcpStore'
import useTaskStore from '../../stores/taskStore'
import useFileOpsStore from '../../stores/fileOpsStore'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import { useSSE } from '../../hooks/useSSE'
import AskUserQuestionCard from './AskUserQuestionCard'
import PermissionRequestCard from './PermissionRequestCard'
import PlanApprovalCard from './PlanApprovalCard'
import ModelSelector from './ModelSelector'
import PromptComposer from '../shared/PromptComposer'
import ErrorBoundary from '../shared/ErrorBoundary'
import FileReferenceCard from '../shared/FileReferenceCard'
import SelectedXlsxCard from '../shared/SelectedXlsxCard'
import SelectedFileCard from '../shared/SelectedFileCard'
import CwdIndicator from './CwdIndicator'
import CheckpointToggle from './CheckpointToggle'
import DirectoryPicker from '../shared/DirectoryPicker'
import { setSessionAddDirs } from '../../api/sessions'
import { deleteUploadedFile, uploadFile } from '../../api/files'
import { resolveImageRoute } from '../../api/vision'
import QueuedMessagesStack from './QueuedMessagesStack'
import { buildSelectedXlsxXml } from '../../utils/selectedXlsx'
import { buildSelectedFileXml } from '../../utils/selectedFile'
import { popIn, pressTick } from '@shared/motion/waapiMicro'
import usePopoverTransition from '@shared/motion/usePopoverTransition'
import { useListLifecycle, LifecycleItem } from '@shared/motion/ListLifecycle'

const EMPTY_COMPOSER_TEXTAREA_HEIGHT = 50

function findNextVariable(text, fromPos) {
  const regex = /\{[^}]+\}/g
  regex.lastIndex = fromPos
  const match = regex.exec(text)
  if (match) return { start: match.index, end: match.index + match[0].length }
  regex.lastIndex = 0
  const wrapMatch = regex.exec(text)
  if (wrapMatch && wrapMatch.index < fromPos) return { start: wrapMatch.index, end: wrapMatch.index + wrapMatch[0].length }
  return null
}

function resolveVisionModelForSelection(selectedModel, profiles, defaultProfileId, fallbackVisionModel) {
  const fallbackProfileId = defaultProfileId || profiles[0]?.id || null
  let profileId = fallbackProfileId
  if (typeof selectedModel === 'string') {
    const separator = selectedModel.indexOf(':')
    if (separator > 0) {
      const candidate = selectedModel.slice(0, separator)
      if (profiles.some((profile) => profile.id === candidate)) profileId = candidate
    }
  }
  const profile = profiles.find((item) => item.id === profileId)
  if (profile?.vision_model) return profile.vision_model
  return profileId === fallbackProfileId ? (fallbackVisionModel || null) : null
}

function imageAttachmentToFile(attachment) {
  const binary = atob(attachment.base64Data || '')
  const bytes = new Uint8Array(binary.length)
  for (let index = 0; index < binary.length; index += 1) {
    bytes[index] = binary.charCodeAt(index)
  }
  const extension = {
    'image/png': '.png',
    'image/jpeg': '.jpg',
    'image/gif': '.gif',
    'image/webp': '.webp',
  }[attachment.mediaType] || '.png'
  const originalName = attachment.name || 'image'
  const filename = /\.(png|jpe?g|gif|webp)$/i.test(originalName)
    ? originalName.replace(/\.(png|jpe?g|gif|webp)$/i, extension)
    : `${originalName.replace(/\.[^.]+$/, '') || 'image'}${extension}`
  return new File(
    [bytes],
    filename,
    { type: attachment.mediaType || 'image/png' },
  )
}

export default function ChatInput({ cwd, cwdPlacement = 'top', summaryAware = false }) {
  const { t } = useTranslation()
  const inputText = useChatStore((s) => s.inputText)
  const setInputText = useChatStore((s) => s.setInputText)
  const isStreaming = useChatStore((s) => s.isStreaming)
  const clearMessages = useChatStore((s) => s.clearMessages)
  const pendingAskUser = useChatStore((s) => s.pendingAskUser)
  const pendingPermission = useChatStore((s) => s.pendingPermission)
  const pendingPlanApproval = useChatStore((s) => s.pendingPlanApproval)
  const permissionMode = useChatStore((s) => s.permissionMode)
  const setPermissionMode = useChatStore((s) => s.setPermissionMode)
  const sessionId = useChatStore((s) => s.sessionId)
  const messageCount = useChatStore((s) => s.messages.length)
  const cwdDraft = useChatStore((s) => s.cwdDraft)
  const setCwdDraft = useChatStore((s) => s.setCwdDraft)
  const addDirs = useChatStore((s) => s.addDirs)
  const setAddDirs = useChatStore((s) => s.setAddDirs)
  const [cwdPickerOpen, setCwdPickerOpen] = useState(false)
  const [dirsPickerOpen, setDirsPickerOpen] = useState(false)
  const activeNavTab = useUiStore((s) => s.activeNavTab)
  const composerActive = activeNavTab === 'priva'
  const mcpServers = useChatStore((s) => s.mcpServers)
  const setMcpServers = useChatStore((s) => s.setMcpServers)
  const mcpServerList = useMcpStore((s) => s.servers)
  const mcpServersLoading = useMcpStore((s) => s.serversLoading)
  const mcpServersLoaded = useMcpStore((s) => s.serversLoaded)
  const fetchMcpServers = useMcpStore((s) => s.fetchServers)
  const attachments = useChatStore((s) => s.attachments)
  const queuedUserMessages = useChatStore((s) => s.queuedUserMessages)
  const clearTasks = useTaskStore((s) => s.clearTasks)
  const hasRunningTasks = useTaskStore((s) => Object.values(s.tasks).some((t) => t.status === 'running'))
  const clearFileOps = useFileOpsStore((s) => s.clearFileOps)
  const clearFileBrowser = useFileBrowserStore((s) => s.clear)
  const quickActionVariableMode = useChatStore((s) => s.quickActionVariableMode)
  const setQuickActionVariableMode = useChatStore((s) => s.setQuickActionVariableMode)
  const quotedText = useChatStore((s) => s.quotedText)
  const clearQuotedText = useChatStore((s) => s.clearQuotedText)
  const fileReference = useChatStore((s) => s.fileReference)
  const clearFileReference = useChatStore((s) => s.clearFileReference)
  const selectedXlsxReference = useChatStore((s) => s.selectedXlsxReference)
  const clearSelectedXlsxReference = useChatStore((s) => s.clearSelectedXlsxReference)
  const selectedFileReference = useChatStore((s) => s.selectedFileReference)
  const clearSelectedFileReference = useChatStore((s) => s.clearSelectedFileReference)
  const { sendMessage, stopStream, sendAnswer, declineAskUser, respondPermission } = useSSE()
  const visionModel = useSettingsStore((s) => s.visionModel)
  const profiles = useSettingsStore((s) => s.profiles)
  const defaultProfileId = useSettingsStore((s) => s.defaultProfileId)
  const selectedModel = useSettingsStore((s) => s.selectedModel)
  const effectiveVisionModel = resolveVisionModelForSelection(selectedModel, profiles, defaultProfileId, visionModel)
  const textareaRef = useRef(null)
  const isBlocked = !!pendingAskUser || !!pendingPermission || !!pendingPlanApproval

  const [selectedSkill, setSelectedSkill] = useState(null)
  const [showPermissionMenu, setShowPermissionMenu] = useState(false)
  const permMenuRef = useRef(null)
  const { mounted: permissionMenuMounted, popRef: permissionMenuPopRef } = usePopoverTransition({ open: showPermissionMenu, placement: 'top' })
  // Composer-warning callback registered by PromptComposer.
  const composerWarnRef = useRef(null)
  const imageRouteRequestRef = useRef(null)
  const imageSendPendingRef = useRef(false)
  const [imageRouteState, setImageRouteState] = useState({ status: 'idle', route: null })
  const [imageSendPending, setImageSendPending] = useState(false)
  const hasAnyImages = attachments.some((attachment) => attachment.isImage)
  const capabilityRevision = profiles
    .map((profile) => JSON.stringify({
      id: profile.id,
      baseUrl: profile.base_url,
      authToken: profile.auth_token,
      visionModel: profile.vision_model,
      capabilities: profile.model_capabilities || {},
    }))
    .join('|')
  const imageRouteKey = `${selectedModel || '__default__'}\u0000${effectiveVisionModel || ''}\u0000${capabilityRevision}`

  const ensureImageRoute = useCallback(() => {
    const existing = imageRouteRequestRef.current
    if (existing?.key === imageRouteKey && existing.promise) return existing.promise

    setImageRouteState({ status: 'checking', route: null })
    const request = { key: imageRouteKey, promise: null }
    request.promise = resolveImageRoute(selectedModel)
      .then((result) => {
        if (imageRouteRequestRef.current === request) {
          setImageRouteState({ status: 'resolved', ...result })
        }
        return result
      })
      .catch(() => {
        const result = { route: 'probe_failed', reason: 'model_unavailable' }
        if (imageRouteRequestRef.current === request) {
          setImageRouteState({ status: 'resolved', ...result })
        }
        return result
      })
    imageRouteRequestRef.current = request
    return request.promise
  }, [imageRouteKey, selectedModel])

  useEffect(() => {
    if (!hasAnyImages) {
      imageRouteRequestRef.current = null
      setImageRouteState({ status: 'idle', route: null })
      return
    }
    ensureImageRoute()
  }, [hasAnyImages, ensureImageRoute])

  useEffect(() => {
    if (composerActive) return
    setCwdPickerOpen(false)
    setDirsPickerOpen(false)
    setShowPermissionMenu(false)
  }, [composerActive])

  // Bridge chatStore attachments to PromptComposer's functional setter
  const setAttachments = useCallback((updater) => {
    const current = useChatStore.getState().attachments
    const next = typeof updater === 'function' ? updater(current) : updater
    // Revoke blob URLs for removed items
    const removed = current.filter((a) => !next.find((n) => n.id === a.id))
    removed.forEach((a) => { if (a.previewUrl) URL.revokeObjectURL(a.previewUrl) })
    useChatStore.setState({ attachments: next })
  }, [])

  // Fetch MCP servers on mount
  useEffect(() => {
    if (!mcpServersLoaded) fetchMcpServers()
  }, [fetchMcpServers, mcpServersLoaded])

  // Stopping a run is destructive — both the stop button and Escape route
  // through the same red confirm dialog; nothing insta-aborts.
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const confirmStop = useCallback(() => {
    showConfirmDialog({
      danger: true,
      title: t('confirm.stopRunTitle'),
      message: t('confirm.stopRunMessage'),
      confirmLabel: t('confirm.stopRunConfirm'),
      onConfirm: () => stopStream(),
    })
  }, [showConfirmDialog, stopStream, t])

  // Escape opens the stop confirmation while streaming. Pickers/modals that
  // consume Escape call preventDefault, so they always win over this.
  useEffect(() => {
    const handler = (e) => {
      if (e.key !== 'Escape' || !isStreaming) return
      if (e.defaultPrevented) return
      e.preventDefault()
      confirmStop()
    }
    window.addEventListener('keydown', handler)
    return () => window.removeEventListener('keydown', handler)
  }, [isStreaming, confirmStop])

  // Quick action variable mode: focus and select first variable
  useEffect(() => {
    if (!quickActionVariableMode) return
    const el = textareaRef.current
    if (!el) return
    el.focus()
    const match = findNextVariable(inputText, 0)
    if (match) {
      requestAnimationFrame(() => { el.setSelectionRange(match.start, match.end) })
    } else {
      requestAnimationFrame(() => { el.setSelectionRange(inputText.length, inputText.length) })
    }
  }, [quickActionVariableMode]) // eslint-disable-line react-hooks/exhaustive-deps

  // Handle pending auto-send from Skills optimize flow
  const pendingOptimize = useChatStore((s) => s.pendingOptimize)
  const clearPendingOptimize = useChatStore((s) => s.clearPendingOptimize)
  const handleSendRef = useRef(null)
  useEffect(() => {
    if (!pendingOptimize?.autoSend) return
    clearPendingOptimize()
    const raf1 = requestAnimationFrame(() => {
      const raf2 = requestAnimationFrame(() => { handleSendRef.current?.() })
      raf1._inner = raf2
    })
    return () => {
      cancelAnimationFrame(raf1)
      if (raf1._inner) cancelAnimationFrame(raf1._inner)
    }
  }, [pendingOptimize]) // eslint-disable-line react-hooks/exhaustive-deps

  // Seeded composer (e.g. "Create Skill with Agent"): prefill a skill chip +
  // prompt text once this view is mounted. Payload is { skill?, text?, autoSend? }
  // (a bare string is treated as auto-sent text for back-compat). When autoSend is
  // not set, we just prefill so the user can refine the prompt before sending.
  const pendingComposerSend = useChatStore((s) => s.pendingComposerSend)
  const clearPendingComposerSend = useChatStore((s) => s.clearPendingComposerSend)
  useEffect(() => {
    if (!pendingComposerSend) return
    const payload = typeof pendingComposerSend === 'string'
      ? { text: pendingComposerSend, autoSend: true }
      : (pendingComposerSend || {})
    if ('skill' in payload) setSelectedSkill(payload.skill || null)
    if (payload.text != null) setInputText(payload.text)
    clearPendingComposerSend()
    if (!payload.autoSend) return undefined
    const raf1 = requestAnimationFrame(() => {
      const raf2 = requestAnimationFrame(() => { handleSendRef.current?.() })
      raf1._inner = raf2
    })
    return () => {
      cancelAnimationFrame(raf1)
      if (raf1._inner) cancelAnimationFrame(raf1._inner)
    }
  }, [pendingComposerSend]) // eslint-disable-line react-hooks/exhaustive-deps

  // Close permission menu on click outside
  useEffect(() => {
    if (!showPermissionMenu) return
    const handler = (e) => {
      if (permMenuRef.current && !permMenuRef.current.contains(e.target)) {
        setShowPermissionMenu(false)
      }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [showPermissionMenu])

  const hasUploading = imageSendPending || attachments.some((a) => a.status === 'uploading' || a.status === 'processing')
  const doneAttachments = attachments.filter((a) => a.status === 'done')

  const handleSend = async () => {
    if (imageSendPendingRef.current) return
    const initialState = useChatStore.getState()
    const initialAttachments = initialState.attachments
    const initialDone = initialAttachments.filter((attachment) => attachment.status === 'done')
    const initialHasUploading = initialAttachments.some((attachment) => attachment.status === 'uploading' || attachment.status === 'processing')
    const hasFileRef = !!initialState.fileReference
    const hasSelectedXlsxRef = !!initialState.selectedXlsxReference
    const hasSelectedFileRef = !!initialState.selectedFileReference
    const hasContent = !!initialState.inputText.trim() || !!selectedSkill || initialDone.length > 0 || hasFileRef || hasSelectedXlsxRef || hasSelectedFileRef
    if (!hasContent || isBlocked || initialHasUploading) return
    if (!isStreaming && hasRunningTasks) return

    const needsImageRoute = initialDone.some((attachment) => attachment.isImage)
    let uploadedImageIds = []
    if (needsImageRoute) {
      imageSendPendingRef.current = true
      setImageSendPending(true)
    }

    try {
      let routeResult = needsImageRoute
        ? await ensureImageRoute()
        : { route: 'direct' }
      if (needsImageRoute) {
        const latestModel = useSettingsStore.getState().selectedModel
        if (latestModel !== selectedModel) {
          routeResult = await resolveImageRoute(latestModel).catch(() => ({
            route: 'probe_failed',
            reason: 'model_unavailable',
          }))
          if (useSettingsStore.getState().selectedModel !== latestModel) {
            composerWarnRef.current?.(t('chat.modelUnavailable'))
            return
          }
        }
      }

      // Snapshot only after route/probe has completed. If the user edited the
      // composer while waiting, the newer text and attachment set are sent.
      const state = useChatStore.getState()
      const attachmentSnapshot = state.attachments
      const doneSnapshot = attachmentSnapshot.filter((attachment) => attachment.status === 'done')
      const imageSnapshot = doneSnapshot.filter((attachment) => attachment.isImage)
      const fileSnapshot = doneSnapshot.filter((attachment) => !attachment.isImage)
      if (imageSnapshot.length > 0 && ['blocked', 'probe_failed'].includes(routeResult.route)) {
        composerWarnRef.current?.(t('chat.modelUnavailable'))
        return
      }
      if (imageSnapshot.length > 0 && routeResult.route === 'vision_mcp' && isStreaming) {
        composerWarnRef.current?.(t('chat.visionMcpQueueUnavailable'))
        return
      }

      const inputSnapshot = state.inputText
      const text = inputSnapshot.trim()
      const skillSnapshot = selectedSkill
      const fullText = skillSnapshot ? `/${skillSnapshot.name} ${text}`.trim() : text
      if (!fullText && doneSnapshot.length === 0) return

      const currentFileRef = state.fileReference
      const fileRefTemplate = state.fileReferenceTemplate
      let finalText = fullText
      if (currentFileRef) {
        const xmlBlock = `<file-reference path="${currentFileRef.filePath}" startLine="${currentFileRef.startLine}" endLine="${currentFileRef.endLine}" language="${currentFileRef.language || ''}">\n${currentFileRef.selectedText}\n</file-reference>`
        finalText = xmlBlock + '\n' + (fileRefTemplate || fullText)
      }

      const currentSelectedXlsxRef = state.selectedXlsxReference
      if (currentSelectedXlsxRef) {
        const xmlBlock = buildSelectedXlsxXml(currentSelectedXlsxRef)
        finalText = finalText ? `${xmlBlock}\n${finalText}` : xmlBlock
      }

      const currentSelectedFileRef = state.selectedFileReference
      if (currentSelectedFileRef) {
        const xmlBlock = buildSelectedFileXml(currentSelectedFileRef)
        finalText = finalText ? `${xmlBlock}\n${finalText}` : xmlBlock
      }

      const currentQuote = state.quotedText
      if (currentQuote && finalText) {
        finalText = t('quote.template', { content: currentQuote, feedback: finalText })
      }

      const displayImages = imageSnapshot.map((attachment) => ({
        data: attachment.base64Data,
        media_type: attachment.mediaType,
        filename: attachment.name,
      }))
      const regularAttachmentItems = fileSnapshot.map((attachment) => ({
        path: attachment.path,
        name: attachment.originalName || attachment.name,
        attachment_id: attachment.attachmentId || attachment.uuid || undefined,
      }))

      let imageAttachmentItems = []
      let backendImages = displayImages
      const uploadedByLocalId = new Map()
      if (imageSnapshot.length > 0 && routeResult.route === 'vision_mcp') {
        const uploadResults = await Promise.allSettled(imageSnapshot.map(async (attachment) => {
          const result = await uploadFile(imageAttachmentToFile(attachment))
          return { attachment, result }
        }))
        const successes = uploadResults
          .filter((result) => result.status === 'fulfilled')
          .map((result) => result.value)
        uploadedImageIds = successes
          .map(({ result }) => result.attachment_id ?? result.uuid)
          .filter(Boolean)
        if (uploadResults.some((result) => result.status === 'rejected')) {
          await Promise.allSettled(uploadedImageIds.map((id) => deleteUploadedFile(id)))
          uploadedImageIds = []
          composerWarnRef.current?.(t('chat.imageUploadFailed'))
          return
        }
        const latestAttachmentIds = new Set(
          useChatStore.getState().attachments.map((attachment) => attachment.id)
        )
        if (doneSnapshot.some((attachment) => !latestAttachmentIds.has(attachment.id))) {
          await Promise.allSettled(uploadedImageIds.map((id) => deleteUploadedFile(id)))
          uploadedImageIds = []
          composerWarnRef.current?.(t('chat.attachmentsChangedDuringSend'))
          return
        }
        imageAttachmentItems = successes.map(({ attachment, result }) => {
          const attachmentId = result.attachment_id ?? result.uuid
          const item = {
            path: result.path,
            name: result.name ?? result.upload_name ?? attachment.name,
            attachment_id: attachmentId,
            media_type: attachment.mediaType,
            is_image: true,
          }
          uploadedByLocalId.set(attachment.id, item)
          return item
        })
        backendImages = []
      }

      const attachmentItems = [...regularAttachmentItems, ...imageAttachmentItems]
      const attachmentMeta = doneSnapshot.map((attachment) => {
        const uploaded = uploadedByLocalId.get(attachment.id)
        return {
          name: attachment.name,
          size: attachment.size,
          path: uploaded?.path || attachment.path,
          isImage: attachment.isImage || false,
          mediaType: attachment.mediaType,
        }
      })
      const messageToSend = finalText || (displayImages.length > 0
        ? 'Describe the uploaded image(s).'
        : 'Please read the uploaded files.')

      let accepted = false
      if (isStreaming) {
        const queueSender = useChatStore.getState().queueSender
        if (!queueSender?.sendQueue) return
        const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
          ? crypto.randomUUID()
          : `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
        accepted = queueSender.sendQueue({
          id,
          text: messageToSend,
          attachments: attachmentItems.length > 0 ? attachmentItems : undefined,
          images: backendImages.length > 0 ? backendImages : undefined,
        })
        if (accepted) {
          useChatStore.getState().enqueueUserMessage({
            id,
            text: messageToSend,
            attachments: attachmentItems,
            images: displayImages,
            attachmentsMeta: attachmentMeta,
          })
        }
      } else {
        accepted = sendMessage(
          messageToSend,
          permissionMode,
          attachmentItems.length > 0 ? attachmentItems : undefined,
          attachmentMeta.length > 0 ? attachmentMeta : undefined,
          backendImages.length > 0 ? backendImages : undefined,
          displayImages.length > 0 ? displayImages : undefined,
        )
      }
      if (!accepted) {
        if (uploadedImageIds.length > 0) {
          await Promise.allSettled(uploadedImageIds.map((id) => deleteUploadedFile(id)))
          uploadedImageIds = []
        }
        return
      }

      // Commit composer changes only after the transport accepted the message.
      // Conditional clears preserve anything the user typed while MCP images
      // were uploading.
      if (useChatStore.getState().inputText === inputSnapshot) {
        setInputText('')
        setQuickActionVariableMode(false)
        setSelectedSkill((current) => current === skillSnapshot ? null : current)
      }
      const sentIds = new Set(doneSnapshot.map((attachment) => attachment.id))
      setAttachments((current) => current.filter((attachment) => !sentIds.has(attachment.id)))
      const latest = useChatStore.getState()
      if (currentFileRef && latest.fileReference === currentFileRef) {
        latest.clearFileReference()
        latest.clearFileReferenceTemplate()
      }
      if (currentSelectedXlsxRef && latest.selectedXlsxReference === currentSelectedXlsxRef) latest.clearSelectedXlsxReference()
      if (currentSelectedFileRef && latest.selectedFileReference === currentSelectedFileRef) latest.clearSelectedFileReference()
      if (currentQuote && latest.quotedText === currentQuote) latest.clearQuotedText()
      if (attachmentSnapshot.some((attachment) => attachment.status === 'error')) {
        composerWarnRef.current?.(t('chat.failedAttachmentsKept'))
      }
    } catch {
      if (uploadedImageIds.length > 0) {
        await Promise.allSettled(uploadedImageIds.map((id) => deleteUploadedFile(id)))
      }
      composerWarnRef.current?.(t('chat.modelUnavailable'))
    } finally {
      if (needsImageRoute) {
        imageSendPendingRef.current = false
        setImageSendPending(false)
      }
    }
  }
  handleSendRef.current = handleSend

  // Chat-specific keydown: quote backspace, quick-action-variable Tab/Escape, Enter-to-send
  const handleKeyDown = (e) => {
    if (quickActionVariableMode) {
      if (e.key === 'Tab') {
        e.preventDefault()
        const el = textareaRef.current
        if (!el) return
        const curPos = el.selectionEnd
        const match = findNextVariable(inputText, curPos)
        if (match) {
          el.setSelectionRange(match.start, match.end)
        } else {
          setQuickActionVariableMode(false)
          el.setSelectionRange(inputText.length, inputText.length)
        }
        return
      }
      if (e.key === 'Escape') {
        e.preventDefault()
        setQuickActionVariableMode(false)
        setInputText('')
        return
      }
    }
    // Backspace on empty: clear fileReference or quote first (before PromptComposer handles skill/attachments)
    if (e.key === 'Backspace' && !inputText) {
      const selectedFile = useChatStore.getState().selectedFileReference
      if (selectedFile) {
        e.preventDefault()
        clearSelectedFileReference()
        return
      }
      const selectedXlsx = useChatStore.getState().selectedXlsxReference
      if (selectedXlsx) {
        e.preventDefault()
        clearSelectedXlsxReference()
        return
      }
      const fr = useChatStore.getState().fileReference
      if (fr) {
        e.preventDefault()
        clearFileReference()
        return
      }
      const q = useChatStore.getState().quotedText
      if (q) {
        e.preventDefault()
        clearQuotedText()
        return
      }
    }
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleDecline = () => {
    declineAskUser()
    setTimeout(() => textareaRef.current?.focus(), 50)
  }

  const hasContentToSend = !!inputText.trim() || !!selectedSkill || doneAttachments.length > 0 || !!fileReference || !!selectedXlsxReference || !!selectedFileReference
  const canSendIdle = hasContentToSend && !isStreaming && !isBlocked && !hasUploading && !hasRunningTasks
  const canQueue = hasContentToSend && isStreaming && !isBlocked && !hasUploading
  const canSend = canSendIdle || canQueue

  // M15 micro-feedback (WAAPI, fire-and-forget): pop the send button when it
  // appears or swaps send↔queue, pop the stop button when it appears. Prev-value
  // guards keep the initial mount silent — only live transitions animate.
  const sendBtnRef = useRef(null)
  const stopBtnRef = useRef(null)
  const sendBtnMode = canSend ? (canQueue ? 'queue' : 'send') : null
  const prevSendBtnModeRef = useRef(sendBtnMode)
  useEffect(() => {
    if (sendBtnMode && prevSendBtnModeRef.current !== sendBtnMode) popIn(sendBtnRef.current)
    prevSendBtnModeRef.current = sendBtnMode
  }, [sendBtnMode])
  const prevStreamingRef = useRef(isStreaming)
  useEffect(() => {
    if (isStreaming && !prevStreamingRef.current) popIn(stopBtnRef.current)
    prevStreamingRef.current = isStreaming
  }, [isStreaming])

  const handlePlanApproval = (option, feedbackText) => {
    const { requestId, planContent, planFilePath } = pendingPlanApproval
    respondPermission(requestId, 'allow')
    useChatStore.getState().clearPendingPlanApproval()

    if (option === 'feedback') {
      const feedback = `Plan feedback:\n${feedbackText}`
      sendMessage(feedback, permissionMode)
      return
    }

    const template = [
      'User has approved your plan. You can now start coding. Start with updating your todo list if applicable',
      '',
      planFilePath ? `Your plan has been saved to: ${planFilePath}` : '',
      planFilePath ? 'You can refer back to it if needed during implementation.' : '',
      '---',
      planContent || '',
      '---',
    ].filter((line) => line !== '' || true).join('\n')

    if (option === 'newSessionAutoEdits') {
      clearMessages()
      clearTasks()
      clearFileOps()
      clearFileBrowser()
      setPermissionMode('acceptEdits')
      setTimeout(() => { sendMessage(template, 'acceptEdits') }, 50)
    } else if (option === 'sameSessionAutoEdits') {
      setPermissionMode('acceptEdits')
      sendMessage(template, 'acceptEdits')
    } else if (option === 'sameSessionManual') {
      setPermissionMode('default')
      sendMessage(template, 'default')
    }
  }

  const PERMISSION_MODES = [
    { value: 'bypassPermissions', label: t('permission.bypass'), desc: t('permission.bypassDesc'), color: 'var(--yellow)' },
    { value: 'default', label: t('permission.default'), desc: t('permission.defaultDesc'), color: 'var(--green)' },
    { value: 'acceptEdits', label: t('permission.acceptEdits'), desc: t('permission.acceptEditsDesc'), color: 'var(--purple)' },
    { value: 'plan', label: t('permission.plan'), desc: t('permission.planDesc'), color: 'var(--cyan)' },
  ]

  const contextItems = []
  if (selectedFileReference) contextItems.push({ id: 'selected-file', type: 'selected-file', data: selectedFileReference })
  if (selectedXlsxReference) contextItems.push({ id: 'selected-xlsx', type: 'selected-xlsx', data: selectedXlsxReference })
  if (fileReference) contextItems.push({ id: 'file-reference', type: 'file-reference', data: fileReference })
  if (quotedText) contextItems.push({ id: 'quote', type: 'quote', text: quotedText })
  const [lifecycleContextItems, removeExitedContextItem] = useListLifecycle(contextItems, (item) => item.id)

  const renderContextItem = (item) => {
    if (item.type === 'selected-file') {
      return (
        <div className="px-3 pt-3 pb-0">
          <SelectedFileCard
            kind={item.data.kind}
            filePath={item.data.filePath}
            fileName={item.data.fileName}
            locator={item.data.locator}
            content={item.data.content}
            onDismiss={clearSelectedFileReference}
          />
        </div>
      )
    }
    if (item.type === 'selected-xlsx') {
      return (
        <div className="px-3 pt-3 pb-0">
          <SelectedXlsxCard
            filePath={item.data.filePath}
            sheetName={item.data.sheetName}
            range={item.data.range}
            contentTsv={item.data.contentTsv}
            onDismiss={clearSelectedXlsxReference}
          />
        </div>
      )
    }
    if (item.type === 'file-reference') {
      return (
        <div className="px-3 pt-3 pb-0">
          <FileReferenceCard
            filePath={item.data.filePath}
            startLine={item.data.startLine}
            endLine={item.data.endLine}
            selectedText={item.data.selectedText}
            language={item.data.language}
            onDismiss={clearFileReference}
          />
        </div>
      )
    }
    return (
      <div className="flex items-center gap-2 px-3 pt-3 pb-0">
        <div className="flex items-center gap-2 px-2 py-1" style={{
          background: 'var(--bg-surface)',
          borderLeft: '2px solid var(--blue)',
          borderRadius: 2,
          maxWidth: '100%',
          minWidth: 0,
        }}>
          <CornerDownLeft size={12} strokeWidth={1.5} style={{ color: 'var(--blue)', flexShrink: 0 }} />
          <span className="uppercase" style={{
            color: 'var(--text-dim)', fontSize: 11, letterSpacing: '0.06em', fontWeight: 600, flexShrink: 0,
          }}>
            {t('quote.quoted')}
          </span>
          <span className="truncate" style={{
            color: 'var(--text-secondary)', fontSize: 12, fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          }}>
            &ldquo;{item.text.length > 60 ? item.text.slice(0, 60) + '...' : item.text}&rdquo;
          </span>
          <button
            className="flex items-center justify-center flex-shrink-0"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 0, marginLeft: 2, transition: 'color 150ms ease' }}
            onClick={clearQuotedText}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={14} strokeWidth={1.5} />
          </button>
        </div>
      </div>
    )
  }

  const beforeTextareaContent = lifecycleContextItems.length > 0 ? (
    <>
      {lifecycleContextItems.map(({ key, item, present, exitIndex }) => (
        <LifecycleItem
          key={key}
          present={present}
          onExited={() => removeExitedContextItem(key)}
          duration={220}
          exitDuration={200}
          enterCollapse
          exitDelay={Math.min(exitIndex * 20, 80)}
          style={{ pointerEvents: present ? 'auto' : 'none' }}
        >
          {renderContextItem(item)}
        </LifecycleItem>
      ))}
    </>
  ) : null

  // Vision model hint banners rendered after image thumbnails
  const hasImages = attachments.some((a) => a.isImage)
  const visionHintItems = hasImages ? [{ id: 'vision-hint', ...imageRouteState }] : []
  const [lifecycleVisionHints, removeExitedVisionHint] = useListLifecycle(visionHintItems, (item) => item.id)
  const visionHints = lifecycleVisionHints.length > 0 ? (
    <>
      {lifecycleVisionHints.map(({ key, item, present }) => {
        const unavailable = ['blocked', 'probe_failed'].includes(item.route)
        const checking = item.status !== 'resolved'
        const hintText = checking
          ? t('chat.imageCapabilityChecking')
          : item.route === 'direct'
            ? t('chat.imageRouteDirect', { model: item.model_id })
            : item.route === 'vision_mcp'
              ? t('chat.imageRouteVisionMcp', { model: item.vision_model })
              : t('chat.modelUnavailable')
        return (
          <LifecycleItem
            key={key}
            present={present}
            onExited={() => removeExitedVisionHint(key)}
            duration={220}
            exitDuration={180}
            enterCollapse
            style={{ pointerEvents: present ? 'auto' : 'none' }}
          >
            <div className="flex items-center gap-2 px-3 py-1 mx-3 mt-1"
              style={{ background: 'var(--bg-elevated)', borderLeft: `2px solid ${unavailable ? 'var(--yellow)' : 'var(--cyan)'}`, borderRadius: 2, fontSize: 11, color: 'var(--text-dim)', userSelect: 'none' }}>
              {unavailable
                ? <AlertTriangle size={12} strokeWidth={1.5} style={{ color: 'var(--yellow)', flexShrink: 0 }} />
                : <Cpu size={12} strokeWidth={1.5} style={{ color: 'var(--cyan)', flexShrink: 0 }} />}
              <span className="flex-1 min-w-0">{hintText}</span>
              {item.route === 'probe_failed' && (
                <button type="button" className="flex items-center gap-1" onClick={() => { imageRouteRequestRef.current = null; ensureImageRoute() }} style={{ padding: 0, background: 'transparent', border: 'none', color: 'var(--blue)', cursor: 'pointer', fontSize: 11 }}>
                  <RefreshCw size={12} strokeWidth={1.5} /> {t('chat.retryImageProbe')}
                </button>
              )}
            </div>
          </LifecycleItem>
        )
      })}
    </>
  ) : null

  // MCP submenu inside the + dropdown
  const mcpMenuContent = (
    <McpSubMenu
      mcpServers={mcpServers}
      setMcpServers={setMcpServers}
      serverList={mcpServerList}
      loading={mcpServersLoading}
      t={t}
    />
  )

  // Permission mode button (toolbar left, after + button)
  const toolbarLeftContent = (
    <div className="flex items-center" style={{ gap: 3 }}>
      <div className="relative" ref={permMenuRef}>
      <button
        className="flex items-center gap-1 px-2"
        style={{
          height: 28,
          background: 'transparent', border: 'none', borderRadius: '4px',
          cursor: isStreaming ? 'not-allowed' : 'pointer',
          color: PERMISSION_MODES.find((m) => m.value === permissionMode)?.color || 'var(--text-dim)',
          fontSize: 12, fontWeight: 600,
          opacity: isStreaming ? 0.5 : 1,
          transition: 'color 150ms ease, background 150ms ease',
        }}
        onMouseEnter={(e) => { if (!isStreaming) e.currentTarget.style.background = 'var(--bg-elevated)' }}
        onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        onPointerDown={(e) => { if (!isStreaming) pressTick(e.currentTarget, { to: 0.96 }) }}
        onClick={(e) => {
          e.stopPropagation()
          if (!isStreaming) setShowPermissionMenu(!showPermissionMenu)
        }}
      >
        <Shield size={12} strokeWidth={1.5} />
        <span>{PERMISSION_MODES.find((m) => m.value === permissionMode)?.label}</span>
      </button>

      {permissionMenuMounted && (
        <div
          ref={permissionMenuPopRef}
          className="absolute"
          style={{
            bottom: '100%', left: 0, marginBottom: 4,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, minWidth: 200, zIndex: 20, padding: '4px 0',
            pointerEvents: showPermissionMenu ? 'auto' : 'none',
          }}
        >
          {PERMISSION_MODES.map((mode) => {
            const isActive = permissionMode === mode.value
            return (
              <button
                key={mode.value}
                className="flex flex-col gap-0 px-3 py-2 w-full"
                style={{
                  background: isActive ? 'var(--bg-surface)' : 'transparent',
                  border: 'none',
                  borderLeft: isActive ? `2px solid ${mode.color}` : '2px solid transparent',
                  cursor: 'pointer', textAlign: 'left', transition: 'background 150ms ease',
                }}
                onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-surface)' }}
                onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = isActive ? 'var(--bg-surface)' : 'transparent' }}
                onClick={() => {
                  setPermissionMode(mode.value)
                  setShowPermissionMenu(false)
                }}
              >
                <span style={{ color: isActive ? mode.color : 'var(--text-secondary)', fontSize: 13, fontWeight: isActive ? 600 : 400 }}>
                  {mode.label}
                </span>
                <span style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                  {mode.desc}
                </span>
              </button>
            )
          })}
        </div>
      )}
      </div>
      <CheckpointToggle />
    </div>
  )

  // Send/stop buttons stay inside the input toolbar. The model selector lives
  // in the composer controls row below, aligned with the workspace controls.
  const toolbarRightContent = (
    <>
      {isStreaming && (
        <button
          ref={stopBtnRef}
          className="flex items-center justify-center"
          style={{
            width: 28, height: 28,
            background: 'transparent', border: 'none', borderRadius: '4px',
            cursor: 'pointer', color: 'var(--text-primary)',
            transition: 'background 150ms ease, color 150ms ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          onPointerDown={(e) => { pressTick(e.currentTarget) }}
          onClick={confirmStop}
          title={t('chat.stopEscape')}
        >
          <Square size={13} strokeWidth={2.25} fill="currentColor" />
        </button>
      )}
      {canSend && (
        <button
          ref={sendBtnRef}
          className="flex items-center justify-center"
          style={{
            width: 28, height: 28,
            background: 'transparent', border: 'none', borderRadius: '4px',
            cursor: 'pointer', color: 'var(--text-primary)',
            transition: 'background 150ms ease, color 150ms ease',
          }}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
          onPointerDown={(e) => { pressTick(e.currentTarget) }}
          onClick={handleSend}
          title={canQueue ? t('chat.queueForBoundary') : t('chat.send')}
        >
          <CornerDownLeft size={17} strokeWidth={2.25} />
        </button>
      )}
      {/* Running tasks block fresh sends — keep the button visible but inert
          so the user understands why nothing happens. */}
      {!canSend && hasContentToSend && !isStreaming && hasRunningTasks && (
        <button
          className="flex items-center justify-center"
          disabled
          style={{
            width: 28, height: 28,
            background: 'var(--bg-elevated)', border: 'none', borderRadius: '4px',
            cursor: 'not-allowed', color: 'var(--text-dim)',
          }}
          title={t('chat.waitForTasks')}
        >
          <CornerDownLeft size={17} strokeWidth={2.25} />
        </button>
      )}
    </>
  )

  // cwd is editable only before a conversation starts; once a session exists or
  // any message is present it locks (no picker, no lock glyph — disabled chip).
  const cwdLocked = !!sessionId || messageCount > 0
  const displayCwd = cwdLocked ? cwd : (cwdDraft || cwd || '')

  const applyAddDirs = (dirs) => {
    setAddDirs(dirs)
    // Persist immediately when a session exists so a resume recovers the set.
    if (sessionId) setSessionAddDirs(sessionId, dirs).catch(() => {})
  }

  // cwd chip (current place) + add_dirs chip immediately to its right.
  const cwdRow = (
    <div className="flex items-center gap-2 min-w-0">
      <div className="min-w-0" style={{ flex: '0 1 auto', minWidth: 0 }}>
        <CwdIndicator
          cwd={displayCwd}
          disabled={cwdLocked}
          onClick={cwdLocked ? undefined : () => setCwdPickerOpen(true)}
        />
      </div>
      <button
        type="button"
        onClick={() => setDirsPickerOpen(true)}
        className="inline-flex items-center gap-1 flex-shrink-0"
        title={addDirs.length ? addDirs.join('\n') : t('chat.addDirsHint')}
        style={{
          height: 28,
          padding: '0 9px',
          boxSizing: 'border-box',
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          cursor: 'pointer',
          color: addDirs.length ? 'var(--text-secondary)' : 'var(--text-dim)',
          fontSize: 12,
          transition: 'border-color 150ms ease, color 150ms ease',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--border-strong)'; e.currentTarget.style.color = 'var(--text-primary)' }}
        onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)'; e.currentTarget.style.color = addDirs.length ? 'var(--text-secondary)' : 'var(--text-dim)' }}
      >
        <FolderPlus size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        <span>{addDirs.length ? t('chat.addDirsCount', { count: addDirs.length }) : t('chat.addDirs')}</span>
      </button>
    </div>
  )

  const composerControlsRow = (
    <div className="flex items-end justify-between gap-2 min-w-0">
      {cwdRow}
      <div className="flex-shrink-0 min-w-0">
        <ModelSelector />
      </div>
    </div>
  )

  const directoryPickers = (
    <>
      <DirectoryPicker
        open={cwdPickerOpen}
        multiple={false}
        allowCreate
        title={t('picker.cwdTitle')}
        initialPath={cwd || '/'}
        initialSelected={displayCwd || null}
        onConfirm={(path) => { setCwdDraft(path); setCwdPickerOpen(false) }}
        onCancel={() => setCwdPickerOpen(false)}
      />
      <DirectoryPicker
        open={dirsPickerOpen}
        multiple
        title={t('picker.addDirsTitle')}
        initialPath={cwd || '/'}
        initialSelected={addDirs}
        onConfirm={(dirs) => { applyAddDirs(dirs); setDirsPickerOpen(false) }}
        onCancel={() => setDirsPickerOpen(false)}
      />
    </>
  )

  const pendingComposerItems = pendingPlanApproval
    ? [{ id: `plan-${pendingPlanApproval.requestId || 'current'}`, type: 'plan', data: pendingPlanApproval }]
    : pendingPermission
      ? [{ id: `permission-${pendingPermission.requestId || pendingPermission.request_id || 'current'}`, type: 'permission', data: pendingPermission }]
      : pendingAskUser
        ? [{ id: `ask-${pendingAskUser.toolUseId || pendingAskUser.tool_use_id || 'current'}`, type: 'ask', data: pendingAskUser }]
        : []
  const [lifecyclePendingComposerItems, removeExitedPendingComposerItem] = useListLifecycle(pendingComposerItems, (item) => item.id)

  const renderPendingComposerItem = (item) => {
    if (item.type === 'plan') {
      return (
        <ErrorBoundary compact resetKey={item.data.requestId}>
          <PlanApprovalCard approval={item.data} onApprove={handlePlanApproval} />
        </ErrorBoundary>
      )
    }
    if (item.type === 'permission') {
      return (
        <ErrorBoundary compact resetKey={item.data.requestId || item.data.request_id}>
          <PermissionRequestCard block={item.data} onRespond={respondPermission} />
        </ErrorBoundary>
      )
    }
    return (
      <ErrorBoundary compact resetKey={item.data.toolUseId || item.data.tool_use_id}>
        <AskUserQuestionCard block={item.data} onAnswer={sendAnswer} onSkip={handleDecline} />
      </ErrorBoundary>
    )
  }

  return (
    <div
      className="flex-shrink-0"
      style={{ background: 'var(--bg-base)', paddingTop: 14, paddingBottom: 10 }}
    >
      {directoryPickers}
      <div style={summaryAware ? {
        width: 'auto',
        maxWidth: 'none',
        marginLeft: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
        marginRight: 'var(--session-summary-track-inline-margin, max(10%, calc(50% - 450px)))',
        transition: 'margin-left var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1)), margin-right var(--session-summary-motion-duration, 200ms) var(--session-summary-motion-ease, cubic-bezier(0.16, 1, 0.3, 1))',
      } : { maxWidth: 900, width: '80%', margin: '0 auto' }}>
        {cwdPlacement === 'top' && (
          <div className="min-w-0" style={{ marginBottom: 8 }}>
            {cwdRow}
          </div>
        )}

        {/* No length gate here: the stack retains exiting rows and unmounts
            itself once the last exit animation finishes. */}
        <QueuedMessagesStack
          entries={queuedUserMessages}
          style={{ marginBottom: 8 }}
        />

        {lifecyclePendingComposerItems.length > 0 ? (
          lifecyclePendingComposerItems.map(({ key, item, present }) => (
            <LifecycleItem
              key={key}
              present={present}
              onExited={() => removeExitedPendingComposerItem(key)}
              duration={220}
              exitDuration={220}
              rise={0}
              style={{ pointerEvents: present ? 'auto' : 'none' }}
            >
              {renderPendingComposerItem(item)}
            </LifecycleItem>
          ))
        ) : (
          <PromptComposer
            value={inputText}
            onChange={setInputText}
            attachments={attachments}
            onAttachmentsChange={setAttachments}
            skill={selectedSkill}
            onSkillChange={setSelectedSkill}
            placeholder={t('chat.placeholder')}
            minHeight={EMPTY_COMPOSER_TEXTAREA_HEIGHT}
            onKeyDown={handleKeyDown}
            textareaRef={textareaRef}
            toolbarLeft={toolbarLeftContent}
            toolbarRight={toolbarRightContent}
            plusMenuExtra={mcpMenuContent}
            beforeTextarea={beforeTextareaContent}
            afterImages={visionHints}
            currentDirectory={displayCwd}
            active={composerActive}
            onRegisterWarn={(fn) => { composerWarnRef.current = fn }}
          />
        )}

        {cwdPlacement === 'below' && (
          <div className="min-w-0" style={{ marginTop: 8 }}>
            {composerControlsRow}
          </div>
        )}
      </div>
      <div style={{ textAlign: 'center', color: 'var(--text-dim)', fontSize: 11, fontWeight: 400, paddingTop: 4 }}>
        {t('chat.disclaimer')}
      </div>
    </div>
  )
}

function McpSubMenu({ mcpServers, setMcpServers, serverList, loading, t }) {
  const [showSub, setShowSub] = useState(false)
  const hoverTimeout = useRef(null)
  const { mounted: subMounted, popRef: subPopRef } = usePopoverTransition({ open: showSub, placement: 'right' })

  const isAuto = mcpServers === 'auto'
  const isDisabled = mcpServers === 'disable'
  const isCustom = Array.isArray(mcpServers)
  const selectedNames = isCustom ? mcpServers : []
  const serverCount = serverList?.length || 0
  const allServerNames = (serverList || []).map((srv) => srv.name)

  const statusColor = isAuto ? 'var(--green)' : isDisabled ? 'var(--red)' : 'var(--cyan)'

  const enter = () => { clearTimeout(hoverTimeout.current); setShowSub(true) }
  const leave = () => { hoverTimeout.current = setTimeout(() => setShowSub(false), 220) }

  useEffect(() => () => clearTimeout(hoverTimeout.current), [])

  const toggleServer = (name) => {
    const current = isAuto ? allServerNames : Array.isArray(mcpServers) ? [...mcpServers] : []
    const idx = current.indexOf(name)
    if (idx >= 0) {
      current.splice(idx, 1)
      setMcpServers(current.length > 0 ? current : 'disable')
    } else {
      current.push(name)
      setMcpServers(current)
    }
  }

  return (
    <div
      className="relative"
      onMouseEnter={enter}
      onMouseLeave={leave}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <div
        aria-hidden="true"
        style={{ height: 1, margin: '0 12px', background: 'var(--border-subtle)' }}
      />
      <button
        type="button"
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: showSub ? 'var(--bg-surface)' : 'transparent',
          border: 'none', cursor: 'pointer',
          color: 'var(--text-secondary)', textAlign: 'left',
          fontFamily: 'var(--font-ui)', fontSize: 13,
          transition: 'background 150ms ease',
        }}
        onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
        onMouseLeave={(e) => { if (!showSub) e.currentTarget.style.background = 'transparent' }}
        onClick={(e) => {
          e.stopPropagation()
          enter()
        }}
      >
        <Cable size={14} strokeWidth={1.5} style={{ color: statusColor }} />
        <span className="flex-1">MCP</span>
        <ChevronRight size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)' }} />
      </button>

      {subMounted && (
        <div
          ref={subPopRef}
          style={{
            position: 'absolute', left: '100%', bottom: 0,
            marginLeft: 0,
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, minWidth: 184, zIndex: 30,
            pointerEvents: showSub ? 'auto' : 'none',
          }}
          onMouseEnter={enter}
          onMouseLeave={leave}
        >
          <div className="flex items-center gap-1 px-2 py-1" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
            {[
              { value: 'auto', label: t('mcp.policyAuto'), color: 'var(--green)' },
              { value: 'disable', label: t('mcp.policyDisable'), color: 'var(--red)' },
            ].map((mode) => {
              const active = (mode.value === 'auto' && isAuto) || (mode.value === 'disable' && isDisabled)
              return (
                <button
                  type="button"
                  key={mode.value}
	                  className="px-2 py-0"
                  style={{
                    background: active ? 'var(--bg-surface)' : 'transparent',
                    border: active ? `1px solid ${mode.color}` : '1px solid transparent',
                    borderRadius: 4, color: active ? mode.color : 'var(--text-dim)',
                    cursor: 'pointer', fontWeight: active ? 700 : 500,
	                    fontSize: 10, lineHeight: '18px', transition: 'all 150ms ease',
                  }}
                  onClick={(e) => { e.stopPropagation(); setMcpServers(mode.value) }}
                  onMouseEnter={(e) => { if (!active) e.currentTarget.style.color = mode.color }}
                  onMouseLeave={(e) => { if (!active) e.currentTarget.style.color = 'var(--text-dim)' }}
                >
                  {mode.label}
                </button>
              )
            })}
            {isCustom && (
	              <span style={{ color: 'var(--cyan)', fontSize: 9, fontWeight: 600, marginLeft: 'auto' }}>
                {selectedNames.length}/{serverCount}
              </span>
            )}
          </div>

	          <div style={{ maxHeight: 184, overflowY: 'auto', padding: '2px 0' }}>
            {loading ? (
	              <div className="px-2 py-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                {t('sidebar.loading')}
              </div>
            ) : serverList.length === 0 ? (
	              <div className="px-2 py-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                {t('mcp.noServers')}
              </div>
            ) : serverList.map((srv) => {
              const checked = isAuto || selectedNames.includes(srv.name)
              return (
                <button
                  type="button"
                  key={srv.name}
	                  className="flex items-center gap-1 w-full px-2 py-1"
                  style={{
                    background: 'transparent', border: 'none',
                    cursor: 'pointer',
                    color: checked ? 'var(--text-primary)' : 'var(--text-secondary)',
	                    fontSize: 11, textAlign: 'left', transition: 'background 150ms ease',
                    opacity: isDisabled ? 0.4 : 1,
                  }}
                  onClick={(e) => { e.stopPropagation(); toggleServer(srv.name) }}
                  onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                >
                  <span style={{
	                    width: 12, height: 12, borderRadius: 2, flexShrink: 0,
                    border: checked ? '1px solid var(--cyan)' : '1px solid var(--border)',
                    background: checked ? 'var(--cyan)' : 'transparent',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    transition: 'all 150ms ease',
                  }}>
	                    {checked && <span style={{ color: 'var(--text-inverse)', fontSize: 9, fontWeight: 700 }}>&#10003;</span>}
                  </span>
                  <span className="truncate flex-1">{srv.name}</span>
                  <span className="uppercase flex-shrink-0" style={{
	                    fontSize: 8, color: srv.type === 'http' ? 'var(--cyan)' : 'var(--purple)',
                    letterSpacing: '0.06em',
                  }}>
                    {srv.type}
                  </span>
                </button>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
