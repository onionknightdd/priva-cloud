import { useEffect, useMemo, useRef, useState } from 'react'
import { ChevronDown, ChevronLeft, ChevronRight, Cpu, Search } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import usePopoverTransition from '@shared/motion/usePopoverTransition'
import useSettingsStore from '../../stores/settingsStore'

const MODEL_ROW_HEIGHT = 28
const MODEL_LIST_MAX_HEIGHT = 196

export default function ModelSelector() {
  const { t } = useTranslation()
  const [open, setOpen] = useState(false)
  const [level, setLevel] = useState('profiles')
  const [profileId, setProfileId] = useState(null)
  const [filter, setFilter] = useState('')
  const dropdownRef = useRef(null)
  const filterRef = useRef(null)
  const { mounted: menuMounted, popRef } = usePopoverTransition({ open, placement: 'top' })
  const profiles = useSettingsStore((s) => s.profiles)
  const defaultProfileId = useSettingsStore((s) => s.defaultProfileId)
  const modelsByProfile = useSettingsStore((s) => s.modelsByProfile)
  const fetchProfiles = useSettingsStore((s) => s.fetchProfiles)
  const fetchModelsForProfile = useSettingsStore((s) => s.fetchModelsForProfile)
  const selectedModel = useSettingsStore((s) => s.selectedModel)
  const setSelectedModel = useSettingsStore((s) => s.setSelectedModel)
  const profileCount = profiles.length

  useEffect(() => { if (!profiles.length) fetchProfiles() }, [profiles.length, fetchProfiles])
  useEffect(() => {
    const handler = (event) => { if (dropdownRef.current && !dropdownRef.current.contains(event.target)) { setOpen(false); setFilter('') } }
    if (open) document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])
  useEffect(() => { if (open) window.setTimeout(() => filterRef.current?.focus(), 100) }, [open, level])

  const separator = selectedModel?.indexOf(':') ?? -1
  const selectedParts = separator >= 0 ? [selectedModel.slice(0, separator), selectedModel.slice(separator + 1)] : [defaultProfileId, selectedModel]
  const selectedProfile = profiles.find((item) => item.id === selectedParts[0])
  const displayModel = selectedModel
    ? (selectedProfile ? `${selectedProfile.label} / ${selectedParts[1]}` : selectedModel)
    : (selectedProfile?.default_model || profiles.find((item) => item.id === defaultProfileId)?.default_model || 'model')
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

  const openMenu = () => {
    const next = !open
    setOpen(next)
    setFilter('')
    if (next) {
      if (profileCount === 1) {
        const only = profiles[0]
        setProfileId(only.id)
        setLevel('models')
        fetchModelsForProfile(only.id)
      } else setLevel('profiles')
    }
  }
  const chooseProfile = (id) => {
    setProfileId(id)
    setLevel('models')
    setFilter('')
    fetchModelsForProfile(id)
  }
  const chooseModel = (id) => {
    if (!activeProfile) return
    const value = activeProfile.id === defaultProfileId && id === activeProfile.default_model
      ? null
      : (profileCount > 1 ? `${activeProfile.id}:${id}` : id)
    setSelectedModel(value)
    setOpen(false)
    setFilter('')
  }
  const back = () => { if (profileCount > 1) { setLevel('profiles'); setFilter('') } }

  return (
    <div className="relative" ref={dropdownRef} style={{ flex: '0 1 auto', minWidth: 0, maxWidth: 'min(55vw, 440px)' }}>
      <button className="flex items-center gap-1 px-2" onClick={openMenu} title={displayModel} style={{ height: 28, maxWidth: '100%', minWidth: 0, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, cursor: 'pointer', color: selectedModel ? 'var(--cyan)' : 'var(--text-dim)', fontSize: 11, fontFamily: 'var(--font-code)', whiteSpace: 'nowrap', overflow: 'hidden' }}>
        <Cpu size={11} strokeWidth={1.5} style={{ flexShrink: 0 }} /><span className="truncate" style={{ minWidth: 0 }}>{displayModel}</span><ChevronDown size={10} strokeWidth={1.5} />
      </button>
      {menuMounted && <div ref={popRef} className="absolute right-0 flex flex-col" style={{ bottom: '100%', marginBottom: 4, minWidth: 220, maxWidth: 320, background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, zIndex: 50, pointerEvents: open ? 'auto' : 'none' }} onKeyDown={(event) => { if (event.key === 'Escape') { event.stopPropagation(); if (level === 'models' && profileCount > 1) back(); else setOpen(false) } }}>
        <div className="flex items-center gap-2 px-2 py-2" style={{ borderBottom: '1px solid var(--border)' }}>
          {level === 'models' && profileCount > 1 && <button type="button" onClick={back} style={{ display: 'inline-flex', background: 'transparent', border: 'none', color: 'var(--text-secondary)', cursor: 'pointer', padding: 0 }}><ChevronLeft size={14} strokeWidth={1.5} /></button>}
          <Search size={11} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
          <input ref={filterRef} value={filter} onChange={(event) => setFilter(event.target.value)} placeholder={level === 'profiles' ? t('settings.filterProfiles') : t('settings.filterModels')} className="flex-1 text-xs" style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontFamily: 'var(--font-code)', minWidth: 0 }} />
        </div>
        {level === 'profiles' ? <div className="overflow-y-auto" style={{ maxHeight: MODEL_LIST_MAX_HEIGHT }}>
          {filteredProfiles.map((item) => <button key={item.id} type="button" onClick={() => chooseProfile(item.id)} className="flex items-center justify-between w-full px-2 text-xs" style={{ height: 34, paddingTop: 3, paddingBottom: 3, background: item.id === defaultProfileId ? 'var(--bg-surface)' : 'transparent', border: 'none', borderLeft: item.id === defaultProfileId ? '2px solid var(--cyan)' : '2px solid transparent', color: 'var(--text-primary)', textAlign: 'left', cursor: 'pointer' }}><span className="truncate" style={{ fontSize: 13 }}><span className="font-semibold">{item.label}</span><span style={{ color: 'var(--text-dim)', fontSize: 13 }}> · {item.id}</span></span><span className="flex items-center gap-1" style={{ color: 'var(--text-dim)' }}>{item.id === defaultProfileId && <span className="text-xs">{t('settings.defaultProfile')}</span>}<ChevronRight size={12} strokeWidth={1.5} /></span></button>)}
        </div> : <>
          <div className="mx-2 text-xs" style={{ paddingTop: 7, paddingBottom: 7, color: 'var(--text-secondary)', borderBottom: '1px solid var(--border)', fontSize: 13 }}><span className="font-semibold">{activeProfile?.label}</span><span style={{ color: 'var(--text-dim)', fontSize: 13 }}> · {activeProfile?.id}</span></div>
          <div className="overflow-y-auto" style={{ maxHeight: MODEL_LIST_MAX_HEIGHT }}>
            {cache?.loading ? <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{t('settings.loadingModels')}</div> : filteredModels.length ? filteredModels.map((item) => { const active = (selectedParts[0] === activeProfile?.id && selectedParts[1] === item.id) || (!selectedModel && activeProfile?.id === defaultProfileId && activeProfile.default_model === item.id); return <button key={item.id} type="button" title={item.id} onClick={() => chooseModel(item.id)} className="flex items-center w-full px-2 text-xs" style={{ height: MODEL_ROW_HEIGHT, background: active ? 'var(--bg-surface)' : 'transparent', border: 'none', borderLeft: active ? '2px solid var(--cyan)' : '2px solid transparent', color: active ? 'var(--text-primary)' : 'var(--text-secondary)', textAlign: 'left', cursor: 'pointer', fontFamily: 'var(--font-code)', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>{item.id}</button> }) : <div className="px-2 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>{cache?.loaded ? t('settings.noModelsAvailable') : t('settings.openToLoadModels')}</div>}
          </div>
        </>}
      </div>}
    </div>
  )
}
