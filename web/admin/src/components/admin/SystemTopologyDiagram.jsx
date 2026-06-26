import { useEffect, useMemo, useRef } from 'react'
import { motion } from 'framer-motion'
import { animate, svg } from 'animejs'
import { useTranslation } from 'react-i18next'

// SystemTopologyDiagram — a modernized §3 topology (docs/priva-cloud-architecture-report.html)
// rendered as pure SVG on the GitHub-dark palette. Four color-coded planes
// (edge=orange, control=purple, data=cyan, tenant=green). Edges are smooth bezier
// curves routed between the nearest box sides; agent-runner is a cascade stack of
// pods. Implemented edges animate a CONSTANT particle flow (anime.js) while healthy,
// freeze + show an X when unreachable, and are fully disabled under
// prefers-reduced-motion. Read-only — it draws whatever /admin/system-health returns.
//
// SANCTIONED continuous-animation exception (CLAUDE.md + design-spec §8): the
// system-map path particle flow. Everything else obeys the no-continuous-motion rule.

const VIEW_W = 1000
const VIEW_H = 700
const PARTICLE_MS = 2600       // constant per-edge traversal time (NOT req/s-scaled)
const PARTICLES_PER_EDGE = 3
const STACK_OFFSET = 12        // cascade offset for the agent-runner pod stack
const CORNER_R = 28            // rounded-corner radius → curved bends on clean lanes

// Verified planar (zero-crossing) node boxes in §3 coordinate space. For agent-runner
// this is the FRONT (live) box; two dim boxes cascade up-right behind it.
const NODE_BOX = {
  browser: { x: 408, y: 2, w: 184, h: 72 },
  agentgateway: { x: 70, y: 104, w: 860, h: 62 },
  'control-panel': { x: 60, y: 210, w: 340, h: 116 },
  operator: { x: 96, y: 416, w: 180, h: 76 },
  scheduler: { x: 300, y: 422, w: 164, h: 80 },
  'agent-runner': { x: 630, y: 238, w: 300, h: 88 },
  'channel-connector': { x: 700, y: 374, w: 210, h: 68 },
  'state-reader': { x: 700, y: 452, w: 210, h: 68 },
  'data-spine': { x: 70, y: 584, w: 580, h: 88 },
  redis: { x: 690, y: 588, w: 250, h: 78 },
}

const PLANES = [
  { id: 'edge', labelKey: 'admin.planeEdge', color: 'var(--orange)', x: 0, y: 0, w: 1000, h: 168, lx: 982, ly: 24, anchor: 'end' },
  { id: 'control', labelKey: 'admin.planeControl', color: 'var(--purple)', x: 0, y: 168, w: 496, h: 400, lx: 16, ly: 188, anchor: 'start' },
  { id: 'tenant', labelKey: 'admin.planeTenantRuntime', color: 'var(--green)', x: 504, y: 168, w: 496, h: 400, lx: 984, ly: 188, anchor: 'end' },
  { id: 'data', labelKey: 'admin.planeData', color: 'var(--cyan)', x: 0, y: 568, w: 1000, h: 132, lx: 982, ly: 690, anchor: 'end' },
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
const displayText = (v, t) => {
  if (v == null) return v
  let text = String(v).replaceAll('0↔1', '0 ↔ 1')
  if (t) {
    text = text
      .replace(/\bplanned\b/g, t('admin.topologyPlanned'))
      .replace(/\bphase\b/g, t('admin.topologyPhase'))
  }
  return text
}

// --- Edge routing: verified ZERO-CROSSING waypoint lanes, rendered as smooth
// rounded-corner paths (curved bends, no crossings). `lab` = label [x,y,anchor];
// `mid` = ✕ marker anchor. ---
const EDGE_ROUTE = {
  'browser|agentgateway|byte': { pts: [[500, 74], [500, 104]], mid: [500, 89], lab: [522, 92, 'start'] },
  'agentgateway|control-panel|byte': { pts: [[150, 166], [150, 210]], mid: [150, 188], lab: [158, 188, 'start'] },
  'agentgateway|control-panel|decision': { pts: [[330, 166], [330, 210]], mid: [330, 188], lab: [338, 188, 'start'] },
  'agentgateway|agent-runner|byte': { pts: [[720, 166], [720, 238]], mid: [720, 204], lab: [728, 204, 'start'] },
  'control-panel|operator|control': { pts: [[162, 326], [162, 416]], mid: [162, 374], lab: [170, 376, 'start'] },
  'operator|agent-runner|control': { pts: [[276, 416], [276, 400], [560, 400], [560, 290], [630, 290]], mid: [418, 400], lab: [418, 392, 'middle'] },
  'control-panel|data-spine|grpc': { pts: [[70, 326], [70, 584]], mid: [70, 470], lab: [58, 470, 'end'] },
  'operator|data-spine|grpc': { pts: [[196, 492], [196, 584]], mid: [196, 538], lab: [204, 538, 'start'] },
  'agent-runner|data-spine|grpc': { pts: [[645, 326], [645, 584]], mid: [645, 456], lab: [653, 452, 'start'] },
  'channel-connector|agent-runner|control': { pts: [[805, 374], [805, 326]], mid: [805, 350] },
  'scheduler|operator|control': { pts: [[300, 462], [276, 462]], mid: [288, 462] },
  'scheduler|agent-runner|control': { pts: [[464, 462], [600, 462], [600, 300], [630, 300]], mid: [600, 382] },
  'data-spine|redis|grpc': { pts: [[650, 628], [690, 628]], mid: [670, 628] },
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
  if (edge.kind === 'decision') return { stroke: 'var(--purple)', width: 2, dash: '0.1 6', opacity: 0.72 }
  if (edge.kind === 'grpc') return { stroke: 'var(--cyan)', width: 1.4, dash: '6 5', opacity: 0.58 }
  return { stroke: 'var(--border-strong)', width: 1.4, dash: null, opacity: 0.85 } // control
}

function metricLine(node, t) {
  const m = node.metrics || {}
  switch (node.id) {
    case 'agentgateway':
      return node.status === 'up' ? t('admin.metricConnections', { count: fmt(m.connections) }) : null
    case 'operator':
      return t('admin.metricReady', { ready: fmt(m.ready), desired: fmt(m.desired) })
    case 'data-spine': {
      const parts = []
      if ('accounts' in m) parts.push(t('admin.metricAccounts', { count: fmt(m.accounts) }))
      if ('jobs' in m) parts.push(t('admin.metricJobs', { count: fmt(m.jobs) }))
      if ('runs' in m) parts.push(t('admin.metricRuns', { count: fmt(m.runs) }))
      return parts.join(' · ') || null
    }
    case 'agent-runner':
      return t('admin.metricRunner', { awake: fmt(m.awake), running: fmt(m.running), total: fmt(m.total) })
    default:
      return null
  }
}

function edgeLabelBox(x, y, anchor, label) {
  const width = Math.max(42, label.length * 5.9 + 14)
  const rectX = anchor === 'end' ? x - width + 6 : anchor === 'middle' ? x - width / 2 : x - 6
  return { x: rectX, y: y - 13, width, height: 18 }
}

function flowParticleCount(edge) {
  return edge.bytepath ? PARTICLES_PER_EDGE : 2
}

function flowParticleColor(edge) {
  if (edge.kind === 'byte') return 'var(--cyan)'
  if (edge.kind === 'decision') return 'var(--purple)'
  if (edge.kind === 'grpc') return 'var(--cyan)'
  return 'var(--yellow)'
}

function flowParticleRadius(edge) {
  return edge.bytepath ? 2.6 : 2.2
}

function NodeView({ node, reducedMotion, t }) {
  const box = NODE_BOX[node.id]
  if (!box) return null
  const disabled = node.status === 'disabled'
  const color = STATUS_COLOR[node.status] || 'var(--status-idle)'
  const metric = metricLine(node, t)
  const wide = node.id === 'agentgateway' || node.id === 'data-spine'
  const isCP = node.id === 'control-panel'
  const isStack = node.id === 'agent-runner'
  const sub = displayText(node.sub, t)
  const detail = displayText(metric || node.detail, t)
  const hasDetail = Boolean(detail)
  const planned = disabled && Boolean(node.detail)
  const titleY = box.y + (planned ? 28 : hasDetail ? 26 : 27)
  const subY = titleY + (planned ? 18 : 17)
  const detailY = subY + (planned ? 18 : 17)
  const metricY = wide
    ? node.id === 'agentgateway' ? titleY + 18 : box.y + box.h - 20
    : hasDetail ? detailY : node.sub ? subY + 17 : titleY + 17
  const detailChipW = planned
    ? Math.min(box.w - 32, Math.max(112, (detail?.length || 0) * 6.2 + 16))
    : 0

  return (
    <g style={{ opacity: disabled ? 0.58 : 1 }}>
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
      <text x={box.x + 16} y={titleY} fontSize={14} fontWeight={600} fill="var(--text-primary)">
        {node.label}
      </text>
      {/* Plane tag (wide bars) on the right */}
      {wide && (
        <text x={box.x + box.w - 16} y={titleY} fontSize={11} textAnchor="end"
          fill={node.plane === 'edge' ? 'var(--orange)' : 'var(--cyan)'}
          style={{ letterSpacing: '0.06em' }}>
          {node.plane === 'edge' ? t('admin.planeEdge') : t('admin.planeData')}
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
          {sub && (
            <text x={box.x + 16} y={subY} fontSize={10.5} fill="var(--text-dim)">{sub}</text>
          )}
          {planned && detail ? (
            <g>
              <rect x={box.x + 16} y={detailY - 13} width={detailChipW} height={18} rx={2}
                fill="var(--bg-elevated)" stroke="var(--border-subtle)" strokeWidth={1} />
              <text x={box.x + 24} y={detailY} fontSize={10}
                fill="var(--text-secondary)" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                {detail}
              </text>
            </g>
          ) : detail && (
            <text x={wide ? box.x + box.w - 16 : box.x + 16} y={metricY}
              textAnchor={wide ? 'end' : 'start'} fontSize={10.5}
              fill={disabled ? 'var(--text-dim)' : 'var(--text-secondary)'}
              style={{ fontFamily: "'JetBrains Mono', monospace" }}>
              {detail}
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
  const { t } = useTranslation()
  const nodes = data?.nodes || []
  const edges = data?.edges || []

  // Resolve each edge to its zero-crossing lane, rendered as a smooth rounded path.
  const routedEdges = useMemo(() => edges.map((e) => {
    const key = edgeKey(e)
    const r = EDGE_ROUTE[key] || fallbackRoute(e)
    if (!r) return null
    return { ...e, key, d: roundedPath(r.pts, CORNER_R), mid: r.mid, lab: r.lab }
  }).filter(Boolean), [edges])

  const flowEdges = useMemo(() => routedEdges.filter((e) => !e.disabled), [routedEdges])

  const pathRefs = useRef({})       // edgeKey → <path> element (animated implemented edges)
  const particleRefs = useRef({})   // `${edgeKey}#${i}` → <circle> element

  // Re-run the anime.js effect only when the animated set or reduced-motion changes
  // (NOT on every 5s poll).
  const flowSig = useMemo(
    () => flowEdges.map((e) => `${e.key}:${e.healthy ? 1 : 0}:${flowParticleCount(e)}`).join(',') + `|rm:${reducedMotion ? 1 : 0}`,
    [flowEdges, reducedMotion],
  )

  useEffect(() => {
    const anims = []
    for (const e of flowEdges) {
      const live = !reducedMotion && e.healthy
      const count = flowParticleCount(e)
      for (let i = 0; i < count; i++) {
        const el = particleRefs.current[`${e.key}#${i}`]
        if (el) el.style.opacity = live ? '1' : '0'
      }
      if (!live) continue
      const pathEl = pathRefs.current[e.key]
      if (!pathEl) continue
      let motionPath
      try { motionPath = svg.createMotionPath(pathEl) } catch { continue }
      for (let i = 0; i < count; i++) {
        const el = particleRefs.current[`${e.key}#${i}`]
        if (!el) continue
        anims.push(animate(el, {
          translateX: motionPath.translateX,
          translateY: motionPath.translateY,
          duration: PARTICLE_MS,
          delay: (PARTICLE_MS / count) * i,
          ease: 'linear',
          loop: true,
        }))
      }
    }
    return () => { for (const a of anims) { try { a.revert ? a.revert() : a.pause?.() } catch { /* noop */ } } }
  }, [flowSig]) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="grid w-full gap-3" style={{ minWidth: 0, gridTemplateColumns: 'minmax(0, 1fr) 210px' }}>
      <div className="min-w-0">
        <svg
        viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
        width="100%"
        preserveAspectRatio="xMidYMid meet"
        style={{ display: 'block', width: '100%', height: 'auto' }}
        role="img"
        aria-label={t('admin.systemTopologyAria')}
      >
        {/* Plane bands */}
        {PLANES.map((p) => (
          <g key={p.id}>
            <rect x={p.x} y={p.y} width={p.w} height={p.h} fill="var(--bg-elevated)" opacity={0.28} />
            <rect x={p.x + 4} y={p.y + 4} width={p.w - 8} height={p.h - 8} rx={4}
              fill="none" stroke={p.color} strokeWidth={1} strokeDasharray="6 6" opacity={0.42} />
            <text x={p.lx} y={p.ly} textAnchor={p.anchor} fontSize={11} fill={p.color}
              style={{ letterSpacing: '0.14em', fontWeight: 600 }}>
              {t(p.labelKey)}
            </text>
          </g>
        ))}

        {/* Edges */}
        {routedEdges.map((e) => {
          const v = edgeVisual(e)
          return (
            <path key={e.key}
              ref={!e.disabled ? (el) => { pathRefs.current[e.key] = el } : undefined}
              d={e.d} fill="none" stroke={v.stroke} strokeWidth={v.width}
              strokeDasharray={v.dash || undefined} strokeLinecap="round" strokeLinejoin="round"
              opacity={v.opacity} />
          )
        })}

        {/* Edge labels (skip planned) */}
        {routedEdges.map((e) => {
          if (e.disabled || !e.label) return null
          const label = displayText(e.label, t)
          const x = e.lab ? e.lab[0] : e.mid[0]
          const y = e.lab ? e.lab[1] : e.mid[1] - 6
          const anchor = e.lab ? e.lab[2] : 'middle'
          const labelBox = edgeLabelBox(x, y, anchor, label)
          return (
            <g key={`l-${e.key}`}>
              <rect x={labelBox.x} y={labelBox.y} width={labelBox.width} height={labelBox.height} rx={2}
                fill="var(--bg-base)" stroke="var(--border-subtle)" strokeWidth={1} opacity={0.92} />
              <text x={x} y={y} textAnchor={anchor} fontSize={9}
                fill="var(--text-secondary)" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
                {label}
              </text>
            </g>
          )
        })}

        {/* Flow particles (constant flow when healthy; hidden otherwise).
            Drawn before nodes so they're occluded as they enter a module. */}
        {flowEdges.map((e) => (
          <g key={`p-${e.key}`}>
            {Array.from({ length: flowParticleCount(e) }).map((_, i) => (
              <circle key={i} ref={(el) => { particleRefs.current[`${e.key}#${i}`] = el }}
                cx={0} cy={0} r={flowParticleRadius(e)} fill={flowParticleColor(e)} style={{ opacity: 0 }} />
            ))}
          </g>
        ))}

        {/* Nodes */}
        {nodes.map((n) => <NodeView key={n.id} node={n} reducedMotion={reducedMotion} t={t} />)}

        {/* ✕ markers for unreachable (non-disabled, unhealthy) edges */}
        {routedEdges.map((e) => (
          !e.disabled && !e.healthy ? <XMark key={`x-${e.key}`} x={e.mid[0]} y={e.mid[1]} /> : null
        ))}
        </svg>
      </div>

      {/* Legend */}
      <aside className="flex flex-col items-start gap-2" aria-label={t('admin.topologyLegend')}
        style={{ minWidth: 0, paddingLeft: 12, borderLeft: '1px solid var(--border-subtle)' }}>
        {[
          { c: 'var(--green)', labelKey: 'admin.statusUp' },
          { c: 'var(--status-pending)', labelKey: 'admin.statusDegraded' },
          { c: 'var(--red)', labelKey: 'admin.statusDown' },
          { c: 'var(--cyan)', labelKey: 'admin.statusIdle' },
          { c: 'var(--status-idle)', labelKey: 'admin.statusDisabled' },
        ].map((s) => (
          <span key={s.labelKey} className="flex items-center gap-2 uppercase"
            style={{ fontSize: 11, color: 'var(--text-secondary)', letterSpacing: '0.06em' }}>
            <span style={{ width: 12, height: 0, borderTop: `2px solid ${s.c}` }} />
            {t(s.labelKey)}
          </span>
        ))}
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="22" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="22" y2="4" stroke="var(--cyan)" strokeWidth="2" opacity="0.4" />
            <circle cx="14" cy="4" r="2.4" fill="var(--cyan)" />
          </svg>
          {t('admin.legendBytePath')}
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="22" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="22" y2="4" stroke="var(--purple)" strokeWidth="2" strokeDasharray="0.1 6" strokeLinecap="round" opacity="0.72" />
            <circle cx="14" cy="4" r="2.1" fill="var(--purple)" />
          </svg>
          {t('admin.legendDecisionPath')}
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="22" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="22" y2="4" stroke="var(--border-strong)" strokeWidth="1.4" />
          </svg>
          {t('admin.legendControlPath')}
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="22" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="22" y2="4" stroke="var(--cyan)" strokeWidth="1.4" strokeDasharray="6 5" opacity="0.58" />
            <circle cx="14" cy="4" r="2.1" fill="var(--cyan)" />
          </svg>
          {t('admin.legendDataPath')}
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-secondary)' }}>
          <svg width="14" height="14" aria-hidden="true">
            <circle cx="7" cy="7" r="6" fill="var(--bg-base)" stroke="var(--red)" strokeWidth="1" />
            <path d="M4.5,4.5 L9.5,9.5 M9.5,4.5 L4.5,9.5" stroke="var(--red)" strokeWidth="1.4" strokeLinecap="round" />
          </svg>
          {t('admin.legendUnreachable')}
        </span>
        <span className="flex items-center gap-2" style={{ fontSize: 11, color: 'var(--text-dim)' }}>
          <span style={{ width: 14, height: 0, borderTop: '1.3px dashed var(--status-idle)' }} />
          {t('admin.legendPlanned')}
        </span>
      </aside>
    </div>
  )
}
