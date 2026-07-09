import { useLayoutEffect, useMemo, useRef, useState, useId } from 'react'
import { animate } from 'animejs'
import { ChevronDown, Folder, FolderOpen, FileText, Search, BookOpen } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { useResizable } from '@shared/hooks/useResizable'
import { usePresence } from '@shared/motion/usePresence'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import { DUR_MIGRATION, EASE_ACCORDION } from '@shared/motion/tokens'
import useSkillHubStore from '../../stores/skillHubStore'

function filterTree(nodes, query) {
  if (!query) return nodes
  const lq = query.toLowerCase()
  const result = []
  for (const node of nodes) {
    if (node.type === 'directory') {
      const filteredChildren = node.children ? filterTree(node.children, query) : []
      if (node.name.toLowerCase().includes(lq) || filteredChildren.length > 0) {
        result.push({ ...node, children: filteredChildren.length > 0 ? filteredChildren : node.children })
      }
    } else {
      if (node.name.toLowerCase().includes(lq)) {
        result.push(node)
      }
    }
  }
  return result
}

function hasMatchingDescendant(nodes, lq) {
  for (const node of nodes) {
    if (node.name.toLowerCase().includes(lq)) return true
    if (node.type === 'directory' && node.children && hasMatchingDescendant(node.children, lq)) return true
  }
  return false
}

function collectExpandedPaths(nodes, query, parentPath = '') {
  if (!query) return new Set()
  const lq = query.toLowerCase()
  const paths = new Set()
  for (const node of nodes) {
    const path = parentPath ? `${parentPath}/${node.name}` : node.name
    if (node.type === 'directory' && node.children) {
      if (hasMatchingDescendant(node.children, lq)) paths.add(path)
      if (node.name.toLowerCase().includes(lq)) paths.add(path)
      const childPaths = collectExpandedPaths(node.children, query, path)
      for (const p of childPaths) paths.add(p)
    }
  }
  return paths
}

function AnimeTreeChevron({ open, children, style }) {
  const ref = useRef(null)
  const reducedMotion = useReducedMotion()
  const mountedRef = useRef(false)

  useLayoutEffect(() => {
    const el = ref.current
    if (!el) return
    const rotate = `${open ? 180 : 0}deg`
    if (!mountedRef.current || reducedMotion) {
      mountedRef.current = true
      el.style.transform = `rotate(${rotate})`
      return
    }
    animate(el, {
      rotate,
      duration: DUR_MIGRATION.chevron,
      ease: EASE_ACCORDION,
    })
  }, [open, reducedMotion])

  return (
    <span
      ref={ref}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        ...style,
      }}
    >
      {children}
    </span>
  )
}

function AnimeTreeCollapse({ open, id, children }) {
  const { mounted, onExited } = usePresence(open)
  const reducedMotion = useReducedMotion()
  const outerRef = useRef(null)
  const innerRef = useRef(null)
  const animRef = useRef(null)
  const enteredRef = useRef(open)
  const targetHeightRef = useRef(null)
  const openRef = useRef(open)
  const [height, setHeight] = useState(0)
  openRef.current = open

  useLayoutEffect(() => {
    if (!mounted) return undefined
    const inner = innerRef.current
    if (!inner) return undefined

    const measure = () => {
      const next = Math.ceil(inner.scrollHeight || inner.getBoundingClientRect().height || 0)
      setHeight((current) => (Math.abs(current - next) > 0.5 ? next : current))
    }
    measure()

    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(inner)
    return () => observer.disconnect()
  }, [mounted])

  useLayoutEffect(() => {
    if (!mounted) {
      enteredRef.current = false
      targetHeightRef.current = null
      animRef.current?.cancel()
      animRef.current = null
      return
    }

    const outer = outerRef.current
    if (!outer) {
      if (!open) onExited()
      return
    }

    const target = Math.ceil(height || innerRef.current?.scrollHeight || 0)

    if (reducedMotion) {
      animRef.current?.cancel()
      animRef.current = null
      if (open) {
        enteredRef.current = true
        targetHeightRef.current = target
        outer.style.height = 'auto'
        outer.style.opacity = '1'
      } else {
        onExited()
      }
      return
    }

    if (open) {
      if (target <= 0) {
        outer.style.height = '0px'
        outer.style.opacity = '0'
        return
      }
      if (enteredRef.current && (outer.style.height === 'auto' || outer.style.height === '')) {
        targetHeightRef.current = target
        outer.style.opacity = '1'
        return
      }
      if (enteredRef.current && Math.abs((targetHeightRef.current ?? -1) - target) < 0.5) return
      animRef.current?.cancel()
      if (!enteredRef.current) {
        outer.style.height = '0px'
        outer.style.opacity = '0'
      }
      enteredRef.current = true
      targetHeightRef.current = target
      animRef.current = animate(outer, {
        height: `${target}px`,
        opacity: 1,
        duration: DUR_MIGRATION.accordionModeB,
        ease: EASE_ACCORDION,
        onComplete: () => {
          if (openRef.current) {
            outer.style.height = 'auto'
            outer.style.opacity = '1'
          }
        },
      })
    } else {
      animRef.current?.cancel()
      const current = outer.offsetHeight
      outer.style.height = `${current}px`
      void outer.offsetHeight
      targetHeightRef.current = 0
      animRef.current = animate(outer, {
        height: '0px',
        opacity: 0,
        duration: DUR_MIGRATION.accordionModeB,
        ease: EASE_ACCORDION,
        onComplete: onExited,
      })
    }
  }, [open, mounted, height, reducedMotion, onExited])

  if (!mounted) return null
  return (
    <div
      id={id}
      ref={outerRef}
      style={{ overflow: 'hidden', contain: 'layout paint style' }}
    >
      <div ref={innerRef}>
        {typeof children === 'function' ? children() : children}
      </div>
    </div>
  )
}

export default function HubFileTree() {
  const { t } = useTranslation()
  const skillDetail = useSkillHubStore((s) => s.skillDetail)
  const detailLoading = useSkillHubStore((s) => s.detailLoading)
  const selectedSkill = useSkillHubStore((s) => s.selectedSkill)
  const selectedFile = useSkillHubStore((s) => s.selectedFile)
  const selectFile = useSkillHubStore((s) => s.selectFile)
  const fileTreeWidth = useSkillHubStore((s) => s.fileTreeWidth)
  const setFileTreeWidth = useSkillHubStore((s) => s.setFileTreeWidth)

  const [fileSearchQuery, setFileSearchQuery] = useState('')

  const { dragging, onMouseDown } = useResizable({
    initial: fileTreeWidth,
    min: 160,
    max: 400,
    direction: 'right',
    onResize: setFileTreeWidth,
  })

  const filteredTree = useMemo(() => {
    if (!skillDetail?.tree) return null
    if (!fileSearchQuery) return skillDetail.tree
    return filterTree(skillDetail.tree, fileSearchQuery)
  }, [skillDetail?.tree, fileSearchQuery])

  const forceExpandedPaths = useMemo(() => {
    if (!skillDetail?.tree || !fileSearchQuery) return new Set()
    return collectExpandedPaths(skillDetail.tree, fileSearchQuery)
  }, [skillDetail?.tree, fileSearchQuery])

  return (
    <div
      className="flex flex-col flex-shrink-0 overflow-hidden relative"
      style={{
        width: fileTreeWidth,
        background: 'var(--bg-surface)',
        borderRight: '1px solid var(--border)',
      }}
    >
      {/* File search */}
      <div className="px-3 py-2 flex-shrink-0" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
        <div
          className="flex items-center gap-1 px-2 py-1"
          style={{
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 4,
          }}
        >
          <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
          <input
            className="flex-1"
            style={{
              background: 'transparent', border: 'none', outline: 'none',
              color: 'var(--text-primary)', minWidth: 0, fontSize: 12,
            }}
            placeholder={t('skills.searchFiles')}
            value={fileSearchQuery}
            onChange={(e) => setFileSearchQuery(e.target.value)}
          />
        </div>
      </div>

      {/* Tree */}
      <div className="flex-1 overflow-y-auto py-1">
        {detailLoading ? (
          <div className="flex flex-col gap-1 px-3">
            {[1, 2, 3, 4].map((i) => (
              <div key={i} className="skeleton" style={{ height: 24, borderRadius: 2 }} />
            ))}
          </div>
        ) : filteredTree ? (
          filteredTree.length > 0 ? (
            <>
              <div
                className="flex items-center gap-1 py-1 pr-2"
                style={{
                  paddingLeft: 8,
                  color: 'var(--text-primary)',
                  fontSize: 14,
                  fontWeight: 600,
                }}
              >
                <BookOpen size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--blue)' }} />
                <span className="truncate">{skillDetail?.name || selectedSkill?.name}</span>
              </div>
              {filteredTree.map((node) => (
                <TreeNode
                  key={node.name}
                  node={node}
                  depth={1}
                  selectedFile={selectedFile}
                  onSelect={selectFile}
                  parentPath=""
                  forceExpandedPaths={forceExpandedPaths}
                />
              ))}
            </>
          ) : (
            <div className="px-3 py-4" style={{ color: 'var(--text-dim)', textAlign: 'center', fontSize: 12 }}>
              {t('skills.noSkills')}
            </div>
          )
        ) : null}
      </div>

      {/* Resize handle */}
      <div
        onMouseDown={onMouseDown}
        style={{
          position: 'absolute',
          right: 0,
          top: 0,
          bottom: 0,
          width: 4,
          cursor: 'col-resize',
          background: dragging ? 'var(--blue)' : 'transparent',
          transition: 'background 100ms ease',
          zIndex: 10,
        }}
        onMouseEnter={(e) => {
          if (!dragging) e.currentTarget.style.background = 'var(--blue)'
        }}
        onMouseLeave={(e) => {
          if (!dragging) e.currentTarget.style.background = 'transparent'
        }}
      />
    </div>
  )
}

function TreeNode({ node, depth, selectedFile, onSelect, parentPath, forceExpandedPaths }) {
  const path = parentPath ? `${parentPath}/${node.name}` : node.name
  const bodyId = useId()
  const isForceExpanded = forceExpandedPaths.has(path)
  const [manualExpanded, setManualExpanded] = useState(node.name === 'SKILL.md' || depth === 0)
  const expanded = isForceExpanded || manualExpanded
  const isActive = selectedFile === path

  if (node.type === 'directory') {
    return (
      <>
        <button
          type="button"
          aria-expanded={expanded}
          aria-controls={bodyId}
          className="flex items-center gap-1 py-1 pr-2"
          style={{
            paddingLeft: 8 + depth * 16,
            cursor: 'pointer',
            color: 'var(--text-secondary)',
            fontSize: 14,
            transition: 'background 150ms ease',
            background: 'transparent',
            border: 'none',
            width: '100%',
            textAlign: 'left',
          }}
          onClick={() => setManualExpanded(!expanded)}
          onMouseEnter={(e) => { e.currentTarget.style.background = 'var(--bg-elevated)' }}
          onMouseLeave={(e) => { e.currentTarget.style.background = 'transparent' }}
        >
          <AnimeTreeChevron open={expanded}>
            <ChevronDown size={12} strokeWidth={1.5} />
          </AnimeTreeChevron>
          {expanded
            ? <FolderOpen size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--yellow)' }} />
            : <Folder size={12} strokeWidth={1.5} style={{ flexShrink: 0, color: 'var(--yellow)' }} />}
          <span className="truncate">{node.name}</span>
        </button>
        <AnimeTreeCollapse open={expanded} id={bodyId}>
          {() => node.children?.map((child) => (
            <TreeNode
              key={child.name}
              node={child}
              depth={depth + 1}
              selectedFile={selectedFile}
              onSelect={onSelect}
              parentPath={path}
              forceExpandedPaths={forceExpandedPaths}
            />
          ))}
        </AnimeTreeCollapse>
      </>
    )
  }

  return (
    <div
      className="flex items-center gap-1 py-1 pr-2"
      style={{
        paddingLeft: 8 + depth * 16 + 14,
        cursor: 'pointer',
        background: isActive ? 'var(--bg-elevated)' : 'transparent',
        color: isActive ? 'var(--blue)' : 'var(--text-secondary)',
        fontSize: 14,
        transition: 'background 150ms ease, color 150ms ease',
      }}
      onClick={() => onSelect(path)}
      onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
    >
      <FileText size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />
      <span className="truncate">{node.name}</span>
    </div>
  )
}
