import { useEffect, useMemo, useState } from 'react'
import hljs from 'highlight.js/lib/core'
import 'highlight.js/styles/github-dark.css'
import bash from 'highlight.js/lib/languages/bash'
import python from 'highlight.js/lib/languages/python'
import javascript from 'highlight.js/lib/languages/javascript'
import typescript from 'highlight.js/lib/languages/typescript'
import yaml from 'highlight.js/lib/languages/yaml'
import json from 'highlight.js/lib/languages/json'
import xml from 'highlight.js/lib/languages/xml'
import css from 'highlight.js/lib/languages/css'
import sql from 'highlight.js/lib/languages/sql'
import go from 'highlight.js/lib/languages/go'
import rust from 'highlight.js/lib/languages/rust'
import java from 'highlight.js/lib/languages/java'
import markdown from 'highlight.js/lib/languages/markdown'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'
import RichFilePreview from './RichFilePreview'
import MarkdownRenderer from '../markdown/MarkdownRenderer'
import VirtualizedCodeLines from './VirtualizedCodeLines'
import MermaidDiagram from '../markdown/MermaidDiagram'
import ExcalidrawDiagram from '../markdown/ExcalidrawDiagram'

function registerLanguage(name, definition) {
  if (hljs.getLanguage(name)) return
  hljs.registerLanguage(name, definition)
}

registerLanguage('bash', bash)
registerLanguage('python', python)
registerLanguage('javascript', javascript)
registerLanguage('typescript', typescript)
registerLanguage('yaml', yaml)
registerLanguage('json', json)
registerLanguage('xml', xml)
registerLanguage('html', xml)
registerLanguage('css', css)
registerLanguage('sql', sql)
registerLanguage('go', go)
registerLanguage('rust', rust)
registerLanguage('java', java)
registerLanguage('markdown', markdown)
registerLanguage('dockerfile', dockerfile)
registerLanguage('ini', ini)
registerLanguage('plaintext', () => ({ contains: [] }))

const EXT_TO_LANG = {
  '.py': 'python', '.js': 'javascript', '.jsx': 'javascript', '.ts': 'typescript', '.tsx': 'typescript',
  '.go': 'go', '.rs': 'rust', '.java': 'java', '.sh': 'bash', '.bash': 'bash', '.zsh': 'bash',
  '.sql': 'sql', '.css': 'css', '.json': 'json', '.yaml': 'yaml', '.yml': 'yaml',
  '.xml': 'xml', '.html': 'html', '.htm': 'html', '.md': 'markdown', '.markdown': 'markdown',
  '.ini': 'ini', '.conf': 'ini', '.env': 'ini', '.toml': 'ini',
  '.dockerfile': 'dockerfile',
  '.excalidraw': 'json',
}

const PLAIN_TEXT_EXTENSIONS = new Set([
  '.txt', '.md', '.markdown', '.html', '.htm', '.css', '.js', '.jsx', '.ts', '.tsx',
  '.json', '.yaml', '.yml', '.toml', '.xml', '.csv', '.tsv', '.log', '.conf', '.ini',
  '.env', '.dockerfile', '.py', '.java', '.go', '.rs', '.rb', '.php', '.c', '.cpp',
  '.h', '.hpp', '.swift', '.kt', '.scala', '.r', '.lua', '.sh', '.sql',
  '.excalidraw',
])

const RICH_PREVIEW_EXTS = new Set([
  '.html', '.htm', '.pdf', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp',
  '.bmp', '.avif', '.xlsx', '.xls', '.csv', '.tsv', '.pptx', '.docx', '.doc',
])

export function normalizeFileExtension(ext) {
  if (!ext) return ''
  const clean = String(ext).trim().toLowerCase()
  if (!clean) return ''
  return clean.startsWith('.') ? clean : `.${clean}`
}

export function getPreviewFileExtension(file) {
  if (file?.ext) return normalizeFileExtension(file.ext)
  const name = file?.path || file?.name || file?.original_name || ''
  const idx = name.lastIndexOf('.')
  return idx >= 0 ? normalizeFileExtension(name.slice(idx)) : ''
}

function getPreviewFileName(file) {
  return file?.path || file?.name || file?.original_name || ''
}

export function detectFileLanguage(fileOrPath) {
  const raw = typeof fileOrPath === 'string' ? fileOrPath : getPreviewFileName(fileOrPath)
  if (!raw) return 'plaintext'
  const lower = raw.toLowerCase()
  if (lower.endsWith('/dockerfile') || lower === 'dockerfile') return 'dockerfile'
  const ext = typeof fileOrPath === 'string'
    ? normalizeFileExtension(lower.slice(lower.lastIndexOf('.') >= 0 ? lower.lastIndexOf('.') : lower.length))
    : getPreviewFileExtension(fileOrPath)
  return EXT_TO_LANG[ext] || 'plaintext'
}

export function isPlainTextFile(file) {
  const mime = String(file?.mimeType || file?.mime_type || '').toLowerCase()
  if (mime.startsWith('text/')) return true
  if (mime === 'application/json' || mime.includes('xml') || mime.includes('yaml')) return true
  return PLAIN_TEXT_EXTENSIONS.has(getPreviewFileExtension(file))
}

function LoadingSkeleton({ diagram = false }) {
  return (
    <div className="flex flex-col gap-2 p-4">
      <div className="skeleton" style={{ width: '100%', height: 16 }} />
      <div className="skeleton" style={{ width: '80%', height: diagram ? 200 : 16 }} />
      {!diagram && <div className="skeleton" style={{ width: '60%', height: 16 }} />}
    </div>
  )
}

function PreviewError({ error }) {
  return <div className="p-4 text-xs" style={{ color: 'var(--red)' }}>{error}</div>
}

function useTextContent({ file, cacheKey, loadText, fallbackText = null, onTextLoaded }) {
  const [content, setContent] = useState('')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const key = cacheKey || file?.path || file?.uuid || file?.name || file?.original_name || getPreviewFileExtension(file)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    setContent('')

    const run = async () => {
      try {
        const text = typeof file?.content === 'string'
          ? file.content
          : typeof loadText === 'function'
            ? await loadText()
            : fallbackText != null
              ? fallbackText
              : ''
        if (!cancelled) {
          setContent(text)
          onTextLoaded?.(text)
        }
      } catch (err) {
        if (!cancelled && fallbackText != null) {
          setContent(fallbackText)
          onTextLoaded?.(fallbackText)
          return
        }
        if (!cancelled) setError(err?.message || String(err))
      } finally {
        if (!cancelled) setLoading(false)
      }
    }

    run()
    return () => {
      cancelled = true
    }
  }, [cacheKey, fallbackText, file, key, loadText, onTextLoaded])

  return { content, loading, error }
}

function HighlightedCode({ content, language }) {
  const highlighted = useMemo(() => {
    if (!content) return null
    try {
      if (language && hljs.getLanguage(language)) {
        return hljs.highlight(content, { language }).value
      }
      return hljs.highlightAuto(content).value
    } catch {
      return null
    }
  }, [content, language])

  const lines = useMemo(() => {
    if (!content) return []
    const raw = content.replace(/\n$/, '')
    if (!highlighted) {
      return raw.split('\n').map((line) => ({ text: line, html: null }))
    }
    return highlighted.replace(/\n$/, '').split('\n').map((html) => ({ text: null, html }))
  }, [content, highlighted])

  return (
    <div className="flex-1 min-w-0 min-h-0 overflow-hidden" style={{ width: '100%', height: '100%' }}>
      <VirtualizedCodeLines lines={lines} />
    </div>
  )
}

export function RawFilePreview({ file, cacheKey, loadText, fallbackText, onTextLoaded }) {
  const { content, loading, error } = useTextContent({ file, cacheKey, loadText, fallbackText, onTextLoaded })
  if (loading) return <LoadingSkeleton />
  if (error) return <PreviewError error={error} />
  return <HighlightedCode content={content} language={detectFileLanguage(file)} />
}

function MarkdownPreview({ file, cacheKey, loadText, fallbackText, onTextLoaded }) {
  const { content, loading, error } = useTextContent({ file, cacheKey, loadText, fallbackText, onTextLoaded })
  if (loading) return <LoadingSkeleton />
  if (error) return <PreviewError error={error} />
  return (
    <div className="overflow-auto" style={{ height: '100%', padding: '12px 16px' }}>
      <MarkdownRenderer content={content} />
    </div>
  )
}

function MermaidPreview({ file, cacheKey, loadText, fallbackText, onTextLoaded }) {
  const { content, loading, error } = useTextContent({ file, cacheKey, loadText, fallbackText, onTextLoaded })
  if (loading) return <LoadingSkeleton diagram />
  if (error) return <PreviewError error={error} />
  return (
    <div
      className="overflow-hidden"
      style={{
        width: '100%',
        minWidth: 0,
        height: '100%',
        minHeight: 0,
        padding: '12px 16px',
        boxSizing: 'border-box',
      }}
    >
      <MermaidDiagram code={content} fill />
    </div>
  )
}

function ExcalidrawPreview({ file, cacheKey, loadText, fallbackText, onTextLoaded }) {
  const { content, loading, error } = useTextContent({ file, cacheKey, loadText, fallbackText, onTextLoaded })
  if (loading) return <LoadingSkeleton diagram />
  if (error) return <PreviewError error={error} />
  return (
    <div
      className="overflow-hidden"
      style={{
        width: '100%',
        minWidth: 0,
        height: '100%',
        minHeight: 0,
        padding: '12px 16px',
        boxSizing: 'border-box',
      }}
    >
      <ExcalidrawDiagram code={content} fill />
    </div>
  )
}

function CodePreview({ file, cacheKey, loadText, fallbackText, onTextLoaded }) {
  const { content, loading, error } = useTextContent({ file, cacheKey, loadText, fallbackText, onTextLoaded })
  if (loading) return <LoadingSkeleton />
  if (error) return <PreviewError error={error} />
  return <HighlightedCode content={content} language={detectFileLanguage(file)} />
}

export default function FilePreviewRenderer({
  file,
  cacheKey,
  loadText,
  loadArrayBuffer,
  loadBlob,
  fallbackText = null,
  onTextLoaded,
}) {
  const ext = getPreviewFileExtension(file)
  const textProps = { file, cacheKey, loadText, fallbackText, onTextLoaded }

  if (ext === '.md' || ext === '.markdown') {
    return <MarkdownPreview {...textProps} />
  }

  if (ext === '.mmd' || ext === '.mermaid') {
    return <MermaidPreview {...textProps} />
  }

  if (ext === '.excalidraw') {
    return <ExcalidrawPreview {...textProps} />
  }

  if (RICH_PREVIEW_EXTS.has(ext)) {
    return (
      <RichFilePreview
        file={file}
        cacheKey={cacheKey}
        loadText={loadText}
        loadArrayBuffer={loadArrayBuffer}
        loadBlob={loadBlob}
        fallbackText={fallbackText}
      />
    )
  }

  if (isPlainTextFile(file)) {
    return <CodePreview {...textProps} />
  }

  return (
    <RichFilePreview
      file={file}
      cacheKey={cacheKey}
      loadText={loadText}
      loadArrayBuffer={loadArrayBuffer}
      loadBlob={loadBlob}
      fallbackText={fallbackText}
    />
  )
}
