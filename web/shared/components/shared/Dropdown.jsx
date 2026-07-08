import { useState, useRef, useEffect, useMemo } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { ChevronDown, Search } from 'lucide-react'

// Above this option count the open menu virtualizes its list — e.g. the admin
// console's account picker on a large tenant. Below it, plain rendering.
const VIRTUALIZE_AT = 50

/**
 * Dropdown — the canonical select control for the whole app.
 *
 * NEVER use a native <select>: its user-agent styling (system arrow, white menu,
 * OS fonts) breaks the design system. Use this instead. The look is the agent UI
 * model selector: a bg-surface trigger with a chevron, an elevated menu that springs
 * in (200ms cubic-bezier), and an ACTIVE option marked by a 2px left cyan border —
 * never a checkmark or a filled background alone. Layout via Tailwind, every color /
 * border / radius from CSS variables.
 *
 * Props:
 *   options: [{ value, label, icon?, disabled? }]
 *   value, onChange(value)
 *   icon       optional lucide icon component at the left of the trigger
 *   align      'left' | 'right'  — which edge the menu aligns to (default 'left')
 *   placement  'bottom' | 'top'  — open direction (default 'bottom')
 *   size       'sm' | 'md'       — trigger height 24/28 (default 'md')
 *   searchable show a filter input (default false; for long lists)
 *   mono       monospace label + menu (default false → inherits the UI font)
 */
export default function Dropdown({
  options = [],
  value,
  onChange,
  icon: Icon,
  align = 'left',
  placement = 'bottom',
  size = 'md',
  searchable = false,
  mono = false,
  disabled = false,
  placeholder = 'Select',
  title,
  ariaLabel,
  minMenuWidth = 160,
  maxTriggerWidth = 240,
}) {
  const [open, setOpen] = useState(false)
  const [filter, setFilter] = useState('')
  const ref = useRef(null)
  const filterRef = useRef(null)

  const selected = useMemo(() => options.find((o) => o.value === value) || null, [options, value])
  const label = selected?.label ?? placeholder

  const filtered = useMemo(() => {
    if (!searchable || !filter.trim()) return options
    const q = filter.toLowerCase()
    return options.filter((o) => String(o.label).toLowerCase().includes(q))
  }, [options, filter, searchable])

  // Close on outside click; reset the filter so the next open starts clean.
  useEffect(() => {
    if (!open) return
    const handler = (e) => {
      if (ref.current && !ref.current.contains(e.target)) { setOpen(false); setFilter('') }
    }
    document.addEventListener('mousedown', handler)
    return () => document.removeEventListener('mousedown', handler)
  }, [open])

  useEffect(() => {
    if (open && searchable && filterRef.current) filterRef.current.focus()
  }, [open, searchable])

  const menuListRef = useRef(null)
  const menuVirtualizer = useVirtualizer({
    count: filtered.length,
    getScrollElement: () => menuListRef.current,
    estimateSize: () => 29, // px-3 py-2 single-line option
    overscan: 10,
    getItemKey: (i) => String(filtered[i].value),
    enabled: open && filtered.length > VIRTUALIZE_AT,
  })

  const fontFamily = mono ? "'JetBrains Mono', 'Source Han Mono SC', monospace" : undefined
  const dims = size === 'sm'
    ? { height: 24, fontSize: 11, icon: 11, chevron: 10 }
    : { height: 28, fontSize: 12, icon: 12, chevron: 10 }

  const menuPos = placement === 'top' ? { bottom: '100%', marginBottom: 4 } : { top: '100%', marginTop: 4 }
  const menuAlign = align === 'right' ? { right: 0 } : { left: 0 }
  const closedTransform = placement === 'top' ? 'translateY(4px)' : 'translateY(-4px)'

  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        title={title}
        className="flex items-center gap-1 px-2"
        style={{
          height: dims.height,
          background: 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          cursor: disabled ? 'default' : 'pointer',
          color: 'var(--text-secondary)',
          fontSize: dims.fontSize,
          fontFamily,
          opacity: disabled ? 0.5 : 1,
          transition: 'color 150ms ease, border-color 150ms ease',
          maxWidth: maxTriggerWidth,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
        }}
        onClick={() => { if (disabled) return; setOpen((o) => !o); setFilter('') }}
        onMouseEnter={(e) => {
          if (disabled) return
          e.currentTarget.style.borderColor = 'var(--border-strong)'
          e.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(e) => {
          e.currentTarget.style.borderColor = 'var(--border)'
          e.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        {Icon && <Icon size={dims.icon} strokeWidth={1.5} style={{ flexShrink: 0 }} />}
        <span className="truncate">{label}</span>
        <ChevronDown size={dims.chevron} strokeWidth={1.5} style={{ flexShrink: 0 }} />
      </button>

      <div
        className="absolute flex flex-col"
        role="listbox"
        style={{
          ...menuPos,
          ...menuAlign,
          background: 'var(--bg-elevated)',
          border: '1px solid var(--border)',
          borderRadius: 4,
          minWidth: minMenuWidth,
          maxWidth: 320,
          maxHeight: 280,
          zIndex: 50,
          opacity: open ? 1 : 0,
          transform: open ? 'translateY(0)' : closedTransform,
          pointerEvents: open ? 'auto' : 'none',
          // Spring in; the exit runs a balanced curve slightly longer — the
          // spring front-loads ~80% into the first 40ms, which makes a
          // dismissal on it read as a hard cut.
          transition: open
            ? 'opacity 200ms cubic-bezier(0.16, 1, 0.3, 1), transform 200ms cubic-bezier(0.16, 1, 0.3, 1)'
            : 'opacity 250ms cubic-bezier(0.4, 0, 0.2, 1), transform 250ms cubic-bezier(0.4, 0, 0.2, 1)',
          fontFamily,
        }}
      >
        {searchable && (
          <div className="flex items-center gap-2 px-2 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
            <Search size={12} strokeWidth={1.5} style={{ color: 'var(--text-dim)', flexShrink: 0 }} />
            <input
              ref={filterRef}
              type="text"
              value={filter}
              onChange={(e) => setFilter(e.target.value)}
              placeholder="Filter..."
              className="flex-1 py-2 text-xs"
              style={{ background: 'transparent', border: 'none', outline: 'none', color: 'var(--text-primary)', fontFamily, fontSize: 12, minWidth: 0 }}
            />
          </div>
        )}

        <div ref={menuListRef} className="overflow-y-auto" style={{ maxHeight: 240 }}>
          {(() => {
            const renderOption = (o) => {
              const isActive = o.value === value
              const OptIcon = o.icon
              return (
                <button
                  key={String(o.value)}
                  type="button"
                  role="option"
                  aria-selected={isActive}
                  disabled={o.disabled}
                  className="flex items-center gap-2 w-full px-3 py-2 text-xs"
                  style={{
                    background: isActive ? 'var(--bg-surface)' : 'transparent',
                    border: 'none',
                    borderLeft: isActive ? '2px solid var(--cyan)' : '2px solid transparent',
                    color: o.disabled ? 'var(--text-dim)' : isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                    cursor: o.disabled ? 'default' : 'pointer',
                    fontFamily,
                    fontSize: 12,
                    textAlign: 'left',
                    transition: 'background 150ms ease',
                    wordBreak: 'break-all',
                  }}
                  onClick={() => { if (o.disabled) return; onChange(o.value); setOpen(false); setFilter('') }}
                  onMouseEnter={(e) => { if (!isActive && !o.disabled) e.currentTarget.style.background = 'var(--bg-surface)' }}
                  onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.background = 'transparent' }}
                >
                  {OptIcon && <OptIcon size={12} strokeWidth={1.5} style={{ flexShrink: 0 }} />}
                  <span className="truncate">{o.label}</span>
                </button>
              )
            }

            if (filtered.length === 0) {
              return <div className="px-3 py-2 text-xs" style={{ color: 'var(--text-dim)' }}>No matches</div>
            }
            if (filtered.length > VIRTUALIZE_AT) {
              return (
                <div style={{ height: menuVirtualizer.getTotalSize(), position: 'relative', width: '100%' }}>
                  {menuVirtualizer.getVirtualItems().map((vi) => (
                    <div
                      key={vi.key}
                      data-index={vi.index}
                      ref={menuVirtualizer.measureElement}
                      style={{ position: 'absolute', top: vi.start, left: 0, width: '100%' }}
                    >
                      {renderOption(filtered[vi.index])}
                    </div>
                  ))}
                </div>
              )
            }
            return filtered.map(renderOption)
          })()}
        </div>
      </div>
    </div>
  )
}
