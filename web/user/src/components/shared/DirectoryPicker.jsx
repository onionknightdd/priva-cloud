import { useState, useEffect, useCallback } from 'react'
import { createPortal } from 'react-dom'
import {
  X, Folder, FolderOpen, ChevronRight, Check, CornerDownLeft,
} from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { listDirectory } from '../../api/userFiles'
import useOverlayTransition from '@shared/motion/useOverlayTransition'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'

// Center-modal directory picker. Lazy-loads directories (dirs only) from the
// agent-runner FS via listDirectory. Two modes:
//   single (cwd)     -> onConfirm(path)
//   multiple (add_dirs) -> onConfirm([path, ...])
// Browses anywhere on the pod FS (rooted at '/'), opens expanded to initialPath,
// shows hidden dot-dirs, and accepts a typed/pasted absolute path.

function chainTo(path) {
  const parts = String(path || '/').split('/').filter(Boolean)
  const chain = ['/']
  let acc = ''
  for (const p of parts) { acc += '/' + p; chain.push(acc) }
  return chain
}

export default function DirectoryPicker({
  open,
  title,
  multiple = false,
  initialPath = '/',
  initialSelected = null,
  onConfirm,
  onCancel,
}) {
  const { t } = useTranslation()
  const [cache, setCache] = useState({})       // path -> { loading } | { dirs:[{name,path}] } | { error }
  const [expanded, setExpanded] = useState({}) // path -> bool
  const [single, setSingle] = useState(null)
  const [multi, setMulti] = useState(new Set())
  const [pathInput, setPathInput] = useState('')
  const [pathError, setPathError] = useState(null)

  const loadDir = useCallback((path) => {
    setCache((c) => (c[path]?.dirs || c[path]?.loading ? c : { ...c, [path]: { loading: true } }))
    listDirectory(path).then((data) => {
      const base = data.path || path
      const sep = base.endsWith('/') ? '' : '/'
      const dirs = (data.entries || [])
        .filter((e) => e.type === 'directory')
        .map((e) => ({ name: e.name, path: `${base}${sep}${e.name}` }))
      setCache((c) => ({ ...c, [path]: { dirs } }))
    }).catch((e) => {
      setCache((c) => ({ ...c, [path]: { error: String(e?.message || e) } }))
    })
  }, [])

  // On open: expand the chain from '/' to initialPath and load each level.
  useEffect(() => {
    if (!open) return
    const start = initialPath || '/'
    const chain = chainTo(start)
    setExpanded(Object.fromEntries(chain.map((p) => [p, true])))
    chain.forEach(loadDir)
    setSingle(multiple ? null : start)
    setMulti(new Set(multiple && Array.isArray(initialSelected) ? initialSelected : []))
    setPathInput('')
    setPathError(null)
  }, [open, initialPath, initialSelected, multiple, loadDir])

  const toggleExpand = (path) => {
    setExpanded((e) => {
      const next = !e[path]
      if (next) loadDir(path)
      return { ...e, [path]: next }
    })
  }

  const choose = (path) => {
    if (multiple) {
      setMulti((s) => {
        const n = new Set(s)
        if (n.has(path)) n.delete(path); else n.add(path)
        return n
      })
    } else {
      setSingle(path)
    }
  }

  const submitPath = () => {
    const p = pathInput.trim()
    if (!p) return
    listDirectory(p).then((data) => {
      const real = data.path || p
      const chain = chainTo(real)
      setExpanded((e) => ({ ...e, ...Object.fromEntries(chain.map((c) => [c, true])) }))
      chain.forEach(loadDir)
      choose(real)
      setPathError(null)
      setPathInput('')
    }).catch((e) => setPathError(String(e?.message || e)))
  }

  const confirm = () => {
    if (multiple) onConfirm(Array.from(multi))
    else if (single) onConfirm(single)
  }

  const { mounted, panelRef, backdropRef } = useOverlayTransition({ open, variant: 'scale' })
  if (!mounted) return null

  const canConfirm = multiple ? true : !!single

  const renderNode = (path, name, depth) => {
    const node = cache[path]
    const isOpen = !!expanded[path]
    const isSel = multiple ? multi.has(path) : single === path
    return (
      <div key={path}>
        <div
          className="flex items-center gap-1 min-w-0"
          style={{
            padding: `4px 8px 4px ${8 + depth * 14}px`,
            background: isSel ? 'var(--bg-surface)' : 'transparent',
            borderLeft: isSel ? '2px solid var(--cyan)' : '2px solid transparent',
            transition: 'background 150ms ease',
          }}
          onMouseEnter={(e) => { if (!isSel) e.currentTarget.style.background = 'var(--bg-surface)' }}
          onMouseLeave={(e) => { if (!isSel) e.currentTarget.style.background = 'transparent' }}
        >
          <button
            type="button"
            onClick={() => toggleExpand(path)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 0, display: 'flex', flexShrink: 0 }}
          >
            <AnimatedChevron open={isOpen}>
              <ChevronRight size={12} strokeWidth={1.5} />
            </AnimatedChevron>
          </button>
          {multiple && (
            <button
              type="button"
              onClick={() => choose(path)}
              style={{
                width: 14, height: 14, flexShrink: 0, padding: 0, cursor: 'pointer',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
                background: isSel ? 'var(--cyan)' : 'transparent',
                border: `1px solid ${isSel ? 'var(--cyan)' : 'var(--border-strong)'}`,
                borderRadius: 2,
              }}
            >
              {isSel && <Check size={10} strokeWidth={2} style={{ color: 'var(--text-inverse)' }} />}
            </button>
          )}
          <button
            type="button"
            className="flex items-center gap-1 flex-1 min-w-0"
            onClick={() => choose(path)}
            style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 0, textAlign: 'left' }}
          >
            {isOpen
              ? <FolderOpen size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />
              : <Folder size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--text-dim)' }} />}
            <span className="truncate" style={{ fontSize: 12, color: isSel ? 'var(--text-primary)' : 'var(--text-secondary)', minWidth: 0 }}>
              {name}
            </span>
          </button>
        </div>
        <AnimatedCollapse
          open={isOpen}
          heightDuration={220}
          opacityDuration={180}
          animateContentResize
          resizeDuration={220}
        >
          <div>
            {node?.loading && (
              <div style={{ padding: `3px 8px 3px ${8 + (depth + 1) * 14}px`, color: 'var(--text-dim)', fontSize: 11 }}>…</div>
            )}
            {node?.error && (
              <div style={{ padding: `3px 8px 3px ${8 + (depth + 1) * 14}px`, color: 'var(--red)', fontSize: 11 }}>{node.error}</div>
            )}
            {node?.dirs?.map((d) => renderNode(d.path, d.name, depth + 1))}
          </div>
        </AnimatedCollapse>
      </div>
    )
  }

  const modal = (
    <div
      ref={backdropRef}
      className="fixed inset-0 flex items-center justify-center"
      style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 1000, pointerEvents: open ? 'auto' : 'none' }}
      onClick={onCancel}
    >
      <div
        ref={panelRef}
        className="flex flex-col"
        onClick={(e) => e.stopPropagation()}
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-strong)',
          borderRadius: 4,
          width: 'min(560px, 92vw)',
          height: '80vh',
        }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3" style={{ borderBottom: '1px solid var(--border)' }}>
          <span className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14 }}>
            {title || t('picker.title')}
          </span>
          <button
            type="button"
            onClick={onCancel}
            style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', display: 'flex', transition: 'color 150ms ease' }}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>

        {/* Path input */}
        <div className="px-4 py-2" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
          <div className="flex items-center gap-2" style={{ background: 'var(--bg-elevated)', border: `1px solid ${pathError ? 'var(--red)' : 'var(--border)'}`, borderRadius: 4, padding: '4px 8px' }}>
            <input
              type="text"
              value={pathInput}
              onChange={(e) => { setPathInput(e.target.value); setPathError(null) }}
              onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submitPath() } }}
              placeholder={t('picker.pathPlaceholder')}
              style={{
                flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none',
                color: 'var(--text-primary)', fontSize: 12,
                fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
              }}
            />
            <button
              type="button"
              onClick={submitPath}
              style={{ background: 'none', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', display: 'flex', flexShrink: 0, transition: 'color 150ms ease' }}
              onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--text-secondary)' }}
              onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
              title={t('picker.go')}
            >
              <CornerDownLeft size={13} strokeWidth={1.5} />
            </button>
          </div>
          {pathError && <div style={{ color: 'var(--red)', fontSize: 11, marginTop: 4 }}>{pathError}</div>}
        </div>

        {/* Tree */}
        <div className="flex-1 overflow-y-auto py-1" style={{ minHeight: 200 }}>
          {renderNode('/', '/', 0)}
        </div>

        {/* Footer */}
        <div className="flex items-center gap-3 px-4 py-3" style={{ borderTop: '1px solid var(--border)' }}>
          <span
            className="flex-1 truncate"
            style={{ color: 'var(--text-secondary)', fontSize: 12, fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace", minWidth: 0 }}
            title={multiple ? Array.from(multi).join('\n') : (single || '')}
          >
            {multiple ? t('picker.selectedCount', { count: multi.size }) : (single || '—')}
          </span>
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1"
            style={{ background: 'transparent', border: '1px solid var(--border)', borderRadius: 4, color: 'var(--text-secondary)', cursor: 'pointer', fontSize: 12, flexShrink: 0 }}
          >
            {t('picker.cancel')}
          </button>
          <button
            type="button"
            onClick={confirm}
            disabled={!canConfirm}
            className="px-3 py-1"
            style={{
              background: canConfirm ? 'var(--blue)' : 'var(--bg-elevated)',
              border: 'none', borderRadius: 4,
              color: canConfirm ? 'var(--text-inverse)' : 'var(--text-dim)',
              cursor: canConfirm ? 'pointer' : 'not-allowed',
              fontSize: 12, fontWeight: 600, flexShrink: 0,
            }}
          >
            {t('picker.use')}
          </button>
        </div>
      </div>
    </div>
  )

  return typeof document === 'undefined' ? modal : createPortal(modal, document.body)
}
