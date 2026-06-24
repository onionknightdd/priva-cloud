import { useEffect, useMemo, useRef } from 'react'
import { motion } from 'framer-motion'
import { animate, svg } from 'animejs'

// SystemTopologyDiagram — a modernized §3 topology (docs/priva-cloud-architecture-report.html)
// rendered as pure SVG on the GitHub-dark palette. Four color-coded planes
// (edge=orange, control=purple, data=cyan, tenant=green). Edges are smooth bezier
// curves routed between the nearest box sides; agent-runner is a cascade stack of
// pods. Byte-path edges animate a CONSTANT particle flow (anime.js) while healthy,
// freeze + show an ✕ when unreachable, and are fully disabled under
// prefers-reduced-motion. Read-only — it draws whatever /admin/system-health returns.
//
// SANCTIONED continuous-animation exception (CLAUDE.md + design-spec §8): the
// byte-path particle flow. Everything else obeys the no-continuous-motion rule.

const VIEW_W = 1000
const VIEW_H = 700
const PARTICLE_MS = 2600       // constant per-edge traversal time (NOT req/s-scaled)
const PARTICLES_PER_EDGE = 3
const STACK_OFFSET = 12        // cascade offset for the agent-runner pod stack
const CORNER_R = 28            // rounded-corner radius → curved bends on clean lanes

// Verified planar (zero-crossing) node boxes in §3 coordinate space. For agent-runner
// this is the FRONT (live) box; two dim boxes cascade up-right behind it.
const NODE_BOX = {
  browser: { x: 408, y: 20, w: 184, h: 44 },
  agentgateway: { x: 70, y: 96, w: 860, h: 54 },
  'control-panel': { x: 60, y: 210, w: 340, h: 116 },
  operator: { x: 130, y: 430, w: 180, h: 60 },
  scheduler: { x: 340, y: 436, w: 150, h: 54 },
  'agent-runner': { x: 630, y: 238, w: 300, h: 76 },
  'channel-connector': { x: 780, y: 432, w: 160, h: 54 },
  'state-reader': { x: 780, y: 500, w: 160, h: 54 },
  'data-spine': { x: 70, y: 588, w: 580, h: 78 },
  redis: { x: 690, y: 588, w: 250, h: 78 },
}

const PLANES = [
  { id: 'edge', label: 'EDGE', color: 'var(--orange)', x: 0, y: 0, w: 1000, h: 168, lx: 982, ly: 24, anchor: 'end' },
  { id: 'control', label: 'CONTROL', color: 'var(--purple)', x: 0, y: 168, w: 496, h: 400, lx: 16, ly: 188, anchor: 'start' },
  { id: 'tenant', label: 'TENANT', color: 'var(--green)', x: 504, y: 168, w: 496, h: 400, lx: 984, ly: 188, anchor: 'end' },
  { id: 'data', label: 'DATA', color: 'var(--cyan)', x: 0, y: 568, w: 1000, h: 132, lx: 982, ly: 690, anchor: 'end' },
]

const STATUS_COLOR = {
  up: 'var(--green)',
  degraded: 'var(--status-pending)',
  down: 'var(--red)',
  idle: 'var(--cyan)',
  disabled: 'var(--status-idle)',
}

const edgeKey = (e) => `${e.source}|${e.target}|${e.kind}`
const fmt = (v) => (v == null || Number.isNaN(v) ? '0' : String(Math.round(v)))

// --- Edge routing: verified ZERO-CROSSING waypoint lanes, rendered as smooth
// rounded-corner paths (curved bends, no crossings). `lab` = label [x,y,anchor];
// `mid` = ✕ marker anchor. ---
const EDGE_ROUTE = {
  'browser|agentgateway|byte': { pts: [[500, 64], [500, 96]], mid: [500, 80], lab: [508, 84, 'start'] },
  'agentgateway|control-panel|byte': { pts: [[150, 150], [150, 210]], mid: [150, 180], lab: [158, 178, 'start'] },
  'agentgateway|control-panel|decision': { pts: [[330, 150], [330, 210]], mid: [330, 180], lab: [338, 178, 'start'] },
  'agentgateway|agent-runner|byte': { pts: [[720, 150], [720, 238]], mid: [720, 196], lab: [728, 196, 'start'] },
  'control-panel|operator|control': { pts: [[180, 326], [180, 430]], mid: [180, 380], lab: [188, 382, 'start'] },
  'operator|agent-runner|control': { pts: [[310, 430], [310, 400], [560, 400], [560, 290], [630, 290]], mid: [435, 400], lab: [435, 392, 'middle'] },
  'control-panel|data-spine|grpc': { pts: [[100, 326], [100, 588]], mid: [100, 470], lab: [108, 470, 'start'] },
  'operator|data-spine|grpc': { pts: [[230, 490], [230, 588]], mid: [230, 540], lab: [238, 540, 'start'] },
  'agent-runner|data-spine|grpc': { pts: [[645, 314], [645, 588]], mid: [645, 458], lab: [653, 452, 'start'] },
  'channel-connector|agent-runner|control': { pts: [[855, 432], [855, 314]], mid: [855, 375] },
  'scheduler|operator|control': { pts: [[340, 460], [310, 460]], mid: [325, 460] },
  'scheduler|agent-runner|control': { pts: [[490, 460], [600, 460], [600, 300], [630, 300]], mid: [600, 380] },
  'data-spine|redis|grpc': { pts: [[650, 627], [690, 627]], mid: [670, 627] },
}

// Polyline → SVG path with rounded corners (quadratic fillets) of radius r.
function roundedPath(pts, r) {
  if (!pts || pts.length < 2) return ''
  if (pts.length === 2) return `M${pts[0][0]},${pts[0][1]} L${pts[1][0]},${pts[1][1]}`
  let d = `M${pts[0][0]},${pts[0][1]}`
  for (let i = 1; i < pts.length - 1; i++) {
    const [x0, y0] = pts[i - 1], [x1, y1] = pts[i], [x2, y2] = pts[i + 1]
    const d1 = Math.hypot(x1 - x0, y1 - y0) || 1
    const d2 = Math.hypot(x2 - x1, y2 - y1) || 1
    const rr = Math.min(r, d1 / 2, d2 / 2)
    const ax = (x1 + (x0 - x1) / d1 * rr).toFixed(1), ay = (y1 + (y0 - y1) / d1 * rr).toFixed(1)
    const bx = (x1 + (x2 - x1) / d2 * rr).toFixed(1), by = (y1 + (y2 - y1) / d2 * rr).toFixed(1)
    d += ` L${ax},${ay} Q${x1},${y1} ${bx},${by}`
  }
  const last = pts[pts.length - 1]
  d += ` L${last[0]},${last[1]}`
  return d
}

// Fallback straight route between node centers — guards against any edge the
// backend emits that isn't in EDGE_ROUTE (so the diagram never throws).
function fallbackRoute(edge) {
  const s = NODE_BOX[edge.source], t = NODE_BOX[edge.target]
  if (!s || !t) return null
  const a = [s.x + s.w / 2, s.y + s.h / 2], b = [t.x + t.w / 2, t.y + t.h / 2]
  return { pts: [a, b], mid: [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2] }
}

function edgeVisual(edge) {
  if (edge.disabled) return { stroke: 'var(--status-idle)', width: 1.3, dash: '4 5', opacity: 0.4 }
  if (!edge.healthy) {
    const dash = edge.kind === 'byte' ? null : edge.kind === 'decision' ? '3 4' : '5 4'
    return { stroke: 'var(--red)', width: edge.kind === 'byte' ? 2 : 1.5, dash, opacity: 0.6 }
  }
  if (edge.kind === 'byte') return { stroke: 'var(--cyan)', width: 2, dash: null, opacity: 0.4 }
  if (edge.kind === 'decision') return { stroke: 'var(--border-strong)', width: 1.4, dash: '3 4', opacity: 0.85 }
  if (edge.kind === 'grpc') return { stroke: 'var(--border-strong)', width: 1.4, dash: '5 4', opacity: 0.85 }
  return { stroke: 'var(--border-strong)', width: 1.4, dash: null, opacity: 0.85 } // control
}

function metricLine(node) {
  const m = node.metrics || {}
  switch (node.id) {
    case 'agentgateway':
      return node.status === 'up' ? `${fmt(m.connections)} conns` : null
    case 'operator':
      return `${fmt(m.ready)}/${fmt(m.desired)} ready`
    case 'data-spine': {
      const parts = []
      if ('accounts' in m) parts.push(`${fmt(m.accounts)} acct`)
      if ('jobs' in m) parts.push(`${fmt(m.jobs)} job`)
      if ('runs' in m) parts.push(`${fmt(m.runs)} run`)
      return parts.join(' · ') || null
    }
    case 'agent-runner':
      return `${fmt(m.awake)} awake · ${fmt(m.running)} run · ${fmt(m.total)} total`
    default:
      return null
  }
}

function NodeView({ node, reducedMotion }) {
  const box = NODE_BOX[node.id]
  if (!box) return null
  const disabled = node.status === 'disabled'
  const color = STATUS_COLOR[node.status] || 'var(--status-idle)'
  const metric = metricLine(node)
  const wide = node.id === 'agentgateway' || node.id === 'data-spine'
  const isCP = node.id === 'control-panel'
  const isStack = node.id === 'agent-runner'
  const subY = box.y + (box.h >= 64 ? 41 : 38)
  const metricY = wide ? box.y + box.h - 14 : node.sub ? subY + 15 : box.y + 38

  return (
    <g style={{ opacity: disabled ? 0.45 : 1 }}>
      {/* Cascade pod stack: two dim boxes offset up-right behind the live front box */}
      {isStack && [2, 1].map((k) => (
        <rect key={`bk-${k}`} x={box.x + k * STACK_OFFSET} y={box.y - k * STACK_OFFSET}
          width={box.w} height={box.h} rx={4}
          fill="var(--bg-surface)" stroke="var(--border)" strokeWidth={1}
          opacity={k === 2 ? 0.4 : 0.65} />
      ))}

      {/* Body */}
      <rect x={box.x} y={box.y} width={box.w} height={box.h} rx={4}
        fill="var(--bg-surface)" stroke="var(--border)" strokeWidth={1}
        strokeDasharray={disabled ? '4 4' : undefined} />
      {/* Status left-border (2px), color-animated over 150ms */}
      <motion.rect x={box.x} y={box.y} width={2.5} height={box.h}
        initial={false} animate={{ fill: color }}
        transition={{ duration: reducedMotion ? 0 : 0.15 }} style={{ fill: color }} />

      {/* Title */}
      <text x={box.x + 16} y={box.y + 24} fontSize={14} fontWeight={600} fill="var(--text-primary)">
        {node.label}
      </text>
      {/* Plane tag (wide bars) on the right */}
      {wide && (
        <text x={box.x + box.w - 16} y={box.y + 24} fontSize={11} textAnchor="end"
          fill={node.plane === 'edge' ? 'var(--orange)' : 'var(--cyan)'}
          style={{ letterSpacing: '0.06em' }}>
          {node.plane === 'edge' ? 'EDGE' : 'DATA'}
        </text>
      )}

      {isCP ? (
        <>
          {/* control-panel listener sub-rows (:8080 HTTP · :9000 EPP) */}
          <rect x={box.x + 16} y={box.y + 40} width={box.w - 32} height={28} rx={3}
            fill="var(--bg-elevated)" stroke="var(--border-subtle)" strokeWidth={1} />
          <text x={box.x + 26} y={box.y + 58} fontSize={10} fill="var(--text-secondary)"
            style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            :8080 HTTP — auth · admin · config SPA
          </text>
          <rect x={box.x + 16} y={box.y + 74} width={box.w - 32} height={28} rx={3}
            fill="var(--bg-elevated)" stroke="var(--border-subtle)" strokeWidth={1} />
          <text x={box.x + 26} y={box.y + 92} fontSize={10} fill="var(--cyan)"
            style={{ fontFamily: "'JetBrains Mono', monospace" }}>
            :9000 gRPC ext_proc — EndPoint Picker
          </text>
        </>
      ) : (
        <>
          {node.sub && (
            <text x={box.x + 16} y={subY} fontSize={10.5} fill="var(--text-dim)">{node.sub}</text>
          )}
          {(metric || (disabled && node.detail)) && (
            <text x={wide ? box.x + box.w - 16 : box.x + 16} y={metricY}
              textAnchor={wide ? 'end' : 'start'} fontSize={10.5}
              fill={disabled ? 'var(--text-dim)' : 'var(--text-secondary)'}
              style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {metric || node.detail}
            </text>
          )}
        </>
      )}
    </g>
  )
}

function XMark({ x, y }) {
  return (
    <g>
      <circle cx={x} cy={y} r={8} fill="var(--bg-base)" stroke="var(--red)" strokeWidth={1} />
      <path d={`M${x - 3.4},${y - 3.4} L${x + 3.4},${y + 3.4} M${x + 3.4},${y - 3.4} L${x - 3.4},${y + 3.4}`}
        stroke="var(--red)" strokeWidth={1.6} strokeLinecap="round" />
    </g>
  )
}

export default function SystemTopologyDiagram({ data, reducedMotion }) {
  const nodes = data?.nodes || []
  const edges = data?.edges || []

  // Resolve each edge to its zero-crossing lane, rendered as a smooth rounded path.
  const routedEdges = useMemo(() => edges.map((e) => {
    const key = edgeKey(e)
    const r = EDGE_ROUTE[key] || fallbackRoute(e)
    if (!r) return null
    return { ...e, key, d: roundedPath(r.pts, CORNER_R), mid: r.mid, lab: r.lab }
  }).filter(Boolean), [edges])

  const byteEdges = useMemo(() => routedEdges.filter((e) => e.bytepath), [routedEdges])

  const pathRefs = useRef({})       // edgeKey → <path> element (byte edges)
  const particleRefs = useRef({})   // `${edgeKey}#${i}` → <circle> element

  // Re-run the anime.js effect only when the animated set or reduced-motion changes
  // (NOT on every 5s poll).
  const byteSig = useMemo(
    () => byteEdges.map((e) => `${e.key}:${e.healthy ? 1 : 0}`).join(',') + `|rm:${reducedMotion ? 1 : 0}`,
    [byteEdges, reducedMotion],
  )

  useEffect(() => {
    const anims = []
    for (const e of byteEdges) {
      const live = !reducedMotion && e.healthy
      for (let i = 0; i < PARTICLES_PER_EDGE; i++) {
        const el = particleRefs.current[`${e.key}#${i}`]
        if (el) el.style.opacity = live ? '1' : '0'
      }
      if (!live) continue
      const pathEl = pathRefs.current[e.key]
      if (!pathEl) continue
      let motionPath
      try { motionPath = svg.createMotionPath(pathEl) } catch { continue }
      for (let i = 0; i < PARTICLES_PER_EDGE; i++) {
        const el = particleRefs.current[`${e.key}#${i}`]
        if (!el) continue
        anims.push(animate(el, {
          translateX: motionPath.translateX,
          translateY: motionPath.translateY,
          duration: PARTICLE_MS,
          delay: (PARTICLE_MS / PARTICLES_PER_EDGE) * i,
          ease: 'linear',
          loop: true,
        }))
      }
    }
    return () => { for (const a of anims) { try { a.revert ? a.revert() : a.pause?.() } catch { /* noop */ } } }
  }, [byteSig]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="w-full" style={{ minWidth: 0 }}>
      <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        style={{ display: 'block', width: '100%', height: 'auto' }}
        role="img"
        aria-label="Priva system topology and live health"
      >
        {/* Plane bands */}
        {PLANES.map((p) => (
          <g key={p.id}>
            <rect x={p.x} y={p.y} width={p.w} height={p.h} fill="var(--bg-elevated)" opacity={0.28} />
            <text x={p.lx} y={p.ly} textAnchor={p.anchor} fontSize={11} fill={p.color}
              style={{ letterSpacing: '0.14em', fontWeight: 600 }}>
              {p.label}
            </text>
          </g>
        ))}

        {/* Edges */}
        {routedEdges.map((e) => {
          const v = edgeVisual(e)
          return (
            <path key={e.key}
              ref={e.bytepath ? (el) => { pathRefs.current[e.key] = el } : undefined}
              d={e.d} fill="none" stroke={v.stroke} strokeWidth={v.width}
              strokeDasharray={v.dash || undefined} strokeLinecap="round" strokeLinejoin="round"
              opacity={v.opacity} />
          )
        })}

        {/* Edge labels (skip planned) */}
        {routedEdges.map((e) => {
          if (e.disabled || !e.label) return null
          const x = e.lab ? e.lab[0] : e.mid[0]
          const y = e.lab ? e.lab[1] : e.mid[1] - 6
          const anchor = e.lab ? e.lab[2] : 'middle'
          return (
            <text key={`l-${e.key}`} x={x} y={y} textAnchor={anchor} fontSize={8.5}
              fill="var(--text-dim)" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {e.label}
            </text>
          )
        })}

        {/* Byte-path particles (constant flow when healthy; hidden otherwise).
            Drawn before nodes so they're occluded as they enter a module. */}
        {byteEdges.map((e) => (
          <g key={`p-${e.key}`}>
            {Array.from({ length: PARTICLES_PER_EDGE }).map((_, i) => (
              <circle key={i} ref={(el) => { particleRefs.current[`${e.key}#${i}`] = el }}
                cx={0} cy={0} r={2.6} fill="var(--cyan)" style={{ opacity: 0 }} />
            ))}
          </g>
        ))}

        {/* Nodes */}
        {nodes.map((n) => <NodeView key={n.id} node={n} reducedMotion={reducedMotion} />)}

        {/* ✕ markers for unreachable (non-disabled, unhealthy) edges */}
        {routedEdges.map((e) => (
          !e.disabled && !e.healthy ? <XMark key={`x-${e.key}`} x={e.mid[0]} y={e.mid[1]} /> : null
        ))}
      </svg>

      {/* Legend */}
      <div className="flex items-center flex-wrap gap-x-4 gap-y-2" style={{ marginTop: 12 }}>
        {[
          { c: 'var(--green)', t: 'up' },
          { c: 'var(--status-pending)', t: 'degraded' },
          { c: 'var(--red)', t: 'down' },
          { c: 'var(--cyan)', t: 'idle' },
          { c: 'var(--status-idle)', t: 'disabled' },
        ].map((s) => (
          <span key={s.t} className="flex items-center gap-2 uppercase"
            style={{ fontSize: 11, color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
            <span style={{ width: 12, height: 0, borderTop: `2px solid ${s.c}` }} />
            {s.t}
          </span>
        ))}
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="22" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="22" y2="4" stroke="var(--cyan)" strokeWidth="2" opacity="0.4" />
            <circle cx="14" cy="4" r="2.4" fill="var(--cyan)" />
          </svg>
          byte path
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="14" height="14" aria-hidden="true">
            <circle cx="7" cy="7" r="6" fill="var(--bg-base)" stroke="var(--red)" strokeWidth="1" />
            <path d="M4.5,4.5 L9.5,9.5 M9.5,4.5 L4.5,9.5" stroke="var(--red)" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          unreachable
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{ width: 14, height: 0, borderTop: '1.3px dashed var(--status-idle)' }} />
          planned
        </span>
      </div>
    </div>
  )
}
