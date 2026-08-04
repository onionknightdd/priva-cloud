import { useState, useEffect, useCallback, useMemo, useRef } from 'react'
import { createPortal } from 'react-dom'
import { Folder, FolderPlus, FileText, Download, Upload, ChevronRight, Search, ArrowUp, ArrowDown, Copy, Sparkles } from 'lucide-react'
import { getFileIcon } from '../../utils/fileIcons'
import { useTranslation } from 'react-i18next'
import OptimizePopup from '../shared/OptimizePopup'
import getLineFromNode from '../../utils/getLineFromNode'
import { copyTextToClipboard } from '@shared/utils/clipboard'
import { listDirectory, previewFile, downloadFile, uploadUserFile } from '../../api/userFiles'
import { useResizable } from '@shared/hooks/useResizable'
import safeStorage from '@shared/utils/safeStorage'
import { formatDateTime } from '../../utils/formatTime'
import DrawIcon from '@shared/components/shared/DrawIcon'
import FilePreviewRenderer, { detectFileLanguage } from '../shared/FilePreviewRenderer'
import CreateDirectoryDialog from '../shared/CreateDirectoryDialog'

function formatSize(bytes) {
  if (bytes == null) return ''
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatDate(ts) {
  if (!ts) return ''
  return formatDateTime(ts * 1000)
}

function getEntryIcon(entry) {
  return getFileIcon(entry, 14)
}

function SortableHeader({ label, field, sortField, sortDir, onSort, width, textAlign, flex }) {
  const isActive = sortField === field
  return (
    <button
      className={`flex items-center gap-1 uppercase${flex ? ' flex-1' : ''}`}
      style={{
        background: 'transparent',
        border: 'none',
        cursor: 'pointer',
        padding: 0,
        color: isActive ? 'var(--text-secondary)' : 'var(--text-dim)',
        width,
        flexShrink: flex ? undefined : 0,
        minWidth: 0,
        textAlign: textAlign || 'left',
        justifyContent: textAlign === 'right' ? 'flex-end' : 'flex-start',
        fontSize: 'inherit',
        fontWeight: 'inherit',
        letterSpacing: 'inherit',
        transition: 'color 150ms ease',
      }}
      onClick={() => onSort(field)}
      onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.color = 'var(--text-dim)' }}
    >
      <span className="truncate">{label}</span>
      {isActive && (sortDir === 'asc'
        ? <ArrowUp size={10} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        : <ArrowDown size={10} strokeWidth={1.5} style={{ flexShrink: 0 }} />
      )}
    </button>
  )
}

export default function FileManagerTab() {
  const { t } = useTranslation()
  const [pathInput, setPathInput] = useState('~')
  const [entries, setEntries] = useState([])
  const [resolvedPath, setResolvedPath] = useState('')
  const [parentPath, setParentPath] = useState(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState(null)
  const [searchQuery, setSearchQuery] = useState('')
  const [sortField, setSortField] = useState(null)
  const [sortDir, setSortDir] = useState('asc')

  // Preview state
  const [preview, setPreview] = useState(null)
  const [previewText, setPreviewText] = useState(null)
  const [selectedFileName, setSelectedFileName] = useState(null)

  // Selection tooltip + optimize popup state (Ask for Priva)
  const [tooltip, setTooltip] = useState(null)
  const [optimizeData, setOptimizeData] = useState(null)
  const previewContentRef = useRef(null)
  const tooltipSetAtRef = useRef(0)

  // Upload state
  const [uploading, setUploading] = useState(false)
  const [uploadProgress, setUploadProgress] = useState(0)
  const fileInputRef = useRef(null)
  const [createDirectoryOpen, setCreateDirectoryOpen] = useState(false)

  // Resizable file list width (default 50%)
  const [fileListWidth, setFileListWidth] = useState(() => (
    safeStorage.getNumber('file-explorer-list-width', 0, { min: 200, max: 800 })
  ))
  const handleListResize = useCallback((w) => {
    setFileListWidth(w)
    safeStorage.setItem('file-explorer-list-width', String(w))
  }, [])

  const fetchDir = useCallback(async (path) => {
    setLoading(true)
    setError(null)
    try {
      const data = await listDirectory(path)
      setEntries(data.entries)
      setResolvedPath(data.path)
      setParentPath(data.parent)
      setPathInput(data.path)
    } catch (e) {
      setError(e.message)
      setEntries([])
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { fetchDir('~') }, [fetchDir])

  const navigateTo = (path) => {
    // Single-tenant pod: the explorer browses the whole pod filesystem; access is
    // governed solely by the sandbox uid's OS permissions (no app-level path gate).
    setPreview(null)
    setPreviewText(null)
    setSelectedFileName(null)
    setSearchQuery('')
    setSortField(null)
    fetchDir(path)
  }

  const handleGo = () => {
    const val = pathInput.trim()
    if (!val) return
    navigateTo(val)
  }

  const handleKeyDown = (e) => {
    if (e.key === 'Enter') handleGo()
  }

  const handleSort = (field) => {
    if (sortField === field) {
      setSortDir((d) => d === 'asc' ? 'desc' : 'asc')
    } else {
      setSortField(field)
      setSortDir('asc')
    }
  }

  // Filter and sort entries
  const filteredEntries = useMemo(() => {
    let list = entries
    if (searchQuery) {
      const q = searchQuery.toLowerCase()
      list = list.filter((e) => e.name.toLowerCase().includes(q))
    }
    if (sortField) {
      list = [...list].sort((a, b) => {
        let cmp = 0
        if (sortField === 'name') {
          cmp = a.name.localeCompare(b.name)
        } else if (sortField === 'size') {
          cmp = (a.size ?? -1) - (b.size ?? -1)
        } else if (sortField === 'modified') {
          cmp = (a.modified ?? 0) - (b.modified ?? 0)
        }
        return sortDir === 'desc' ? -cmp : cmp
      })
    }
    return list
  }, [entries, searchQuery, sortField, sortDir])

  const handleFileClick = (entry) => {
    const fullPath = resolvedPath === '/' ? `/${entry.name}` : `${resolvedPath}/${entry.name}`
    setSelectedFileName(entry.name)
    setPreviewText(null)
    setPreview({
      name: entry.name,
      path: fullPath,
      size: entry.size,
      modified: entry.modified,
      mime_type: entry.mime_type || entry.mimeType || null,
      ext: entry.ext || null,
    })
  }

  const previewCacheKey = useMemo(() => {
    if (!preview?.path) return null
    return `${preview.path}:${preview.modified || ''}:${preview.size ?? ''}`
  }, [preview?.modified, preview?.path, preview?.size])

  const loadPreviewText = useCallback(async () => {
    if (!preview?.path) return ''
    try {
      const data = await previewFile(preview.path, { cacheBustKey: previewCacheKey })
      if (typeof data?.content === 'string') return data.content
    } catch { /* fall back to download */ }

    const blob = await downloadFile(preview.path, {
      cacheBustKey: previewCacheKey,
      cacheMode: 'no-store',
    })
    return blob.text()
  }, [preview?.path, previewCacheKey])

  const loadPreviewBlob = useCallback(async () => {
    if (!preview?.path) throw new Error('No file selected')
    return downloadFile(preview.path, {
      cacheBustKey: previewCacheKey,
      cacheMode: 'no-store',
    })
  }, [preview?.path, previewCacheKey])

  const loadPreviewArrayBuffer = useCallback(async () => {
    const blob = await loadPreviewBlob()
    return blob.arrayBuffer()
  }, [loadPreviewBlob])

  const handleDownload = async (entry) => {
    const fullPath = resolvedPath === '/' ? `/${entry.name}` : `${resolvedPath}/${entry.name}`
    try {
      const blob = await downloadFile(fullPath)
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = entry.name
      a.click()
      URL.revokeObjectURL(url)
    } catch { /* ignore */ }
  }

  const handleUpload = async (e) => {
    const file = e.target.files?.[0]
    if (!file) return
    setUploading(true)
    setUploadProgress(0)
    try {
      await uploadUserFile(resolvedPath, file, (p) => setUploadProgress(p))
      fetchDir(resolvedPath)
    } catch (err) {
      setError(err.message)
    } finally {
      setUploading(false)
      setUploadProgress(0)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  const handleDirectoryCreated = useCallback(() => {
    setCreateDirectoryOpen(false)
    fetchDir(resolvedPath || '~')
  }, [fetchDir, resolvedPath])

  // Text selection detection for "Ask for Priva"
  useEffect(() => {
    const onMouseUp = (e) => {
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed || !selection.toString().trim()) return

      const text = selection.toString()
      const range = selection.getRangeAt(0)
      const ancestor = range.commonAncestorContainer

      if (previewContentRef.current
        && !previewContentRef.current.contains(ancestor)
        && !(ancestor.contains?.(previewContentRef.current))) {
        return
      }

      let startLine = getLineFromNode(range.startContainer, range.startOffset)
      let endLine = getLineFromNode(range.endContainer, Math.max(0, range.endOffset - 1))

      // Fallback for multi-row selection
      if ((!startLine || !endLine) && previewContentRef.current) {
        const tbody = previewContentRef.current.querySelector('table')?.tBodies?.[0]
        if (tbody && tbody.rows.length > 0) {
          if (!startLine) {
            for (let i = 0; i < tbody.rows.length; i++) {
              if (range.intersectsNode(tbody.rows[i])) { startLine = i + 1; break }
            }
          }
          if (!endLine) {
            for (let i = tbody.rows.length - 1; i >= 0; i--) {
              if (range.intersectsNode(tbody.rows[i])) { endLine = i + 1; break }
            }
          }
        }
      }

      if (!startLine || !endLine) return

      tooltipSetAtRef.current = Date.now()
      setTooltip({
        x: e.clientX + 8,
        y: e.clientY + 8,
        startLine: Math.min(startLine, endLine),
        endLine: Math.max(startLine, endLine),
        selectedText: text,
      })
    }

    document.addEventListener('mouseup', onMouseUp)
    return () => document.removeEventListener('mouseup', onMouseUp)
  }, [])

  // Hide tooltip when selection cleared
  useEffect(() => {
    const onSelectionChange = () => {
      if (Date.now() - tooltipSetAtRef.current < 150) return
      const selection = window.getSelection()
      if (!selection || selection.isCollapsed) {
        setTooltip(null)
      }
    }
    document.addEventListener('selectionchange', onSelectionChange)
    return () => document.removeEventListener('selectionchange', onSelectionChange)
  }, [])

  // Handle "Ask for Priva" tooltip click
  const handleAskPrivaClick = useCallback(() => {
    if (!tooltip || !preview || previewText == null) return
    const allLines = previewText.replace(/\n$/, '').split('\n')
    const selStart = tooltip.startLine
    const selEnd = tooltip.endLine
    const ctxStart = Math.max(1, selStart - 2)
    const ctxEnd = Math.min(allLines.length, selEnd + 2)
    const previewLines = []
    for (let i = ctxStart; i <= ctxEnd; i++) {
      previewLines.push({
        lineNum: i,
        text: allLines[i - 1] || '',
        isSelected: i >= selStart && i <= selEnd,
      })
    }
    const language = detectFileLanguage(preview.path || preview.name) || ''
    setOptimizeData({
      source: 'file',
      filePath: preview.path || '',
      startLine: selStart,
      endLine: selEnd,
      selectedText: tooltip.selectedText,
      language,
      previewLines,
      anchorX: tooltip.x,
      anchorY: tooltip.y,
    })
    setTooltip(null)
    window.getSelection()?.removeAllRanges()
  }, [tooltip, preview, previewText])

  // Handle Sparkles icon click in preview header (full file)
  const handleAskPrivaFull = useCallback(() => {
    if (!preview || previewText == null) return
    const allLines = previewText.replace(/\n$/, '').split('\n')
    const totalLines = allLines.length
    const ctxEnd = Math.min(totalLines, 4)
    const previewLines = []
    for (let i = 1; i <= ctxEnd; i++) {
      previewLines.push({ lineNum: i, text: allLines[i - 1] || '', isSelected: true })
    }
    if (totalLines > 4) {
      previewLines.push({ lineNum: -1, text: `... (${totalLines - 4} more lines)`, isSelected: false })
    }
    const language = detectFileLanguage(preview.path || preview.name) || ''
    setOptimizeData({
      source: 'file',
      filePath: preview.path || '',
      startLine: 1,
      endLine: totalLines,
      selectedText: previewText.replace(/\n$/, ''),
      language,
      previewLines,
      anchorX: Math.min(window.innerWidth / 2, window.innerWidth - 460),
      anchorY: 120,
    })
  }, [preview, previewText])

  // Breadcrumb segments (absolute — the explorer browses the whole pod filesystem)
  const segments = resolvedPath ? resolvedPath.split('/').filter(Boolean) : []
  const hasPreview = true

  // Stats
  const fileCount = entries.filter((e) => e.type === 'file').length
  const dirCount = entries.filter((e) => e.type === 'directory').length


  return (
    <div className="flex flex-col flex-1" style={{ minHeight: 0, minWidth: 0, height: '100%' }}>
      {/* Top bar: Breadcrumb + Path input */}
      <div
        className="flex items-center gap-2 px-4 py-2 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        {/* Breadcrumb */}
        <div className="flex items-center gap-1 overflow-hidden" style={{ fontSize: 12, minWidth: 0 }}>
          <button
            style={{
              background: 'transparent', border: 'none', cursor: 'pointer',
              color: 'var(--blue)', padding: '0 2px', fontSize: 12, flexShrink: 0,
            }}
            onClick={() => navigateTo('/')}
          >
            /
          </button>
          {segments.map((seg, i) => {
            const segPath = '/' + segments.slice(0, i + 1).join('/')
            const isLast = i === segments.length - 1
            return (
              <span key={segPath} className="flex items-center gap-1" style={{ minWidth: 0 }}>
                <ChevronRight size={10} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
                {isLast ? (
                  <span className="truncate" style={{ color: 'var(--text-primary)', fontWeight: 600 }}>{seg}</span>
                ) : (
                  <button
                    className="truncate"
                    style={{
                      background: 'transparent', border: 'none', cursor: 'pointer',
                      color: 'var(--blue)', padding: 0, fontSize: 12,
                    }}
                    onClick={() => navigateTo(segPath)}
                  >
                    {seg}
                  </button>
                )}
              </span>
            )
          })}
        </div>

        {/* Upload */}
        <input type="file" ref={fileInputRef} onChange={handleUpload} style={{ display: 'none' }} />
        <button
          className="flex items-center gap-1 px-2 py-1 flex-shrink-0"
          style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, cursor: uploading ? 'wait' : 'pointer',
            color: 'var(--text-secondary)', fontSize: 12,
            transition: 'border-color 150ms ease',
          }}
          onClick={() => fileInputRef.current?.click()}
          disabled={uploading}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--blue)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <Upload size={12} strokeWidth={1.5} />
          <span>{t('settings.fileManagerUpload')}</span>
        </button>
        <button
          className="flex items-center justify-center px-2 py-1 flex-shrink-0"
          style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, cursor: resolvedPath ? 'pointer' : 'not-allowed',
            color: resolvedPath ? 'var(--text-secondary)' : 'var(--text-dim)',
            transition: 'border-color 150ms ease',
          }}
          type="button"
          onClick={() => { setTooltip(null); setCreateDirectoryOpen(true) }}
          disabled={!resolvedPath}
          title={t('picker.newFolder')}
          aria-label={t('picker.newFolder')}
          onMouseEnter={(e) => { if (resolvedPath) e.currentTarget.style.borderColor = 'var(--blue)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          <FolderPlus size={14} strokeWidth={1.5} />
        </button>

        <div className="flex-1" />

        {/* Path input + Go */}
        <input
          value={pathInput}
          onChange={(e) => setPathInput(e.target.value)}
          onKeyDown={handleKeyDown}
          style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, color: 'var(--text-primary)', padding: '4px 8px',
            fontSize: 12, fontFamily: 'var(--font-mono)',
            outline: 'none', flexShrink: 1, minWidth: 220, width: 280,
          }}
          placeholder={t('settings.fileManagerPath')}
        />
        <button
          className="px-2 py-1 flex-shrink-0"
          style={{
            background: 'var(--bg-elevated)', border: '1px solid var(--border)',
            borderRadius: 4, cursor: 'pointer', color: 'var(--text-secondary)',
            fontSize: 12, transition: 'border-color 150ms ease',
          }}
          onClick={handleGo}
          onMouseEnter={(e) => { e.currentTarget.style.borderColor = 'var(--blue)' }}
          onMouseLeave={(e) => { e.currentTarget.style.borderColor = 'var(--border)' }}
        >
          {t('settings.fileManagerGo')}
        </button>
      </div>

      {/* Upload progress */}
      {uploading && (
        <div className="flex-shrink-0" style={{ height: 2, background: 'var(--bg-elevated)' }}>
          <div style={{
            height: '100%', width: `${uploadProgress * 100}%`,
            background: 'var(--blue)', transition: 'width 150ms ease',
          }} />
        </div>
      )}

      {/* Search + stats bar */}
      <div
        className="flex items-center gap-4 px-4 py-2 flex-shrink-0"
        style={{ borderBottom: '1px solid var(--border)' }}
      >
        <span className="text-xs flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
          {dirCount} dirs, {fileCount} files
        </span>
        <div
          className="flex items-center gap-2 px-3 py-1"
          style={{
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            flex: 1,
            maxWidth: 240,
          }}
        >
          <Search size={14} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
          <input
            className="flex-1"
            placeholder={t('userData.searchFiles')}
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            style={{
              background: 'transparent',
              border: 'none',
              outline: 'none',
              color: 'var(--text-primary)',
              fontSize: 12,
              minWidth: 0,
            }}
          />
        </div>
      </div>

      {/* Error */}
      {error && (
        <div className="px-4 py-2 flex-shrink-0" style={{ color: 'var(--red)', fontSize: 12, borderBottom: '1px solid var(--border-subtle)' }}>
          {error}
        </div>
      )}

      {/* Main content: file list + preview side by side */}
      <div className="flex flex-1" style={{ minHeight: 0, overflow: 'hidden' }}>
        {/* File list pane */}
        <div
          className="flex flex-col"
          style={{
            minHeight: 0,
            width: fileListWidth || '50%',
            minWidth: 200,
            flexShrink: 0,
            borderRight: '1px solid var(--border)',
          }}
        >
          {/* Table header */}
          <div
            className="flex items-center gap-3 px-4 py-2 text-xs uppercase flex-shrink-0"
            style={{
              borderBottom: '1px solid var(--border)',
              color: 'var(--text-dim)',
              letterSpacing: '0.06em',
              fontWeight: 600,
            }}
          >
            <span style={{ width: 16, flexShrink: 0 }} />
            <SortableHeader label={t('userData.fileName')} field="name" sortField={sortField} sortDir={sortDir} onSort={handleSort} flex />
            <SortableHeader label={t('userData.fileSize')} field="size" sortField={sortField} sortDir={sortDir} onSort={handleSort} width={80} textAlign="right" />
            <SortableHeader label="Modified" field="modified" sortField={sortField} sortDir={sortDir} onSort={handleSort} width={140} textAlign="right" />
          </div>

          {/* File rows */}
          <div className="flex-1 overflow-y-auto" style={{ minHeight: 0 }}>
            {loading ? (
              <div className="flex flex-col gap-1 p-3">
                {[1, 2, 3, 4, 5].map((i) => (
                  <div key={i} className="skeleton" style={{ height: 28, borderRadius: 2 }} />
                ))}
              </div>
            ) : (
              <>
                {/* Parent directory */}
                {parentPath && (
                  <div
                    className="flex items-center gap-3 px-4 py-2"
                    style={{
                      borderBottom: '1px solid var(--border-subtle)',
                      cursor: 'pointer',
                      transition: 'background 150ms ease',
                    }}
                    onClick={() => navigateTo(parentPath)}
                    onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-surface)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
                  >
                    <Folder size={14} strokeWidth={1.5} style={{ color: 'var(--yellow)', flexShrink: 0 }} />
                    <span className="flex-1 text-sm" style={{ color: 'var(--text-primary)' }}>..</span>
                    <span style={{ width: 80, flexShrink: 0 }} />
                    <span style={{ width: 140, flexShrink: 0 }} />
                  </div>
                )}

                {filteredEntries.length === 0 && !parentPath && (
                  <div className="text-sm" style={{ color: 'var(--text-dim)', padding: '40px 20px', textAlign: 'center' }}>
                    {t('settings.fileManagerEmpty')}
                  </div>
                )}

                {filteredEntries.map((entry) => {
                  const isSelected = entry.type === 'file' && entry.name === selectedFileName
                  return (
                    <div
                      key={entry.name}
                      className="flex items-center gap-3 px-4 py-2"
                      style={{
                        borderBottom: '1px solid var(--border-subtle)',
                        cursor: 'pointer',
                        transition: 'background 150ms ease',
                        background: isSelected ? 'var(--bg-elevated)' : 'transparent',
                        borderLeft: isSelected ? '2px solid var(--blue)' : '2px solid transparent',
                      }}
                      onClick={() => {
                        if (entry.type === 'directory') {
                          navigateTo(resolvedPath === '/' ? `/${entry.name}` : `${resolvedPath}/${entry.name}`)
                        } else {
                          handleFileClick(entry)
                        }
                      }}
                      onMouseEnter={(e) => { if (!isSelected) e.currentTarget.style.background = 'var(--bg-surface)' }}
                      onMouseLeave={(e) => { if (!isSelected) e.currentTarget.style.background = isSelected ? 'var(--bg-elevated)' : 'transparent' }}
                    >
                      {/* Icon */}
                      <span style={{ flexShrink: 0 }}>{getEntryIcon(entry)}</span>

                      {/* Name */}
                      <span className="flex-1 truncate text-sm" style={{ color: 'var(--text-primary)', minWidth: 0 }}>
                        {entry.name}
                      </span>

                      {/* Size */}
                      <span className="text-xs" style={{ width: 80, flexShrink: 0, textAlign: 'right', color: 'var(--text-dim)' }}>
                        {entry.type === 'file' ? formatSize(entry.size) : ''}
                      </span>

                      {/* Modified */}
                      <span className="text-xs" style={{ width: 140, flexShrink: 0, textAlign: 'right', color: 'var(--text-dim)' }}>
                        {formatDate(entry.modified)}
                      </span>
                    </div>
                  )
                })}
              </>
            )}
          </div>
        </div>

        {/* Resize handle + Preview pane */}
        {hasPreview && (
          <>
            <ResizeHandle width={fileListWidth} onResize={handleListResize} />
            <div className="flex flex-col flex-1" style={{ minWidth: 0, minHeight: 0, overflow: 'hidden' }}>
              {/* Preview header */}
              <div
                className="flex items-center gap-2 px-3 py-2 flex-shrink-0"
                style={{ borderBottom: '1px solid var(--border-subtle)' }}
              >
                <span className="flex-1 truncate" style={{ color: 'var(--text-primary)', fontSize: 12, fontFamily: 'var(--font-mono)' }}>
                  {preview?.path || selectedFileName || ''}
                </span>
                {previewText != null && (
                  <button
                    className="flex items-center justify-center flex-shrink-0"
                    style={{
                      background: 'transparent', border: 'none', cursor: 'pointer',
                      color: 'var(--text-dim)', padding: 4, transition: 'color 150ms ease',
                    }}
                    onClick={handleAskPrivaFull}
                    onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--purple)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                    title={t('optimize.askForPriva')}
                  >
                    <Sparkles size={14} strokeWidth={1.5} />
                  </button>
                )}
                {preview?.path && <CopyPathBtn path={preview.path} />}
                {preview?.size != null && (
                  <span className="flex-shrink-0" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                    {formatSize(preview.size)}
                  </span>
                )}
                {preview && !preview.error && (
                  <button
                    className="flex-shrink-0"
                    style={{
                      background: 'transparent', border: 'none', cursor: 'pointer',
                      color: 'var(--text-dim)', padding: 4, transition: 'color 150ms ease',
                    }}
                    onClick={() => handleDownload({ name: preview.name })}
                    onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--blue)' }}
                    onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
                    title={t('settings.fileManagerDownload')}
                  >
                    <Download size={14} strokeWidth={1.5} />
                  </button>
                )}
              </div>

              {/* Preview content */}
              <div ref={previewContentRef} className="flex-1 min-w-0 min-h-0 overflow-hidden" style={{ minHeight: 0 }}>
                {preview?.error ? (
                  <div style={{ color: 'var(--red)', fontSize: 13 }}>{preview.error}</div>
                ) : preview?.path ? (
                  <FilePreviewRenderer
                    file={preview}
                    cacheKey={previewCacheKey}
                    loadText={loadPreviewText}
                    loadArrayBuffer={loadPreviewArrayBuffer}
                    loadBlob={loadPreviewBlob}
                    onTextLoaded={setPreviewText}
                  />
                ) : (
                  <div className="flex flex-col items-center justify-center" style={{ color: 'var(--text-dim)', fontSize: 12, height: '100%' }}>
                    <FileText size={28} strokeWidth={1.5} style={{ color: 'var(--text-dim)', marginBottom: 8 }} />
                    <span>{t('settings.fileManagerSelectFile', 'Select a file to preview')}</span>
                  </div>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      {/* "Ask for Priva" tooltip — portaled to body */}
      {tooltip && previewText != null && createPortal(
        <button
          className="flex items-center gap-1"
          onClick={handleAskPrivaClick}
          style={{
            position: 'fixed',
            left: tooltip.x,
            top: tooltip.y,
            zIndex: 9999,
            background: 'var(--bg-surface)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            padding: '4px 10px',
            cursor: 'pointer',
            fontSize: 12,
            color: 'var(--text-secondary)',
            whiteSpace: 'nowrap',
            transition: 'color 150ms ease, border-color 150ms ease',
          }}
          onMouseEnter={(e) => {
            e.currentTarget.style.color = 'var(--text-primary)'
            e.currentTarget.style.borderColor = 'var(--blue)'
          }}
          onMouseLeave={(e) => {
            e.currentTarget.style.color = 'var(--text-secondary)'
            e.currentTarget.style.borderColor = 'var(--border)'
          }}
        >
          <Sparkles size={14} strokeWidth={1.5} />
          {t('optimize.askForPriva')}
        </button>,
        document.body
      )}

      {/* Optimize popup — portaled to body */}
      {optimizeData && createPortal(
        <OptimizePopup
          data={optimizeData}
          onClose={() => setOptimizeData(null)}
        />,
        document.body
      )}

      <CreateDirectoryDialog
        open={createDirectoryOpen}
        parentPath={resolvedPath}
        onCreated={handleDirectoryCreated}
        onCancel={() => setCreateDirectoryOpen(false)}
      />
    </div>
  )
}

function CopyPathBtn({ path }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      className="flex-shrink-0"
      style={{
        background: 'transparent', border: 'none', cursor: 'pointer',
        color: copied ? 'var(--green)' : 'var(--text-dim)',
        padding: 4, transition: 'color 150ms ease',
      }}
      onClick={() => {
        copyTextToClipboard(path)
        setCopied(true)
        setTimeout(() => setCopied(false), 800)
      }}
      title={path}
    >
      {copied ? <DrawIcon name="check" size={14} strokeWidth={1.5} /> : <Copy size={14} strokeWidth={1.5} />}
    </button>
  )
}

function ResizeHandle({ width, onResize }) {
  const { dragging, onMouseDown } = useResizable({
    initial: width,
    min: 200,
    max: 800,
    direction: 'right',
    onResize,
  })

  return (
    <div
      onMouseDown={onMouseDown}
      style={{
        width: 4,
        flexShrink: 0,
        cursor: 'col-resize',
        background: dragging ? 'var(--blue)' : 'transparent',
        transition: 'background 100ms ease',
      }}
      onMouseEnter={(e) => {
        if (!dragging) e.currentTarget.style.background = 'var(--blue)'
      }}
      onMouseLeave={(e) => {
        if (!dragging) e.currentTarget.style.background = 'transparent'
      }}
    />
  )
}
