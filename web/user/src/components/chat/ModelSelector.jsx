import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { animate, eases } from 'animejs'
import { ChevronDown, ChevronRight, Cpu, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import usePopoverTransition from '@shared/motion/usePopoverTransition'
import useReducedMotion from '@shared/motion/useReducedMotion'
import { EASE_SPRING } from '@shared/motion/tokens'
import useSettingsStore from '../../stores/settingsStore'
import { MODEL_CONTEXT_1M } from '../../utils/modelSelection'

const MODEL_ROW_HEIGHT = 28
const MODEL_LIST_MAX_HEIGHT = 196
const SELECTOR_MAX_WIDTH = 220
const SELECTOR_WIDTH_BUFFER = 5
const MODEL_MENU_WIDTH = 200
const MARQUEE_MS_PER_PIXEL = 30

function ProfileSeparator() {
  return <span aria-hidden="true" style={{ display: 'inline-block', width: 3, height: 3, flexShrink: 0, margin: '0 5px', borderRadius: 2, background: 'currentColor', userSelect: 'none', pointerEvents: 'none' }} />
}

function AnimeMarqueeText({ children, className = '', style = {}, title, autoPlay = false, hoverable = false, active = false }) {
  const viewportRef = useRef(null)
  const contentRef = useRef(null)
  const animationRef = useRef(null)
  const overflowRef = useRef(false)
  const [overflows, setOverflows] = useState(false)
  const reducedMotion = useReducedMotion()

  useLayoutEffect(() => {
    const viewport = viewportRef.current
    const content = contentRef.current
    if (!viewport || !content) return undefined

    const measure = () => {
      animationRef.current?.cancel()
      content.style.transform = 'translateX(0px)'
      const next = content.scrollWidth > viewport.clientWidth + 1
      overflowRef.current = next
      setOverflows(next)
    }

    measure()
    if (typeof ResizeObserver === 'undefined') return undefined
    const observer = new ResizeObserver(measure)
    observer.observe(viewport)
    observer.observe(content)
    return () => {
      observer.disconnect()
      animationRef.current?.cancel()
      animationRef.current = null
    }
  }, [children])

  const startAnimation = () => {
    const viewport = viewportRef.current
    const content = contentRef.current
    if (!viewport || !content || !overflowRef.current || reducedMotion || (!autoPlay && !hoverable && !active)) return
    const distance = content.scrollWidth - viewport.clientWidth
    if (distance <= 0) return
    animationRef.current?.cancel()
    animationRef.current = animate(content, {
      translateX: -distance,
      duration: Math.max(1, distance * MARQUEE_MS_PER_PIXEL),
      delay: 350,
      ease: eases.linear,
    })
  }

  const stopAnimation = () => {
    if (autoPlay) return
    animationRef.current?.cancel()
    animationRef.current = null
    if (contentRef.current) contentRef.current.style.transform = 'translateX(0px)'
  }

  useEffect(() => {
    if (active) {
      startAnimation()
    } else if (!autoPlay) {
      stopAnimation()
    }
  }, [active, autoPlay, overflows, reducedMotion, children])

  useEffect(() => {
    if (!autoPlay || !overflows || reducedMotion) return undefined
    startAnimation()
    return () => {
      animationRef.current?.cancel()
      animationRef.current = null
      if (contentRef.current) contentRef.current.style.transform = 'translateX(0px)'
    }
  }, [autoPlay, overflows, reducedMotion, children])

  const canAnimate = autoPlay || hoverable || active

  return (
    <span
      ref={viewportRef}
      className={className}
      title={title}
      onMouseEnter={canAnimate ? startAnimation : undefined}
      onMouseLeave={canAnimate ? stopAnimation : undefined}
      onFocus={canAnimate ? startAnimation : undefined}
      onBlur={canAnimate ? stopAnimation : undefined}
      style={{ display: 'block', minWidth: 0, overflow: 'hidden', whiteSpace: 'nowrap', ...style }}
    >
      <span ref={contentRef} style={{ display: 'inline-block', whiteSpace: 'nowrap' }}>{children}</span>
    </span>
  )
}

export default function ModelSelector() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [level, setLevel] = useState('profiles')
  const [profileId, setProfileId] = useState(null)
  const [filter, setFilter] = useState('')
  const [selectorHovered, setSelectorHovered] = useState(false)
  const [contextButtonHovered, setContextButtonHovered] = useState(false)
  const dropdownRef = useRef(null)
  const profileFilterRef = useRef(null)
  const modelFilterRef = useRef(null)
  const submenuRef = useRef(null)
  const { mounted: menuMounted, popRef } = usePopoverTransition({ open, placement: 'top' })
  const reducedMotion = useReducedMotion()
  const profiles = useSettingsStore((s) => s.profiles)
  const defaultProfileId = useSettingsStore((s) => s.defaultProfileId)
  const modelsByProfile = useSettingsStore((s) => s.modelsByProfile)
  const fetchProfiles = useSettingsStore((s) => s.fetchProfiles)
  const fetchModelsForProfile = useSettingsStore((s) => s.fetchModelsForProfile)
  const selectedModel = useSettingsStore((s) => s.selectedModel)
  const selectedModelCapabilities = useSettingsStore((s) => s.selectedModelCapabilities)
  const setSelectedModel = useSettingsStore((s) => s.setSelectedModel)
  const setSelectedModelContext = useSettingsStore((s) => s.setSelectedModelContext)
  const profileCount = profiles.length

  useEffect(() => { if (!profiles.length) fetchProfiles() }, [profiles.length, fetchProfiles])
  useEffect(() => {
    const handler = (event) => {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target)) {
        setOpen(false)
        setLevel('profiles')
        setFilter('')
      }
    }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])
  useEffect(() => {
    if (!open) return undefined
    const timer = window.setTimeout(() => {
      const input = profileCount > 1 && level === 'models' ? modelFilterRef.current : profileFilterRef.current
      input?.focus()
    }, 100)
    return () => window.clearTimeout(timer)
  }, [open, level, profileCount])
  useLayoutEffect(() => {
    const pane = submenuRef.current
    if (!pane || !open || profileCount <= 1 || level !== 'models') return undefined
    const reposition = () => {
      const parent = pane.offsetParent
      const previousTransform = pane.style.transform
      pane.style.transform = 'translateX(0px)'
      const verticalPadding = 8
      if (parent) {
        const parentRect = parent.getBoundingClientRect()
        // The submenu grows upward from the primary menu's bottom edge. When
        // vertical space is tight, shrink its scrollable list instead of
        // shifting the whole pane and breaking the shared bottom baseline.
        const availableHeight = Math.max(0, Math.floor(parentRect.bottom - verticalPadding))
        pane.style.maxHeight = `${availableHeight}px`
        pane.style.top = 'auto'
        pane.style.bottom = '0px'
        const rect = pane.getBoundingClientRect()
        const horizontalGap = 4
        const viewportWidth = document.documentElement.clientWidth || window.innerWidth
        const defaultLeft = parentRect.width + horizontalGap
        const leftSideLeft = -rect.width - horizontalGap
        const rightSideLimit = viewportWidth - rect.width - parentRect.left
        const rightSideFits = defaultLeft <= rightSideLimit
        const nextLeft = rightSideFits ? defaultLeft : leftSideLeft
        pane.style.left = `${nextLeft}px`
      }
      pane.style.transform = previousTransform || 'translateX(0px)'
    }
    reposition()
    window.addEventListener('resize', reposition)
    window.addEventListener('scroll', reposition, true)
    let observer
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(reposition)
      observer.observe(pane)
    }
    let animation
    if (reducedMotion) {
      pane.style.opacity = '1'
      pane.style.transform = 'translateX(0px)'
    } else {
      pane.style.opacity = '0'
      pane.style.transform = 'translateX(-16px)'
      animation = animate(pane, {
        opacity: 1,
        translateX: '0px',
        duration: 200,
        ease: EASE_SPRING,
      })
    }
    return () => {
      window.removeEventListener('resize', reposition)
      window.removeEventListener('scroll', reposition, true)
      observer?.disconnect()
      animation?.cancel()
    }
  }, [level, open, profileId, profileCount, reducedMotion])

  const candidateSeparator = selectedModel?.indexOf(':') ?? -1
  const candidateProfileId = candidateSeparator >= 0
    ? selectedModel.slice(0, candidateSeparator)
    : null
  // Match the backend resolver: a colon is a Profile separator only when the
  // prefix names a configured Profile. Model ids such as ollama:llama3:8b
  // otherwise remain intact.
  const separator = candidateProfileId && profiles.some((item) => item.id === candidateProfileId)
    ? candidateSeparator
    : -1
  const effectiveDefaultProfileId = defaultProfileId || profiles[0]?.id || null
  const selectedParts = separator >= 0 ? [selectedModel.slice(0, separator), selectedModel.slice(separator + 1)] : [effectiveDefaultProfileId, selectedModel]
  const selectedProfile = profiles.find((item) => item.id === selectedParts[0])
  const displayProfile = selectedProfile || profiles.find((item) => item.id === effectiveDefaultProfileId)
  const displayProfileName = displayProfile?.label || displayProfile?.id || selectedParts[0] || 'profile'
  const displayModelName = selectedModel
    ? selectedParts[1]
    : (displayProfile?.default_model || 'model')
  const displayModel = `${displayProfileName}·${displayModelName}`
  const context1mEnabled = selectedModelCapabilities?.context === MODEL_CONTEXT_1M
  const context1mAvailable = Boolean(selectedModel || displayProfile?.default_model)
  const context1mLabel = t(context1mEnabled ? 'chat.disable1mContext' : 'chat.enable1mContext')

  useLayoutEffect(() => {
    const selector = dropdownRef.current
    const measure = selector?.querySelector('[data-selector-measure]')
    if (!selector || !measure) return undefined
    const measuredWidth = Math.ceil(measure.getBoundingClientRect().width)
    const targetWidth = measuredWidth < SELECTOR_MAX_WIDTH
      ? Math.min(SELECTOR_MAX_WIDTH, measuredWidth + SELECTOR_WIDTH_BUFFER)
      : SELECTOR_MAX_WIDTH
    const currentWidth = selector.getBoundingClientRect().width
    if (reducedMotion || Math.abs(currentWidth - targetWidth) < 1) {
      selector.style.width = `${targetWidth}px`
      return undefined
    }
    const animation = animate(selector, {
      width: `${targetWidth}px`,
      duration: 200,
      ease: EASE_SPRING,
    })
    return () => animation.cancel()
  }, [displayModelName, reducedMotion])

  const currentProfileId = selectedProfile?.id || effectiveDefaultProfileId
  const activeProfile = profiles.find((item) => item.id === profileId)
  const cache = activeProfile ? modelsByProfile[activeProfile.id] : null
  const models = cache?.models || []
  const filteredProfiles = useMemo(() => {
    const query = filter.trim().toLowerCase()
    return query ? profiles.filter((item) => `${item.label} ${item.id}`.toLowerCase().includes(query)) : profiles
  }, [profiles, filter])
  const filteredModels = useMemo(() => {
    const query = filter.trim().toLowerCase()
    return query ? models.filter((item) => item.id.toLowerCase().includes(query)) : models
  }, [models, filter])

  const isModelSelected = (profile, modelId) => {
    if (!selectedModel) return profile.id === effectiveDefaultProfileId && profile.default_model === modelId
    if (separator >= 0) return selectedParts[0] === profile.id && selectedParts[1] === modelId
    return profile.id === effectiveDefaultProfileId && selectedParts[1] === modelId
  }

  const openMenu = () => {
    const next = !open
    setOpen(next)
    setFilter('')
    if (next) {
      setProfileId(currentProfileId)
      if (profileCount === 1) {
        const only = profiles[0]
        setProfileId(only.id)
        setLevel('models')
        fetchModelsForProfile(only.id)
      } else setLevel('profiles')
    } else setLevel('profiles')
  }
  const chooseProfile = (id) => {
    setProfileId(id)
    setLevel('models')
    setFilter('')
    fetchModelsForProfile(id)
  }
  const chooseModel = (id) => {
    if (!activeProfile) return
    const value = activeProfile.id === effectiveDefaultProfileId && id === activeProfile.default_model
      ? null
      : (profileCount > 1 ? `${activeProfile.id}:${id}` : id)
    setSelectedModel(value)
    setOpen(false)
    setLevel('profiles')
    setFilter('')
  }
  const back = () => { if (profileCount > 1) { setLevel('profiles'); setFilter('') } }

  return (
    <div className="flex items-center gap-1 min-w-0" style={{ flex: '0 0 auto' }}>
      <span className="flex items-center" style={{ flex: '0 1 auto', minWidth: 0, maxWidth: '45%', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', color: 'var(--text-dim)', fontSize: 11 }} title={displayProfileName}><span className="truncate">{displayProfileName}</span><ProfileSeparator /></span>
      <div className="relative" ref={dropdownRef} style={{ flex: '0 0 auto', width: SELECTOR_MAX_WIDTH, minWidth: 0, maxWidth: SELECTOR_MAX_WIDTH }}>
        <button className="flex items-center gap-1 px-2 w-full" onClick={openMenu} onMouseEnter={() => setSelectorHovered(true)} onMouseLeave={() => setSelectorHovered(false)} onFocus={() => setSelectorHovered(true)} onBlur={() => setSelectorHovered(false)} title={displayModel} style={{ height: 28, width: '100%', maxWidth: '100%', minWidth: 0, boxSizing: 'border-box', background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer', color: selectedModel ? 'var(--cyan)' : 'var(--text-dim)', fontSize: 11, fontFamily: 'var(--font-code)', whiteSpace: 'nowrap', overflow: 'hidden', textAlign: 'left', justifyContent: 'flex-start' }}>
          <Cpu size={11} strokeWidth={1.5} style={{ flexShrink: 0 }} />
          <AnimeMarqueeText active={selectorHovered} title={displayModelName} style={{ flex: '1 1 auto', minWidth: 0, textAlign: 'left' }}>{displayModelName}</AnimeMarqueeText>
          <ChevronDown size={10} strokeWidth={1.5} style={{ flexShrink: 0 }} />
        </button>
        <span data-selector-measure aria-hidden="true" style={{ position: 'fixed', left: -10000, top: -10000, display: 'inline-flex', alignItems: 'center', gap: 4, width: 'max-content', height: 28, padding: '0 8px', boxSizing: 'border-box', visibility: 'hidden', whiteSpace: 'nowrap', fontSize: 11, fontFamily: 'var(--font-code)' }}><Cpu size={11} strokeWidth={1.5} /><span>{displayModelName}</span><ChevronDown size={10} strokeWidth={1.5} /></span>
        {menuMounted && <div ref={popRef} className="absolute flex flex-col" style={{ right: 0, bottom: '100%', marginBottom: 4, width: MODEL_MENU_WIDTH, minWidth: MODEL_MENU_WIDTH, maxWidth: MODEL_MENU_WIDTH, boxSizing: 'border-box', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, zIndex: 50, pointerEvents: open ? 'auto' : 'none' }} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); if (level === 'models' && profileCount > 1) back(); else setOpen(false) } }}>
        {profileCount === 1 ? <div>
          <div className="flex items-center gap-2 px-2 py-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <Search size={11} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            <input ref={modelFilterRef} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t('settings.filterModels')} className="flex-1 text-xs" style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontFamily: 'var(--font-code)', minWidth: 0 }} />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: `min(${MODEL_LIST_MAX_HEIGHT}px, calc(100vh - 56px))` }}>
            {cache?.loading ? <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.loadingModels')}</div> : filteredModels.length ? filteredModels.map((item) => { const active = isModelSelected(activeProfile, item.id); return <button key={item.id} type="button" onClick={() => chooseModel(item.id)} className="flex items-center w-full px-2 text-xs" style={{ height: MODEL_ROW_HEIGHT, background: active ? 'var(--bg-surface)' : 'transparent', border: 'none', borderLeft: active ? '2px solid var(--cyan)' : '2px solid transparent', color: active ? 'var(--text-primary)' : 'var(--text-secondary)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-code)', overflow: 'hidden' }}><AnimeMarqueeText hoverable={active} title={item.id} style={{ flex: '1 1 auto', minWidth: 0 }}>{item.id}</AnimeMarqueeText></button> }) : <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{cache?.loaded ? t('settings.noModelsAvailable') : t('settings.openToLoadModels')}</div>}
          </div>
        </div> : <div>
          <div className="flex items-center gap-2 px-2 py-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <Search size={11} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            <input ref={profileFilterRef} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t('settings.filterProfiles')} className="flex-1 text-xs" style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontFamily: 'var(--font-code)', minWidth: 0 }} />
          </div>
          <div className="overflow-y-auto" style={{ maxHeight: `min(${MODEL_LIST_MAX_HEIGHT}px, calc(100vh - 56px))` }}>
            {filteredProfiles.map((item) => <button key={item.id} type="button" onClick={() => chooseProfile(item.id)} onMouseEnter={(event) => { if (item.id !== profileId) event.currentTarget.style.background = 'var(--bg-surface)' }} onMouseLeave={(event) => { if (item.id !== profileId) event.currentTarget.style.background = 'transparent' }} className="flex items-center justify-between w-full px-2 text-xs" style={{ height: 34, paddingTop: 3, paddingBottom: 3, background: item.id === profileId ? 'var(--bg-surface)' : 'transparent', border: 'none', color: 'var(--text-primary)', textAlign: 'left', cursor: 'pointer', overflow: 'hidden' }}><span className="font-semibold truncate" style={{ flex: '1 1 auto', minWidth: 0, fontSize: 11 }}>{item.label}</span><span className="flex items-center gap-1" style={{ flexShrink: 0, color: 'var(--text-dim)', fontSize: 11 }}>{item.id === defaultProfileId && <span style={{ whiteSpace: 'nowrap', fontSize: 11 }}>{t('settings.defaultProfile')}</span>}<ChevronRight size={12} strokeWidth={1.5} /></span></button>)}
          </div>
        </div>}
        {profileCount > 1 && level === 'models' && activeProfile && <div ref={submenuRef} className="absolute flex flex-col" style={{ left: 'calc(100% + 4px)', top: 'auto', bottom: 0, width: MODEL_MENU_WIDTH, minWidth: MODEL_MENU_WIDTH, maxWidth: MODEL_MENU_WIDTH, maxHeight: 'calc(100vh - 16px)', boxSizing: 'border-box', overflow: 'hidden', background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, zIndex: 51 }}>
          <div className="flex items-center gap-2 px-2 py-2" style={{ borderBottom: '1px solid var(--border)' }}>
            <Search size={11} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            <input ref={modelFilterRef} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={t('settings.filterModels')} className="flex-1 text-xs" style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontFamily: 'var(--font-code)', minWidth: 0 }} />
          </div>
          <div className="mx-2 text-xs" style={{ paddingTop: 7, paddingBottom: 7, color: 'var(--text-primary)', borderBottom: '1px solid var(--border)', fontSize: 12 }}><span className="font-semibold truncate" style={{ display: 'block', fontSize: 12 }}>{activeProfile.label}</span></div>
          <div className="flex-1 overflow-y-auto" style={{ minHeight: 0, maxHeight: `min(${MODEL_LIST_MAX_HEIGHT}px, calc(100vh - 84px))` }}>
            {cache?.loading ? <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.loadingModels')}</div> : filteredModels.length ? filteredModels.map((item) => { const active = isModelSelected(activeProfile, item.id); return <button key={item.id} type="button" onClick={() => chooseModel(item.id)} onMouseEnter={(event) => { if (!active) event.currentTarget.style.background = 'var(--bg-surface)' }} onMouseLeave={(event) => { if (!active) event.currentTarget.style.background = 'transparent' }} className="flex items-center w-full px-2 text-xs" style={{ height: MODEL_ROW_HEIGHT, background: active ? 'var(--bg-surface)' : 'transparent', border: 'none', color: active ? 'var(--text-primary)' : 'var(--text-secondary)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-code)', overflow: 'hidden' }}><AnimeMarqueeText hoverable title={item.id} style={{ flex: '1 1 auto', minWidth: 0, textAlign: 'left' }}>{item.id}</AnimeMarqueeText></button> }) : <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{cache?.loaded ? t('settings.noModelsAvailable') : t('settings.openToLoadModels')}</div>}
          </div>
        </div>}
        </div>}
      </div>
      <button
        type="button"
        aria-label={context1mLabel}
        aria-pressed={context1mEnabled}
        title={context1mLabel}
        disabled={!context1mAvailable}
        onClick={() => setSelectedModelContext(context1mEnabled ? null : MODEL_CONTEXT_1M)}
        onMouseEnter={() => setContextButtonHovered(true)}
        onMouseLeave={() => setContextButtonHovered(false)}
        onFocus={() => setContextButtonHovered(true)}
        onBlur={() => setContextButtonHovered(false)}
        className="flex items-center justify-center flex-shrink-0"
        style={{
          width: 34,
          height: 28,
          boxSizing: 'border-box',
          background: context1mEnabled ? 'var(--text-primary)' : 'transparent',
          border: 'none',
          borderRadius: 4,
          color: context1mEnabled ? 'var(--text-inverse)' : (contextButtonHovered ? 'var(--text-secondary)' : 'var(--text-dim)'),
          cursor: context1mAvailable ? 'pointer' : 'not-allowed',
          fontFamily: 'var(--font-code)',
          fontSize: 11,
          fontWeight: 600,
          opacity: context1mAvailable ? 1 : 0.55,
          transition: 'color 150ms ease, border-color 150ms ease, background 150ms ease',
        }}
      >
        1M
      </button>
    </div>
  )
}
