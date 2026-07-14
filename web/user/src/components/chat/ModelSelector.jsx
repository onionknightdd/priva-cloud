import { useState, useRef, useEffect, useMemo, useCallback } from 'react'
import { ChevronDown, Cpu, Search } from 'lucide-react'
import usePopoverTransition from '@shared/motion/usePopoverTransition'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import useSettingsStore from '../../stores/settingsStore'

const MODEL_ROW_HEIGHT = 28
const MODEL_LIST_MAX_HEIGHT = 196
const MODEL_LIST_OVERSCAN = 3

export default function ModelSelector() {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const [scrollTop, setScrollTop] = useState(0)
  const hasEnv = useSettingsStore((s) => s.hasEnv)
  const models = useSettingsStore((s) => s.models)
  const modelsLoading = useSettingsStore((s) => s.modelsLoading)
  const modelsLoaded = useSettingsStore((s) => s.modelsLoaded)
  const selectedModel = useSettingsStore((s) => s.selectedModel)
  const setSelectedModel = useSettingsStore((s) => s.setSelectedModel)
  const env = useSettingsStore((s) => s.env)
  const defaultModelFromBootstrap = useSettingsStore((s) => s.defaultModel)
  const dropdownRef = useRef(null)
  const filterRef = useRef(null)
  const listRef = useRef(null)
  const openFetchRafRef = useRef(null)
  // Canonical popover envelope (M7): opacity + 4px rise, 200ms both ways.
  const { mounted: menuMounted, popRef } = usePopoverTransition({ open, placement: 'top' })

  // Default model from env
  const defaultModel = env?.ANTHROPIC_MODEL || defaultModelFromBootstrap || null
  const displayModel = selectedModel || defaultModel || 'model'

  const filteredModels = useMemo(() => {
    if (!filter.trim()) return models
    const q = filter.toLowerCase()
    return models.filter((m) => m.id.toLowerCase().includes(q))
  }, [models, filter])
  const modelListHeight = filteredModels.length > 0
    ? Math.min(MODEL_LIST_MAX_HEIGHT, filteredModels.length * MODEL_ROW_HEIGHT)
    : 0
  const visibleModelRange = useMemo(() => {
    if (!filteredModels.length) return { start: 0, end: 0 }
    const start = Math.max(0, Math.floor(scrollTop / MODEL_ROW_HEIGHT) - MODEL_LIST_OVERSCAN)
    const visibleCount = Math.ceil(MODEL_LIST_MAX_HEIGHT / MODEL_ROW_HEIGHT) + MODEL_LIST_OVERSCAN * 2
    const end = Math.min(filteredModels.length, start + visibleCount)
    return { start, end }
  }, [filteredModels.length, scrollTop])
  const visibleModels = useMemo(
    () => filteredModels.slice(visibleModelRange.start, visibleModelRange.end),
    [filteredModels, visibleModelRange]
  )
  const shouldLoadModels = hasEnv !== false && !modelsLoaded && models.length === 0
  const showModelsLoading = modelsLoading || shouldLoadModels
  const showSearch = !showModelsLoading && models.length > 0

  const prefetchModels = useCallback(() => {
    const state = useSettingsStore.getState()
    if (state.hasEnv === false || state.modelsLoaded || state.modelsLoading || state.models.length > 0) return
    state.fetchModels()
  }, [])

  useEffect(() => {
    if (hasEnv === false || modelsLoaded || modelsLoading || models.length > 0) return undefined
    const run = () => prefetchModels()
    if (typeof window.requestIdleCallback === 'function') {
      const id = window.requestIdleCallback(run, { timeout: 1200 })
      return () => window.cancelIdleCallback(id)
    }
    const timer = window.setTimeout(run, 700)
    return () => window.clearTimeout(timer)
  }, [hasEnv, models.length, modelsLoaded, modelsLoading, prefetchModels])

  useEffect(() => () => {
    if (openFetchRafRef.current) cancelAnimationFrame(openFetchRafRef.current)
  }, [])

  useEffect(() => {
    const handler = (e) => {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target)) {
        setOpen(false)
        setFilter('')
      }
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  // Focus after the popover's first paint/enter animation so opening the menu
  // does not spend its first frame on input focus layout work.
  useEffect(() => {
    if (!open) return undefined
    const timer = window.setTimeout(() => { filterRef.current?.focus() }, 180)
    return () => window.clearTimeout(timer)
  }, [open])

  useEffect(() => {
    setScrollTop(0)
    if (listRef.current) listRef.current.scrollTop = 0
  }, [filter, open])

  const handleOpen = () => {
    const nextOpen = !open
    if (open) setFilter('')
    setOpen(nextOpen)
    if (nextOpen && shouldLoadModels) {
      if (openFetchRafRef.current) cancelAnimationFrame(openFetchRafRef.current)
      openFetchRafRef.current = requestAnimationFrame(() => {
        openFetchRafRef.current = null
        prefetchModels()
      })
    }
  }

  const handleSelect = (modelId) => {
    // If selecting the default model, clear the override
    setSelectedModel(modelId === defaultModel ? null : modelId)
    setOpen(false)
    setFilter('')
  }

  return (
    <div
      className="relative"
      ref={dropdownRef}
      style={{ flex: '0 1 auto', minWidth: 0, maxWidth: 'min(50vw, 420px)' }}
    >
      <button
        className="flex items-center gap-1 px-2"
        style={{
          height: 26,
          width: 'fit-content',
          maxWidth: '100%',
          minWidth: 0,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          cursor: 'pointer',
          color: selectedModel ? 'var(--cyan)' : 'var(--text-dim)',
          fontSize: 11,
          fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          transition: 'color 150ms ease, border-color 150ms ease',
          whiteSpace: 'nowrap',
          overflow: 'hidden',
        }}
        onClick={handleOpen}
        onFocus={prefetchModels}
        onMouseEnter={(e) => {
          prefetchModels()
          e.currentTarget.style.borderColor = 'var(--border-strong)'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = 'var(--border)'
          e.currentTarget.style.color = selectedModel ? 'var(--cyan)' : 'var(--text-dim)'
        }}
        title={displayModel}
      >
        <Cpu size={11} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        <span className="truncate" style={{ minWidth: 0 }}>{displayModel}</span>
        <ChevronDown size={9} strokeWidth={1.5} style={{ flexShrink: 0 }} />
      </button>

      {menuMounted && (
        <div
          ref={popRef}
          className="absolute right-0 flex flex-col"
          style={{
            bottom: '100%',
            marginBottom: 4,
            background: 'var(--bg-elevated)',
            border: '1px solid var(--border)',
            borderRadius: 4,
            minWidth: 180,
            maxWidth: 276,
            maxHeight: 228,
            zIndex: 50,
            pointerEvents: open ? 'auto' : 'none',
            contain: 'layout paint style',
            willChange: 'transform, opacity',
          }}
        >
          {/* Search filter */}
          <AnimatedCollapse
            open={showSearch}
            heightDuration={180}
            opacityDuration={160}
            animateContentResize
            resizeDuration={180}
          >
            <div
              className="flex items-center gap-2 px-2 py-2 flex-shrink-0"
              style={{ borderBottom: '1px solid var(--border)' }}
            >
              <Search size={11} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
              <input
                ref={filterRef}
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter models..."
                className="flex-1 text-xs"
                style={{
                  background: 'transparent',
                  border: 'none',
                  outline: 'none',
                  color: 'var(--text-primary)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 11,
                  minWidth: 0,
                }}
              />
            </div>
          </AnimatedCollapse>

          {/* Model list */}
          <AnimatedCollapse
            open
            animateContentResize
            resizeDuration={220}
            heightDuration={220}
            opacityDuration={160}
          >
            <div
              ref={listRef}
              className="overflow-y-auto"
              style={{ height: modelListHeight || 'auto', maxHeight: MODEL_LIST_MAX_HEIGHT }}
              onScroll={(e) => { setScrollTop(e.currentTarget.scrollTop) }}
            >
              {showModelsLoading ? (
                <div className="px-2 py-1 text-xs" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                  Loading models...
                </div>
              ) : modelsLoaded && models.length === 0 ? (
                <div className="px-2 py-1 text-xs" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                  No models available
                </div>
              ) : filteredModels.length === 0 ? (
                <div className="px-2 py-1 text-xs" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
                  No matches
                </div>
              ) : (
                <div style={{ height: filteredModels.length * MODEL_ROW_HEIGHT, position: 'relative' }}>
                  <div
                    style={{
                      position: 'absolute',
                      top: visibleModelRange.start * MODEL_ROW_HEIGHT,
                      left: 0,
                      right: 0,
                    }}
                  >
                    {visibleModels.map((m) => {
                      const isActive = (selectedModel || defaultModel) === m.id
                      return (
                        <button
                          key={m.id}
                          className="flex items-center w-full px-2 text-xs"
                          title={m.id}
                          style={{
                            height: MODEL_ROW_HEIGHT,
                            boxSizing: 'border-box',
                            background: isActive ? 'var(--bg-surface)' : 'transparent',
                            border: 'none',
                            borderLeft: isActive ? '2px solid var(--cyan)' : '2px solid transparent',
                            color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                            cursor: 'pointer',
                            fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                            fontSize: 11,
                            textAlign: 'left',
                            transition: 'background 150ms ease',
                            whiteSpace: 'nowrap',
                            overflow: 'hidden',
                            textOverflow: 'ellipsis',
                          }}
                          onClick={() => handleSelect(m.id)}
                          onMouseEnter={(e) => {
                            if (!isActive) e.currentTarget.style.background = 'var(--bg-surface)'
                          }}
                          onMouseLeave={(e) => {
                            if (!isActive) e.currentTarget.style.background = 'transparent'
                          }}
                        >
                          {m.id}
                        </button>
                      )
                    })}
                  </div>
                </div>
              )}
            </div>
          </AnimatedCollapse>
        </div>
      )}
    </div>
  )
}
