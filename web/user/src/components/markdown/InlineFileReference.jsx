import { useEffect, useMemo, useState } from 'react'
import { useTranslation } from 'react-i18next'
import useAuthStore from '@shared/stores/authStore'
import useUiStore from '@shared/stores/uiStore'
import { probeFilePreview } from '../../api/userFiles'
import useFileBrowserStore from '../../stores/fileBrowserStore'
import { getFileIcon } from '../../utils/fileIcons'
import { resolveFilePathAgainstCwd } from '../../utils/filePath'
import { createFilePreviewProbe } from '../../utils/filePreviewProbe'
import { useMarkdownRenderContext } from './MarkdownRenderContext'

const MAX_FILE_CANDIDATE_LENGTH = 4096
const probeInlineFile = createFilePreviewProbe(probeFilePreview)

const INLINE_CODE_STYLE = {
  background: 'var(--bg-elevated)',
  color: 'var(--cyan)',
  padding: '1px 5px',
  borderRadius: 3,
  fontSize: '0.9em',
  border: '1px solid var(--border)',
  fontFamily: 'var(--font-code)',
}

function extractText(node) {
  if (node == null) return ''
  if (typeof node === 'string' || typeof node === 'number') return String(node)
  if (Array.isArray(node)) return node.map(extractText).join('')
  return extractText(node.props?.children)
}

function fileName(path) {
  const segments = String(path || '').split('/').filter(Boolean)
  return segments[segments.length - 1] || path || ''
}

function PlainInlineCode({ children }) {
  return <code style={INLINE_CODE_STYLE}>{children}</code>
}

export default function InlineFileReference({ children }) {
  const { t } = useTranslation()
  const { resolveInlineFiles, inlineFileProbeDeferred, filePreviewCwd } = useMarkdownRenderContext()
  const displayText = useMemo(() => extractText(children), [children])
  const candidate = displayText.trim()
  const resolvedPath = useMemo(
    () => resolveFilePathAgainstCwd(candidate, filePreviewCwd),
    [candidate, filePreviewCwd],
  )
  const [probeResult, setProbeResult] = useState(null)

  useEffect(() => {
    if (
      !resolveInlineFiles
      || inlineFileProbeDeferred
      || !candidate
      || candidate.length > MAX_FILE_CANDIDATE_LENGTH
    ) return undefined

    let active = true
    const user = useAuthStore.getState().user
    const cacheScope = user?.username || user?.id || 'anonymous'
    probeInlineFile(resolvedPath, `${cacheScope}\0${resolvedPath}`).then((preview) => {
      if (active) setProbeResult({ path: resolvedPath, preview })
    })
    return () => { active = false }
  }, [candidate, inlineFileProbeDeferred, resolveInlineFiles, resolvedPath])

  const preview = resolveInlineFiles && probeResult?.path === resolvedPath
    ? probeResult.preview
    : null
  if (!preview) return <PlainInlineCode>{children}</PlainInlineCode>

  const previewPath = preview.path || resolvedPath
  const previewName = preview.name || fileName(previewPath) || candidate
  const label = t('fileBrowser.openInlineFile', {
    name: previewName,
    defaultValue: `Open ${previewName} in File Browser`,
  })

  const setInteractiveState = (element, active) => {
    element.style.color = 'var(--blue)'
    element.style.textDecoration = active ? 'underline' : 'none'
  }

  const openInFileBrowser = () => {
    useFileBrowserStore.getState().openFile({
      filePath: previewPath,
      name: previewName,
      mimeType: preview.mimeType || preview.mime_type || null,
      extension: preview.extension || null,
      size: typeof preview.size === 'number' ? preview.size : null,
      source: 'assistant-inline-code',
    })
    useUiStore.getState().openCanvasTab('file-browser')
  }

  return (
    <button
      type="button"
      aria-label={label}
      title={`${label}\n${previewPath}`}
      onClick={openInFileBrowser}
      style={{
        display: 'inline',
        background: 'transparent',
        border: 'none',
        borderRadius: 0,
        padding: 0,
        color: 'var(--blue)',
        cursor: 'pointer',
        verticalAlign: 'baseline',
        lineHeight: 'inherit',
        fontFamily: 'var(--font-ui)',
        fontSize: INLINE_CODE_STYLE.fontSize,
        textDecoration: 'none',
        outline: 'none',
        appearance: 'none',
        transition: 'color 150ms ease, text-decoration-color 150ms ease',
      }}
      onMouseEnter={(event) => setInteractiveState(event.currentTarget, true)}
      onMouseLeave={(event) => setInteractiveState(event.currentTarget, false)}
      onFocus={(event) => setInteractiveState(event.currentTarget, true)}
      onBlur={(event) => setInteractiveState(event.currentTarget, false)}
    >
      <span
        aria-hidden="true"
        style={{
          display: 'inline-flex',
          verticalAlign: 'middle',
          lineHeight: 1,
          marginInlineEnd: 4,
        }}
      >
        {getFileIcon({ name: previewName, type: 'file' }, '1em')}
      </span>
      <span style={{ minWidth: 0, wordBreak: 'break-word', overflowWrap: 'anywhere' }}>{children}</span>
    </button>
  )
}
