import { useEffect, useState } from 'react'

/**
 * StatusRow — generic sequential-timeline status surface.
 *
 * Presentational only: it knows nothing about workflows, tasks, or any store.
 * Ported from the "StatusRow full detail with progress" pattern in
 * docs/shared-components-preview.html. The CSS lives in web/shared/index.css
 * (`.status-row*`, `.status-step*`, `.progress-strip`, `.live-chip`).
 *
 * Phases map to the `.status-step` rail; anything nested (e.g. parallel agents)
 * is passed as `step.children` and renders inside `.status-step-content`.
 *
 * Props:
 *   variant      'full' | 'medium' | 'compact'   (default 'full')
 *   status       'running' | 'success' | 'pending' | 'error'
 *   title        ReactNode  — uppercase header line
 *   description  ReactNode  — sub-line under the title
 *   headerChips  ReactNode  — right-aligned chips (counts, live indicator)
 *   meta         string[]   — monospace meta row (task · model · tokens · elapsed)
 *   steps        [{ key?, status, name, description?, chips?, children? }]
 *   progress     number (0..1 or 0..100) | { done, total }
 *   latest       ReactNode  — "latest event" line under the step list
 *   actions      [{ label, onClick, danger?, disabled?, icon? }]
 *   collapsible  show the "Step details" toggle (default true)
 *   defaultOpen  steps expanded initially (default true)
 *   dense        tighter padding for the Canvas mirror (default false)
 */

const STATUS_CLASS = { running: 'running', success: 'success', pending: 'pending', error: 'error' }

function fillColorFor(status) {
  if (status === 'error') return 'var(--red)'
  if (status === 'success') return 'var(--green)'
  if (status === 'pending') return 'var(--yellow)'
  return 'var(--purple)'
}

function toPercent(progress) {
  if (progress == null) return null
  let pct
  if (typeof progress === 'number') {
    pct = progress <= 1 ? progress * 100 : progress
  } else if (progress.total > 0) {
    pct = (progress.done / progress.total) * 100
  } else {
    pct = 0
  }
  return Math.max(0, Math.min(100, pct))
}

export default function StatusRow({
  variant = 'full',
  status = 'pending',
  title,
  description,
  headerChips = null,
  meta = [],
  steps = [],
  progress = null,
  latest = null,
  actions = [],
  collapsible = true,
  defaultOpen = true,
  dense = false,
  className = '',
}) {
  const [open, setOpen] = useState(defaultOpen)
  // Re-open when a collapsed surface gains its first step (first progress event).
  useEffect(() => {
    if (defaultOpen && steps.length > 0) setOpen(true)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [steps.length === 0])

  const statusClass = STATUS_CLASS[status] || 'pending'
  const pct = toPercent(progress)

  if (variant === 'compact' || variant === 'medium') {
    return (
      <div className={`status-row ${variant} ${statusClass} overflow-hidden ${className}`}>
        {variant === 'compact' && <span className="compact-code truncate">{title}</span>}
        <div style={{ minWidth: 0 }}>
          {variant === 'medium' && title && <h3 className="truncate">{title}</h3>}
          {description && (
            variant === 'compact'
              ? <span className="compact-text">{description}</span>
              : <p>{description}</p>
          )}
        </div>
        {headerChips}
      </div>
    )
  }

  return (
    <div
      className={`status-row full ${statusClass} overflow-hidden ${className}`}
      style={dense ? { padding: 10, gap: 8 } : undefined}
    >
      <div className="status-full-header">
        <div style={{ minWidth: 0 }}>
          {title && <h3 style={{ wordBreak: 'break-word' }}>{title}</h3>}
          {description && <p>{description}</p>}
        </div>
        {headerChips && <div className="status-header-chips">{headerChips}</div>}
      </div>

      {meta.length > 0 && (
        <div className="status-meta">
          {meta.map((m, i) => <span key={i}>{m}</span>)}
        </div>
      )}

      {steps.length > 0 && collapsible && (
        <button
          type="button"
          className="status-collapse-toggle"
          aria-expanded={open}
          onClick={() => setOpen((o) => !o)}
        >
          Step details
        </button>
      )}

      {steps.length > 0 && (
        <div className={`status-step-details ${collapsible && !open ? 'collapsed' : ''}`}>
          <div className="status-step-list">
            {steps.map((step, i) => (
              <div key={step.key ?? i} className={`status-step ${STATUS_CLASS[step.status] || 'pending'}`}>
                <div className="status-step-rail">
                  <span className="status-step-icon" aria-label={step.status} />
                  {i < steps.length - 1 && <span className="status-step-connector" />}
                </div>
                <div className="status-step-content">
                  <div
                    className="status-step-name"
                    style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', minWidth: 0 }}
                  >
                    <span className="min-w-0 truncate" style={{ flex: '0 1 auto' }}>{step.name}</span>
                    {step.chips}
                  </div>
                  {step.description && <div className="status-step-desc">{step.description}</div>}
                  {step.children}
                </div>
              </div>
            ))}
          </div>
          {latest && (
            <p className="status-step-desc" style={{ margin: 0 }}>{latest}</p>
          )}
        </div>
      )}

      {pct != null && (
        <div className="progress-strip" aria-label="progress">
          <span className="progress-fill" style={{ width: `${pct}%`, background: fillColorFor(status) }} />
        </div>
      )}

      {actions.length > 0 && (
        <div className="status-actions">
          {actions.map((a, i) => (
            <button
              key={i}
              type="button"
              className={`status-action-btn${a.danger ? ' danger' : ''}`}
              onClick={a.onClick}
              disabled={a.disabled}
            >
              {a.icon}
              {a.label}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
