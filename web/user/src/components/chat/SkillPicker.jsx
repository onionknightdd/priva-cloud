import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronRight } from 'lucide-react'
import usePopoverTransition from '@shared/motion/usePopoverTransition'

export default function SkillPicker({ skills, query, onSelect, onClose, activeIndex, loading, positionStyle, open = true }) {
  const { t } = useTranslation()
  const listRef = useRef(null)
  const activeRef = useRef(null)
  // Popover envelope: parent keeps the picker rendered with open=false while
  // the dismissal fade plays ('top' = the picker sits above its trigger).
  const { mounted, popRef } = usePopoverTransition({ open, placement: 'top' })

  // Filter skills by query
  const q = query.toLowerCase()
  const filtered = q
    ? skills.filter((s) => s.name.toLowerCase().includes(q))
    : skills

  const projectSkills = filtered.filter((s) => s.level === 'project')
  const globalSkills = filtered.filter((s) => s.level === 'global')
  const builtinCommands = filtered.filter((s) => s.level === 'builtin')

  // Scroll active item into view
  useEffect(() => {
    if (activeRef.current) {
      activeRef.current.scrollIntoView({ block: 'nearest' })
    }
  }, [activeIndex])

  const popupPosition = positionStyle || {
    position: 'absolute',
    bottom: '100%',
    left: 0,
    right: 0,
    marginBottom: 4,
  }

  // Build flat list for index mapping — must match the parent's
  // getFilteredSkills order (project → global → builtin) for keyboard nav.
  const flatList = [...projectSkills, ...globalSkills, ...builtinCommands]

  const headerEl = (
    <div
      className="px-3 pt-2 pb-2"
      style={{
        color: 'var(--text-secondary)',
        fontSize: 12,
        fontWeight: 400,
        borderBottom: '1px solid var(--border-subtle)',
        marginBottom: 2,
      }}
    >
      {t('skillPicker.header')}
    </div>
  )

  if (!mounted) return null

  let runningIndex = 0

  const renderItem = (skill) => {
    const idx = runningIndex++
    const isActive = idx === activeIndex
    return (
      <div
        key={`${skill.level}-${skill.name}`}
        ref={isActive ? activeRef : null}
        className="flex items-center gap-2 px-3 py-1 cursor-pointer"
        style={{
          background: isActive ? 'var(--bg-elevated)' : 'transparent',
          borderLeft: isActive ? '2px solid var(--blue)' : '2px solid transparent',
          transition: 'background 150ms ease',
        }}
        onClick={() => onSelect(skill.name)}
        onMouseEnter={(e) => {
          if (!isActive) e.currentTarget.style.background = 'var(--bg-elevated)'
        }}
        onMouseLeave={(e) => {
          if (!isActive) e.currentTarget.style.background = 'transparent'
        }}
      >
        <ChevronRight size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
        <span
          className="truncate"
          style={{
            color: 'var(--text-primary)',
            fontWeight: 600,
            fontSize: 13,
            minWidth: 0,
          }}
        >
          {skill.name}
        </span>
        <span className="flex-1 min-w-0" />
        <span
          className="uppercase flex-shrink-0"
          style={{
            color: 'var(--text-dim)',
            fontSize: 11,
            letterSpacing: '0.06em',
            fontWeight: 600,
          }}
        >
          {skill.level === 'project'
            ? t('skillPicker.project')
            : skill.level === 'builtin'
              ? t('skillPicker.builtin')
              : t('skillPicker.global')}
        </span>
      </div>
    )
  }

  const renderDescription = (skill, idx) => {
    if (!skill.description) return null
    return (
      <div
        key={`desc-${skill.level}-${skill.name}`}
        className="px-3 pb-1 truncate"
        style={{
          paddingLeft: 34,
          color: 'var(--text-dim)',
          fontSize: 12,
        }}
      >
        {skill.description}
      </div>
    )
  }

  let body
  if (loading) {
    body = [1, 2, 3].map((i) => (
      <div key={i} className="px-3 py-2 flex items-center gap-2">
        <div className="skeleton" style={{ width: 120, height: 14, borderRadius: 2 }} />
        <div className="flex-1" />
        <div className="skeleton" style={{ width: 50, height: 10, borderRadius: 2 }} />
      </div>
    ))
  } else if (flatList.length === 0) {
    body = (
      <div className="px-3 py-2">
        <span style={{ color: 'var(--text-dim)', fontSize: 12 }}>
          {t('skillPicker.noMatch')}
        </span>
      </div>
    )
  }

  return (
    <div
      ref={(el) => {
        listRef.current = el
        popRef.current = el
      }}
      className="skill-picker-popup"
      style={{
        ...popupPosition,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border-strong)',
        borderRadius: 4,
        maxHeight: 280,
        overflowY: 'auto',
        zIndex: 50,
        padding: '0 0 4px 0',
        pointerEvents: open ? 'auto' : 'none',
      }}
    >
      {headerEl}
      {body}
      {!body && projectSkills.length > 0 && (
        <>
          <div
            className="px-3 pt-2 pb-1 uppercase"
            style={{
              color: 'var(--text-dim)',
              fontSize: 11,
              letterSpacing: '0.06em',
              fontWeight: 600,
            }}
          >
            {t('skillPicker.project')}
          </div>
          {projectSkills.map((skill) => (
            <div key={`${skill.level}-${skill.name}`}>
              {renderItem(skill)}
              {renderDescription(skill)}
            </div>
          ))}
        </>
      )}
      {!body && globalSkills.length > 0 && (
        <>
          <div
            className="px-3 pt-2 pb-1 uppercase"
            style={{
              color: 'var(--text-dim)',
              fontSize: 11,
              letterSpacing: '0.06em',
              fontWeight: 600,
            }}
          >
            {t('skillPicker.global')}
          </div>
          {globalSkills.map((skill) => (
            <div key={`${skill.level}-${skill.name}`}>
              {renderItem(skill)}
              {renderDescription(skill)}
            </div>
          ))}
        </>
      )}
      {/* Builtins are static — keep them visible (and selectable) even while
          the dynamic skill groups are still loading, so keyboard indexes stay
          in sync with the parent's filtered list. */}
      {(!body || loading) && builtinCommands.length > 0 && (
        <>
          <div
            className="px-3 pt-2 pb-1 uppercase"
            style={{
              color: 'var(--text-dim)',
              fontSize: 11,
              letterSpacing: '0.06em',
              fontWeight: 600,
            }}
          >
            {t('skillPicker.builtin')}
          </div>
          {builtinCommands.map((skill) => (
            <div key={`${skill.level}-${skill.name}`}>
              {renderItem(skill)}
              {renderDescription(skill)}
            </div>
          ))}
        </>
      )}
    </div>
  )
}

// Helper to get the filtered count (used by parent for keyboard nav)
export function getFilteredSkills(skills, query) {
  const q = query.toLowerCase()
  const filtered = q
    ? skills.filter((s) => s.name.toLowerCase().includes(q))
    : skills
  return filtered
}
