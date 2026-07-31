import { useRef, useEffect, useState, useCallback } from 'react'
import { Square, Shield, Cable, ChevronRight, X, AlertTriangle, Cpu, CornerDownLeft, FolderPlus } from 'lucide-react'
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
import QueuedMessagesStack from './QueuedMessagesStack'
import TaskProgressCapsule from './TaskProgressCapsule'
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

export default function ChatInput({ cwd, cwdPlacement = 'top' }) {
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
  const clearAttachments = useChatStore((s) => s.clearAttachments)
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
  const textareaRef = useRef(null)
  const isBlocked = !!pendingAskUser || !!pendingPermission || !!pendingPlanApproval

  const [selectedSkill, setSelectedSkill] = useState(null)
  const [showPermissionMenu, setShowPermissionMenu] = useState(false)
  const permMenuRef = useRef(null)
  const { mounted: permissionMenuMounted, popRef: permissionMenuPopRef } = usePopoverTransition({ open: showPermissionMenu, placement: 'top' })
  // Composer-warning callback registered by PromptComposer.
  const composerWarnRef = useRef(null)

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

  const hasUploading = attachments.some((a) => a.status === 'uploading' || a.status === 'processing')
  const doneAttachments = attachments.filter((a) => a.status === 'done')
  const imageAttachments = doneAttachments.filter((a) => a.isImage)
  const fileAttachments = doneAttachments.filter((a) => !a.isImage)

  const handleSend = () => {
    const text = inputText.trim()
    const hasFileRef = !!useChatStore.getState().fileReference
    const hasSelectedXlsxRef = !!useChatStore.getState().selectedXlsxReference
    const hasSelectedFileRef = !!useChatStore.getState().selectedFileReference
    const hasContent = !!text || !!selectedSkill || doneAttachments.length > 0 || hasFileRef || hasSelectedXlsxRef || hasSelectedFileRef
    if (!hasContent || isBlocked || hasUploading) return
    // Running tasks only block fresh sends; mid-stream queueing is still allowed
    if (!isStreaming && hasRunningTasks) return
    const fullText = selectedSkill ? `/${selectedSkill.name} ${text}`.trim() : text
    if (!fullText && doneAttachments.length === 0) return
    setInputText('')
    setSelectedSkill(null)
    setQuickActionVariableMode(false)

    // Handle file reference: prepend XML block and use stored template
    const currentFileRef = useChatStore.getState().fileReference
    const fileRefTemplate = useChatStore.getState().fileReferenceTemplate
    let finalText = fullText
    if (currentFileRef) {
      const xmlBlock = `<file-reference path="${currentFileRef.filePath}" startLine="${currentFileRef.startLine}" endLine="${currentFileRef.endLine}" language="${currentFileRef.language || ''}">\n${currentFileRef.selectedText}\n</file-reference>`
      // Use stored template if available, otherwise build from user text
      if (fileRefTemplate) {
        finalText = xmlBlock + '\n' + fileRefTemplate
      } else {
        finalText = xmlBlock + '\n' + fullText
      }
      useChatStore.getState().clearFileReference()
      useChatStore.getState().clearFileReferenceTemplate()
    }

    const currentSelectedXlsxRef = useChatStore.getState().selectedXlsxReference
    if (currentSelectedXlsxRef) {
      const xmlBlock = buildSelectedXlsxXml(currentSelectedXlsxRef)
      finalText = finalText ? `${xmlBlock}\n${finalText}` : xmlBlock
      useChatStore.getState().clearSelectedXlsxReference()
    }

    const currentSelectedFileRef = useChatStore.getState().selectedFileReference
    if (currentSelectedFileRef) {
      const xmlBlock = buildSelectedFileXml(currentSelectedFileRef)
      finalText = finalText ? `${xmlBlock}\n${finalText}` : xmlBlock
      useChatStore.getState().clearSelectedFileReference()
    }

    const currentQuote = useChatStore.getState().quotedText
    if (currentQuote && finalText) {
      finalText = t('quote.template', { content: currentQuote, feedback: finalText })
      useChatStore.getState().clearQuotedText()
    }

    const attItems = fileAttachments.map((a) => ({ path: a.path, name: a.originalName || a.name }))
    const imageItems = imageAttachments.map((a) => ({
      data: a.base64Data, media_type: a.mediaType, filename: a.name,
    }))
    // Display metadata only — no base64Data/previewUrl. The image bytes
    // already live in the message content blocks; duplicating them here
    // pins multi-MB strings in memory for the session's lifetime.
    const attMeta = doneAttachments.map((a) => ({
      name: a.name, size: a.size, path: a.path,
      isImage: a.isImage || false, mediaType: a.mediaType,
    }))
    // Failed uploads stay visible as error chips (with a warning) so the user
    // notices they were NOT sent; sending itself is not blocked.
    const hasErrorAtts = attachments.some((a) => a.status === 'error')
    if (hasErrorAtts) {
      composerWarnRef.current?.(t('chat.failedAttachmentsKept'))
      clearAttachments({ keepErrors: true })
    } else {
      clearAttachments()
    }

    const messageToSend = finalText || (imageItems.length > 0 ? 'Describe the uploaded image(s).' : 'Please read the uploaded files.')

    if (isStreaming) {
      // Mid-stream: queue for injection at next tool-result boundary
      const queueSender = useChatStore.getState().queueSender
      if (!queueSender?.sendQueue) return
      const id = (typeof crypto !== 'undefined' && crypto.randomUUID)
        ? crypto.randomUUID()
        : `q-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      queueSender.sendQueue({
        id,
        text: messageToSend,
        attachments: attItems.length > 0 ? attItems : undefined,
        images: imageItems.length > 0 ? imageItems : undefined,
      })
      useChatStore.getState().enqueueUserMessage({
        id,
        text: messageToSend,
        attachments: attItems,
        images: imageItems,
        attachmentsMeta: attMeta,
      })
      return
    }

    sendMessage(
      messageToSend,
      permissionMode,
      attItems.length > 0 ? attItems : undefined,
      attMeta.length > 0 ? attMeta : undefined,
      imageItems.length > 0 ? imageItems : undefined,
    )
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
  const visionHintItems = hasImages ? [{ id: 'vision-hint', model: visionModel || null }] : []
  const [lifecycleVisionHints, removeExitedVisionHint] = useListLifecycle(visionHintItems, (item) => item.id)
  const visionHints = lifecycleVisionHints.length > 0 ? (
    <>
      {lifecycleVisionHints.map(({ key, item, present }) => {
        const hasVisionModel = !!item.model
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
              style={{ background: 'var(--bg-elevated)', borderLeft: `2px solid ${hasVisionModel ? 'var(--cyan)' : 'var(--yellow)'}`, borderRadius: 2, fontSize: 11, color: 'var(--text-dim)', userSelect: 'none' }}>
              {hasVisionModel
                ? <Cpu size={12} strokeWidth={1.5} style={{ color: 'var(--cyan)', flexShrink: 0 }} />
                : <AlertTriangle size={12} strokeWidth={1.5} style={{ color: 'var(--yellow)', flexShrink: 0 }} />}
              {hasVisionModel ? t('chat.usingVisionModel', { model: item.model }) : t('chat.noVisionModel')}
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
    <div className="flex items-center gap-1">
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
      <div style={{ maxWidth: 900, width: '80%', margin: '0 auto' }}>
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

        <TaskProgressCapsule />

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
      style={{ borderTop: '1px solid var(--border-subtle)' }}
      onMouseEnter={enter}
      onMouseLeave={leave}
      onMouseDown={(e) => e.stopPropagation()}
    >
      <button
        type="button"
        className="flex items-center gap-2 px-3 py-2 w-full text-sm"
        style={{
          background: showSub ? 'var(--bg-surface)' : 'transparent',
          border: 'none', cursor: 'pointer',
          color: 'var(--text-secondary)', textAlign: 'left',
          fontFamily: "'Noto Sans', sans-serif", fontSize: 13,
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
