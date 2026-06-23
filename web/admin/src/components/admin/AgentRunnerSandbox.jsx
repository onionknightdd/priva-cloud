import { useState, useEffect, useRef, useCallback } from 'react'
import { Clock, Cpu, ShieldCheck, Package, Check } from 'lucide-react'
import { SlidingTabGroup, SlidingTabIndicator } from '@shared/components/shared/Tabs'

// Agent Runner Sandbox — Configurations section. Two columns: a left "index" of
// config groups (animated active highlight, like the tab switch) + a right scrollable
// "detail". The index follows the detail via scroll-spy; clicking it smooth-scrolls.
// Scaffold: the controls edit local state only — not yet wired to a backend.

const inputStyle = {
  width: '100%',
  padding: '8px 12px',
  background: 'var(--bg-elevated)',
  border: '1px solid var(--border)',
  borderRadius: 4,
  color: 'var(--text-primary)',
  fontSize: 13,
  fontFamily: "'Noto Sans', sans-serif",
  outline: 'none',
  boxSizing: 'border-box',
}

const labelStyle = {
  display: 'block',
  marginBottom: 4,
  color: 'var(--text-secondary)',
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

// Horizontal field row: title + description stacked on the left, value box on the
// right, vertically centered against each other.
const fieldTitleStyle = {
  color: 'var(--text-secondary)',
  fontSize: 11,
  fontWeight: 600,
  textTransform: 'uppercase',
  letterSpacing: '0.06em',
}

const FIELD_LABEL_WIDTH = 240
const FIELD_BOX_WIDTH = 260

const GROUPS = [
  {
    id: 'lifecycle',
    label: 'Lifecycle',
    icon: Clock,
    fields: [
      { id: 'idleGrace', label: 'Idle grace (seconds)', type: 'number', default: '120', hint: 'Idle time before the sandbox pod is scaled to zero.' },
      { id: 'minAlive', label: 'Min-alive (seconds)', type: 'number', default: '60', hint: 'Minimum lifetime after a wake before auto-sleep can trigger.' },
      { id: 'wakeTimeout', label: 'Wake timeout (seconds)', type: 'number', default: '30', hint: 'Max time the edge waits for a cold pod to answer.' },
    ],
  },
  {
    id: 'resources',
    label: 'Resources',
    icon: Cpu,
    fields: [
      { id: 'cpuReq', label: 'CPU request', type: 'text', default: '250m', mono: true },
      { id: 'cpuLimit', label: 'CPU limit', type: 'text', default: '1', mono: true },
      { id: 'memReq', label: 'Memory request', type: 'text', default: '512Mi', mono: true },
      { id: 'memLimit', label: 'Memory limit', type: 'text', default: '2Gi', mono: true },
      { id: 'pvcSize', label: 'Workspace PVC size', type: 'text', default: '5Gi', mono: true },
    ],
  },
  {
    id: 'isolation',
    label: 'Isolation',
    icon: ShieldCheck,
    fields: [
      { id: 'rootEscape', label: 'IS_SANDBOX root escape', type: 'toggle', default: true, hint: 'Marks the per-account pod as an isolated sandbox so the CLI may run as root.' },
      { id: 'egress', label: 'Network egress', type: 'segment', default: 'allow', options: [{ value: 'allow', label: 'Allow' }, { value: 'deny', label: 'Deny' }] },
    ],
  },
  {
    id: 'image',
    label: 'Image & CLI',
    icon: Package,
    fields: [
      { id: 'image', label: 'Agent-runner image', type: 'text', default: 'priva/agent-runner:dev', mono: true },
      { id: 'cliVersion', label: 'Claude CLI version', type: 'text', default: '2.1.x', mono: true },
    ],
  },
]

const SPY_SUPPRESS_MS = 600

export default function AgentRunnerSandbox() {
  const scrollRef = useRef(null)
  const sectionRefs = useRef({})
  const suppressSpy = useRef(false)
  const [active, setActive] = useState(GROUPS[0].id)

  const [values, setValues] = useState(() => {
    const v = {}
    GROUPS.forEach((g) => g.fields.forEach((f) => { v[f.id] = f.default }))
    return v
  })
  const [dirty, setDirty] = useState({})

  const setField = useCallback((groupId, fieldId, val) => {
    setValues((prev) => ({ ...prev, [fieldId]: val }))
    setDirty((prev) => ({ ...prev, [groupId]: true }))
  }, [])

  // Scroll-spy: the active index follows whichever section sits near the top of the
  // detail pane. Suppressed briefly while a click-driven smooth scroll is in flight.
  useEffect(() => {
    const root = scrollRef.current
    if (!root || typeof IntersectionObserver === 'undefined') return
    const obs = new IntersectionObserver(
      (entries) => {
        if (suppressSpy.current) return
        const visible = entries.filter((e) => e.isIntersecting)
        if (!visible.length) return
        visible.sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)
        const id = visible[0].target.dataset.groupId
        if (id) setActive(id)
      },
      { root, rootMargin: '0px 0px -70% 0px', threshold: 0 },
    )
    Object.values(sectionRefs.current).forEach((el) => el && obs.observe(el))
    return () => obs.disconnect()
  }, [])

  const goTo = useCallback((id) => {
    setActive(id)
    const el = sectionRefs.current[id]
    const root = scrollRef.current
    if (!el || !root) return
    suppressSpy.current = true
    const top = el.getBoundingClientRect().top - root.getBoundingClientRect().top + root.scrollTop
    root.scrollTo({ top, behavior: 'smooth' })
    window.setTimeout(() => { suppressSpy.current = false }, SPY_SUPPRESS_MS)
  }, [])

  const renderField = (group, f) => {
    const val = values[f.id]
    const hint = f.hint && (
      <div className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4, fontWeight: 300 }}>{f.hint}</div>
    )

    let control
    if (f.type === 'toggle') {
      control = (
        <div className="flex gap-2">
          {[{ v: true, l: 'On' }, { v: false, l: 'Off' }].map((opt) => {
            const on = val === opt.v
            return (
              <button
                key={opt.l}
                type="button"
                className="flex items-center gap-2 px-3 py-2 text-sm"
                style={{
                  background: on ? 'var(--bg-elevated)' : 'var(--bg-surface)',
                  border: `1px solid ${on ? 'var(--blue)' : 'var(--border)'}`,
                  borderRadius: 4,
                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontWeight: on ? 600 : 400,
                  cursor: 'pointer',
                  transition: 'background 150ms ease, border-color 150ms ease, color 150ms ease',
                }}
                onClick={() => setField(group.id, f.id, opt.v)}
              >
                {on && <Check size={14} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />}
                {opt.l}
              </button>
            )
          })}
        </div>
      )
    } else if (f.type === 'segment') {
      control = (
        <div className="flex gap-2">
          {f.options.map((opt) => {
            const on = val === opt.value
            return (
              <button
                key={opt.value}
                type="button"
                className="flex items-center gap-2 px-3 py-2 text-sm"
                style={{
                  background: on ? 'var(--bg-elevated)' : 'var(--bg-surface)',
                  border: `1px solid ${on ? 'var(--blue)' : 'var(--border)'}`,
                  borderRadius: 4,
                  color: on ? 'var(--text-primary)' : 'var(--text-secondary)',
                  fontWeight: on ? 600 : 400,
                  cursor: 'pointer',
                  transition: 'background 150ms ease, border-color 150ms ease, color 150ms ease',
                }}
                onClick={() => setField(group.id, f.id, opt.value)}
              >
                {on && <Check size={14} strokeWidth={1.5} style={{ color: 'var(--blue)' }} />}
                {opt.label}
              </button>
            )
          })}
        </div>
      )
    } else {
      control = (
        <input
          type={f.type === 'number' ? 'number' : 'text'}
          value={val}
          onChange={(e) => setField(group.id, f.id, e.target.value)}
          style={f.mono ? { ...inputStyle, fontFamily: "'JetBrains Mono', monospace", fontSize: 12 } : inputStyle}
        />
      )
    }

    return (
      <div key={f.id} className="flex items-center gap-4">
        <div style={{ width: FIELD_LABEL_WIDTH, flexShrink: 0 }}>
          <div style={fieldTitleStyle}>{f.label}</div>
          {hint}
        </div>
        <div style={{ width: FIELD_BOX_WIDTH, flexShrink: 0 }}>
          {control}
        </div>
      </div>
    )
  }

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Page header */}
      <div className="flex-shrink-0" style={{ padding: '20px 24px 12px 24px' }}>
        <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>Agent Runner Sandbox</h2>
        <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
          Sandbox pod defaults for per-account agent runners. Scaffold — controls are not yet wired to a backend.
        </p>
      </div>

      {/* Two columns: index + detail */}
      <div className="flex flex-1 min-h-0">
        {/* Index — top-aligned; active = elevated band + 2px blue left bar */}
        <div
          className="flex-shrink-0 overflow-y-auto"
          style={{ width: 216, borderRight: '1px solid var(--border)', padding: '8px 0 8px 16px' }}
        >
          <SlidingTabGroup id="sandbox-index">
            <div className="flex flex-col gap-1">
              {GROUPS.map((g) => {
                const isActive = active === g.id
                const Icon = g.icon
                return (
                  <button
                    key={g.id}
                    type="button"
                    onClick={() => goTo(g.id)}
                    className="relative flex items-center text-left"
                    style={{
                      width: '100%',
                      padding: '10px 16px',
                      background: 'transparent',
                      border: 'none',
                      cursor: 'pointer',
                      color: isActive ? 'var(--text-primary)' : 'var(--text-secondary)',
                      fontSize: 13,
                      fontWeight: isActive ? 600 : 400,
                      transition: 'color 150ms ease',
                    }}
                    onMouseEnter={(e) => { if (!isActive) e.currentTarget.style.color = 'var(--text-primary)' }}
                    onMouseLeave={(e) => { if (!isActive) e.currentTarget.style.color = 'var(--text-secondary)' }}
                  >
                    {isActive && (
                      <SlidingTabIndicator
                        variant="frame"
                        layoutId="sandbox-index-indicator"
                        style={{ border: 'none', borderLeft: '2px solid var(--blue)', borderRadius: 0 }}
                      />
                    )}
                    <span className="relative flex items-center gap-2" style={{ zIndex: 1, minWidth: 0 }}>
                      <Icon size={16} strokeWidth={1.5} className="flex-shrink-0" />
                      <span className="truncate">{g.label}</span>
                    </span>
                  </button>
                )
              })}
            </div>
          </SlidingTabGroup>
        </div>

        {/* Detail */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto" style={{ padding: '16px 24px 50vh 24px' }}>
          <div style={{ maxWidth: 520, margin: '0 auto' }}>
          {GROUPS.map((g) => {
            const Icon = g.icon
            return (
              <section
                key={g.id}
                data-group-id={g.id}
                ref={(el) => { sectionRefs.current[g.id] = el }}
                style={{ marginBottom: 32 }}
              >
                <div className="flex items-center gap-2" style={{ marginBottom: 14 }}>
                  <Icon size={16} strokeWidth={1.5} style={{ color: 'var(--text-secondary)' }} />
                  <h3 className="font-semibold" style={{ color: 'var(--text-primary)', fontSize: 14, margin: 0 }}>{g.label}</h3>
                </div>
                <div className="flex flex-col gap-4">
                  {g.fields.map((f) => renderField(g, f))}
                </div>
                <div className="flex justify-end" style={{ marginTop: 14 }}>
                  <button
                    type="button"
                    className="px-4 py-2 text-xs font-semibold"
                    disabled={!dirty[g.id]}
                    style={{
                      background: dirty[g.id] ? 'var(--blue)' : 'var(--bg-elevated)',
                      color: dirty[g.id] ? 'var(--text-inverse)' : 'var(--text-dim)',
                      border: 'none',
                      borderRadius: 4,
                      cursor: dirty[g.id] ? 'pointer' : 'default',
                      opacity: dirty[g.id] ? 1 : 0.5,
                      transition: 'opacity 150ms ease',
                    }}
                    onClick={() => setDirty((prev) => ({ ...prev, [g.id]: false }))}
                  >
                    Save
                  </button>
                </div>
              </section>
            )
          })}
          </div>
        </div>
      </div>
    </div>
  )
}
