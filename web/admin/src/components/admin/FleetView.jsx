import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { useVirtualizer } from '@tanstack/react-virtual'
import { Server, Activity, ArrowRightLeft, RotateCw, Power } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { animate, svg as animeSvg, utils as animeUtils } from 'animejs'
import Dropdown from '@shared/components/shared/Dropdown'
import useUiStore from '@shared/stores/uiStore'
import { useAnimatedNumber } from '@shared/motion/useAnimatedNumber'
import { useStaggerEntrance } from '@shared/motion/useStaggerEntrance'
import { useStatusColorTween } from '@shared/motion/useStatusSettle'
import { useReducedMotion } from '@shared/motion/useReducedMotion'
import useAdminStore from '../../stores/adminStore'
import LiveToggleButton from './LiveToggleButton'

// Fleet — Dashboard overview. Live agent-runner snapshot from /api/admin/fleet:
// two summary tiles (awake sandboxes, running sessions) + a per-account table.
// Polls every POLL_MS; the skeleton only shows on the first load — background
// polls update in place. Status is shown via a 2px left border, never dots.

const POLL_MS = 5000

// Selectable trailing windows for the gateway headline count. Values in seconds.
const WINDOW_OPTIONS = [
  { value: 30, labelKey: 'admin.windowLast30s' },
  { value: 60, labelKey: 'admin.windowLast1m' },
  { value: 300, labelKey: 'admin.windowLast5m' },
  { value: 900, labelKey: 'admin.windowLast15m' },
]

// Destination scope. The metric has no URL-path label; the backend split is the only
// path-ish dimension — control-plane (control-panel face) vs agent-runtime (the pool).
const SCOPE_OPTIONS = [
  { value: 'all', labelKey: 'admin.scopeAll' },
  { value: 'control-panel', labelKey: 'admin.scopeControlPlane' },
  { value: 'agent-runner', labelKey: 'admin.scopeAgentRuntime' },
]

// Derive the rolling-window request count, current req/s, and the req/s sparkline for the
// selected scope from the per-destination cumulative buffer (server-clock, no browser-skew).
//  - val() picks the scope's cumulative series (total / control-panel / agent-runner).
//  - count = val(latest) − val(at-or-before latest.t − windowSec); clamped ≥ 0.
//  - full  = buffer actually spans the window yet (else count is "so far", still rising).
function deriveGateway(buffer, windowSec, scope) {
  if (!buffer || buffer.length === 0) return { count: null, rate: null, spark: [], full: false }
  const val = scope === 'control-panel' ? (s) => s.cp
    : scope === 'agent-runner' ? (s) => s.ar
    : (s) => s.total
  const latest = buffer[buffer.length - 1]
  const cutoff = latest.t - windowSec
  let base = buffer[0]
  for (let i = buffer.length - 1; i >= 0; i--) {
    if (buffer[i].t <= cutoff) { base = buffer[i]; break }
  }
  const count = Math.max(0, val(latest) - val(base))
  const full = buffer[0].t <= cutoff
  let rate = null
  const spark = []
  for (let i = 1; i < buffer.length; i++) {
    const dt = buffer[i].t - buffer[i - 1].t
    spark.push(dt > 0 ? Math.max(0, (val(buffer[i]) - val(buffer[i - 1])) / dt) : 0)
  }
  if (spark.length) rate = spark[spark.length - 1]
  return { count, rate, spark: spark.slice(-40), full }
}

// State derived from the operator phase + the pod's in-flight runs. Colors come
// from the design-spec status palette (running=purple, online=green, waking=yellow,
// idle=neutral border) and drive both the row's left border and the label.
function stateOf(acct) {
  if (acct.phase === 'Waking') return { labelKey: 'admin.stateWaking', color: 'var(--status-pending)' }
  if (acct.awake) {
    if ((acct.active_runs || 0) > 0) return { labelKey: 'admin.stateRunning', color: 'var(--status-running)' }
    return { labelKey: 'admin.stateOnline', color: 'var(--green)' }
  }
  return { labelKey: 'admin.stateIdle', color: 'var(--status-idle)' }
}

function relTime(ts, t) {
  if (!ts) return t('admin.never')
  const secs = Math.max(0, Math.floor(Date.now() / 1000 - ts))
  if (secs < 5) return t('admin.justNow')
  if (secs < 60) return t('admin.secondsAgo', { count: secs })
  const mins = Math.floor(secs / 60)
  if (mins < 60) return t('admin.minutesAgo', { count: mins })
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return t('admin.hoursAgo', { count: hrs })
  return t('admin.daysAgo', { count: Math.floor(hrs / 24) })
}

// Memoized so the 1s relative-time tick only re-renders rows whose derived
// labels actually changed — all row props are primitives except `account`,
// whose identity is stable between 5s polls.
const FleetRow = memo(function FleetRow({ account, stateColor, stateLabel, stateIdle, runs, lastActivity, onRestart, onShutdown, entranceKey, entranceRef }) {
  const { t } = useTranslation()
  const a = account
  // T3: live state changes blend the 2px status border over 150ms.
  const borderTweenRef = useStatusColorTween(stateColor)
  return (
    <div
      // Entrance animates this inner div — the virtualizer shell must not move.
      ref={(el) => {
        borderTweenRef.current = el
        if (entranceRef) entranceRef(entranceKey)(el)
      }}
      className="flex items-center gap-3 px-4 py-2"
      style={{
        borderBottom: '1px solid var(--border-subtle)',
        borderLeft: `2px solid ${stateColor}`,
      }}
    >
      <span className="flex flex-col flex-1 min-w-0">
        <span className="text-sm font-semibold truncate" style={{ color: 'var(--text-primary)' }}>
          {a.username || a.account_id}
        </span>
        {a.username && (
          <span className="text-xs truncate" style={{ color: 'var(--text-dim)', fontWeight: 300, fontFamily: "'JetBrains Mono', monospace" }}>
            {a.account_id}
          </span>
        )}
      </span>
      <span
        className="uppercase"
        style={{ width: 80, flexShrink: 0, fontSize: 11, fontWeight: 600, letterSpacing: '0.06em', color: stateIdle ? 'var(--text-dim)' : stateColor }}
      >
        {stateLabel}
      </span>
      <span
        className="text-sm"
        style={{ width: 88, flexShrink: 0, textAlign: 'right', color: a.active_runs ? 'var(--text-primary)' : 'var(--text-dim)', fontFamily: "'JetBrains Mono', monospace" }}
      >
        {runs}
      </span>
      <span className="text-xs font-light" style={{ width: 96, flexShrink: 0, textAlign: 'right', color: 'var(--text-dim)' }}>
        {lastActivity}
      </span>
      {/* Actions: restart (left) always; shut down (right) only when there's a
          live pod to stop. The 72px slot is fixed so idle rows don't shift. */}
      <span className="flex items-center flex-shrink-0" style={{ width: 72 }}>
        <button
          className="flex items-center justify-center flex-shrink-0"
          style={{ width: 36, background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4, transition: 'color 150ms ease' }}
          onClick={() => onRestart(a)}
          onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
          onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
          title={t('admin.restartPodTitle')}
          aria-label={t('admin.restartPodTitle')}
        >
          <RotateCw size={14} strokeWidth={1.5} />
        </button>
        {(a.awake || a.phase === 'Waking') ? (
          <button
            className="flex items-center justify-center flex-shrink-0"
            style={{ width: 36, background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 4, transition: 'color 150ms ease' }}
            onClick={() => onShutdown(a)}
            onMouseEnter={(e) => { e.currentTarget.style.color = 'var(--red)' }}
            onMouseLeave={(e) => { e.currentTarget.style.color = 'var(--text-dim)' }}
            title={t('admin.shutdownTitle')}
            aria-label={t('admin.shutdownTitle')}
          >
            <Power size={14} strokeWidth={1.5} />
          </button>
        ) : (
          <span style={{ width: 36, flexShrink: 0 }} />
        )}
      </span>
    </div>
  )
})

function Tile({ icon: Icon, label, value, suffix }) {
  // Poll updates tween to the new value; first paint and non-numeric snap.
  const isNumeric = typeof value === 'number' && Number.isFinite(value)
  const animatedValue = useAnimatedNumber(isNumeric ? value : 0)
  const shownValue = isNumeric ? animatedValue : value
  return (
    <div
      className="flex flex-col gap-2"
      style={{
        flex: 1,
        minWidth: 0,
        background: 'var(--bg-surface)',
        border: '1px solid var(--border)',
        borderRadius: 4,
        padding: '16px 18px',
      }}
    >
      <div className="flex items-center gap-2 uppercase" style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}>
        <Icon size={14} strokeWidth={1.5} className="flex-shrink-0" />
        <span className="truncate">{label}</span>
      </div>
      <div className="flex items-baseline gap-1">
        <span className="font-bold" style={{ color: 'var(--text-primary)', fontSize: 28, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>{shownValue}</span>
        {suffix != null && (
          <span style={{ color: 'var(--text-dim)', fontSize: 16, fontWeight: 300 }}>/ {suffix}</span>
        )}
      </div>
    </div>
  )
}

// Inline SVG sparkline of recent req/s samples — no chart lib (design-spec minimal).
// Reserves its footprint until two samples exist so the tile doesn't reflow on fill-in.
// T7: the first data traces the line in once; later polls morph the points to
// their new shape with a single 200ms settle (matched point-counts only —
// while the window is still filling, shapes just snap). Never loops.
function Sparkline({ data, width = 76, height = 24 }) {
  const polyRef = useRef(null)
  const tracedRef = useRef(false)
  const reducedMotion = useReducedMotion()
  const has = !!data && data.length >= 2
  let pts = ''
  if (has) {
    const max = Math.max(...data)
    const min = Math.min(...data)
    const range = max - min || 1
    const n = data.length
    pts = data
      .map((v, i) => {
        const x = (i / (n - 1)) * (width - 2) + 1
        const y = height - 2 - ((v - min) / range) * (height - 4)
        return `${x.toFixed(1)},${y.toFixed(1)}`
      })
      .join(' ')
  }

  useLayoutEffect(() => {
    const el = polyRef.current
    if (!el || !pts) return
    const prev = el.getAttribute('points') || ''
    if (reducedMotion) {
      el.setAttribute('points', pts)
      tracedRef.current = true
      return
    }
    if (!tracedRef.current) {
      // First data: set the shape, then draw it in once.
      tracedRef.current = true
      el.setAttribute('points', pts)
      const [drawable] = animeSvg.createDrawable(el)
      animeUtils.set(drawable, { draw: '0 0' })
      animate(drawable, { draw: '0 1', duration: 300, ease: 'out(2)' })
      return
    }
    const prevCount = (prev.match(/-?[\d.]+/g) || []).length
    const nextCount = (pts.match(/-?[\d.]+/g) || []).length
    if (prev === pts) return
    if (prevCount !== nextCount) {
      el.setAttribute('points', pts) // window still filling — snap
      return
    }
    animate(el, { points: pts, duration: 200, ease: 'out(2)' })
  }, [pts, reducedMotion])

  if (!has) return <div style={{ width, height, flexShrink: 0 }} />
  return (
    <svg width={width} height={height} style={{ display: 'block', flexShrink: 0 }} aria-hidden="true">
      {/* points is written imperatively (initial set + morphs) so React's
          re-renders never snap the shape mid-tween */}
      <polyline ref={polyRef} fill="none" stroke="var(--cyan)" strokeWidth={1.5}
        strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  )
}

// Gateway-traffic tile: requests received over the selected trailing window (headline)
// + a req/s sparkline and the current rate. The window is chosen via a compact dropdown
// in the header (persisted). '—' when no reachable gateway pod. ``full`` is false while
// the sample buffer hasn't spanned the window yet — the count is then "so far", marked ~.
function GatewayTile({ available, count, rate, spark, full, windowSec, onWindowChange, scope, onScopeChange }) {
  const { t } = useTranslation()
  const windowOptions = WINDOW_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))
  const scopeOptions = SCOPE_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))
  const animatedCount = useAnimatedNumber(count ?? 0)

  return (
    <div
      className="flex flex-col gap-2"
      style={{
        flex: 1, minWidth: 0, background: 'var(--bg-surface)',
        border: '1px solid var(--border)', borderRadius: 4, padding: '16px 18px',
      }}
    >
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2 uppercase min-w-0" style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, letterSpacing: '0.06em' }}>
          <ArrowRightLeft size={14} strokeWidth={1.5} className="flex-shrink-0" />
          <span className="truncate">{t('admin.gateway')}</span>
        </div>
        <div className="flex items-center gap-1 flex-shrink-0">
          <Dropdown
            size="sm"
            align="right"
            value={windowSec}
            onChange={onWindowChange}
            options={windowOptions}
            ariaLabel={t('admin.gatewayWindowAria')}
            title={t('admin.gatewayWindowTitle')}
            minMenuWidth={120}
            maxTriggerWidth={96}
          />
          <Dropdown
            size="sm"
            align="right"
            value={scope}
            onChange={onScopeChange}
            options={scopeOptions}
            ariaLabel={t('admin.gatewayScopeAria')}
            title={t('admin.gatewayScopeTitle')}
            minMenuWidth={150}
            maxTriggerWidth={120}
          />
        </div>
      </div>
      {!available ? (
        <span className="font-bold" style={{ color: 'var(--text-dim)', fontSize: 28, lineHeight: 1 }}>—</span>
      ) : (
        <>
          <div className="flex items-center justify-between gap-3">
            <span className="font-bold" style={{ color: 'var(--text-primary)', fontSize: 28, lineHeight: 1, fontVariantNumeric: 'tabular-nums' }}>
              {count == null ? '—' : `${full ? '' : '~'}${animatedCount.toLocaleString()}`}
            </span>
            <Sparkline data={spark} />
          </div>
          <span style={{ color: 'var(--text-dim)', fontSize: 12, fontWeight: 300, fontFamily: "'JetBrains Mono', monospace" }}>
            {rate == null ? t('admin.reqPerSecEmpty') : t('admin.reqPerSec', { rate: rate.toFixed(rate < 10 ? 1 : 0) })}
          </span>
        </>
      )}
    </div>
  )
}

function TilesSkeleton() {
  return (
    <div className="flex gap-3">
      {[1, 2, 3].map((i) => (
        <div key={i} style={{ flex: 1, background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '16px 18px' }}>
          <div className="skeleton" style={{ width: 120, height: 11, marginBottom: 12 }} />
          <div className="skeleton" style={{ width: 64, height: 28 }} />
        </div>
      ))}
    </div>
  )
}

function TableSkeleton() {
  return (
    <div className="flex flex-col">
      {[1, 2, 3, 4].map((i) => (
        <div key={i} className="flex items-center gap-3 px-4 py-3" style={{ borderLeft: '2px solid transparent' }}>
          <div className="skeleton flex-1" style={{ height: 13, maxWidth: 180 }} />
          <div className="skeleton" style={{ width: 64, height: 13 }} />
          <div className="skeleton" style={{ width: 28, height: 13 }} />
          <div className="skeleton" style={{ width: 72, height: 13 }} />
        </div>
      ))}
    </div>
  )
}

export default function FleetView() {
  const { t } = useTranslation()
  const rowEntranceRef = useStaggerEntrance()
  const fleet = useAdminStore((s) => s.fleet)
  const fleetLoading = useAdminStore((s) => s.fleetLoading)
  const fleetRefreshing = useAdminStore((s) => s.fleetRefreshing)
  const fleetError = useAdminStore((s) => s.fleetError)
  const fetchFleet = useAdminStore((s) => s.fetchFleet)
  const restartAccountPod = useAdminStore((s) => s.restartAccountPod)
  const shutdownAccountRunner = useAdminStore((s) => s.shutdownAccountRunner)
  const showConfirmDialog = useUiStore((s) => s.showConfirmDialog)
  const gateway = useAdminStore((s) => s.gateway)
  const gatewayBuffer = useAdminStore((s) => s.gatewayBuffer)
  const gatewayWindowSec = useAdminStore((s) => s.gatewayWindowSec)
  const setGatewayWindowSec = useAdminStore((s) => s.setGatewayWindowSec)
  const gatewayScope = useAdminStore((s) => s.gatewayScope)
  const setGatewayScope = useAdminStore((s) => s.setGatewayScope)
  const fetchGateway = useAdminStore((s) => s.fetchGateway)
  const [liveEnabled, setLiveEnabled] = useState(true)

  // Re-render once a second so relative timestamps ("12s ago") stay fresh between polls.
  const [, setTick] = useState(0)
  const fetchRef = useRef({ fleet: fetchFleet, gateway: fetchGateway })
  fetchRef.current = { fleet: fetchFleet, gateway: fetchGateway }

  useEffect(() => {
    if (!liveEnabled) return undefined
    const poll = () => { fetchRef.current.fleet(); fetchRef.current.gateway() }
    poll()
    const pid = setInterval(poll, POLL_MS)
    return () => clearInterval(pid)
  }, [liveEnabled])

  useEffect(() => {
    const tid = setInterval(() => setTick((n) => n + 1), 1000)
    return () => clearInterval(tid)
  }, [])

  const accounts = fleet?.accounts || []
  const initialLoad = fleetLoading && !fleet
  const handleLiveToggle = () => {
    if (fleetError) {
      setLiveEnabled(true)
      fetchFleet()
      fetchGateway()
      return
    }
    setLiveEnabled((enabled) => {
      const next = !enabled
      if (next) {
        fetchFleet()
        fetchGateway()
      }
      return next
    })
  }

  const handleRestart = useCallback((a) => {
    showConfirmDialog({
      title: t('admin.restartPodTitle'),
      message: t('admin.restartPodMessage', { name: a.username || a.account_id }),
      confirmLabel: t('admin.restartPodConfirm'),
      danger: true,
      onConfirm: async () => {
        try {
          await restartAccountPod(a.account_id)
        } catch (e) {
          console.error(e)
        }
      },
    })
  }, [showConfirmDialog, t])

  const handleShutdown = useCallback((a) => {
    showConfirmDialog({
      title: t('admin.shutdownTitle'),
      message: t('admin.shutdownMessage', { name: a.username || a.account_id }),
      confirmLabel: t('admin.shutdownConfirm'),
      danger: true,
      onConfirm: async () => {
        try {
          await shutdownAccountRunner(a.account_id)
        } catch (e) {
          console.error(e)
        }
      },
    })
  }, [showConfirmDialog, t])

  const fleetScrollRef = useRef(null)
  const fleetRowsRef = useRef(null)
  const [fleetRowsMargin, setFleetRowsMargin] = useState(0)

  // Account rows sit below the tiles inside the same scroll pane — measure
  // that offset into scrollMargin.
  useLayoutEffect(() => {
    const scrollEl = fleetScrollRef.current
    const rowsEl = fleetRowsRef.current
    if (!scrollEl || !rowsEl) return
    setFleetRowsMargin(
      rowsEl.getBoundingClientRect().top - scrollEl.getBoundingClientRect().top + scrollEl.scrollTop
    )
  }, [initialLoad, accounts.length])

  const fleetVirtualizer = useVirtualizer({
    count: accounts.length,
    getScrollElement: () => fleetScrollRef.current,
    estimateSize: () => 46,
    overscan: 12,
    scrollMargin: fleetRowsMargin,
    getItemKey: (i) => accounts[i].account_id,
  })

  return (
    <div className="flex flex-col" style={{ height: '100%', overflow: 'hidden' }}>
      {/* Header */}
      <div className="flex items-center justify-between flex-shrink-0" style={{ padding: '20px var(--admin-section-x) 0 var(--admin-section-x)' }}>
        <div>
          <h2 className="font-semibold text-lg" style={{ color: 'var(--text-primary)', margin: 0 }}>{t('admin.fleetTitle')}</h2>
          <p className="text-xs" style={{ color: 'var(--text-dim)', marginTop: 4 }}>
            {t('admin.fleetDescription')}
          </p>
        </div>
        <LiveToggleButton
          active={liveEnabled && !fleetError}
          error={!!fleetError}
          refreshing={fleetRefreshing}
          onClick={handleLiveToggle}
          spinAnimation="fleet-spin 1s linear infinite"
        />
      </div>

      {/* Body */}
      <div ref={fleetScrollRef} className="flex-1 overflow-y-auto" style={{ padding: '16px var(--admin-section-x) 24px var(--admin-section-x)' }}>
        {/* Tiles */}
        {initialLoad ? (
          <TilesSkeleton />
        ) : (
          <div className="flex gap-3">
            <Tile icon={Server} label={t('admin.awakeSandboxes')} value={fleet?.awake_sandboxes ?? 0} suffix={fleet?.total_accounts ?? 0} />
            <Tile icon={Activity} label={t('admin.runningSessions')} value={fleet?.running_sessions ?? 0} />
            <GatewayTile
              available={!!gateway?.available}
              {...deriveGateway(gatewayBuffer, gatewayWindowSec, gatewayScope)}
              windowSec={gatewayWindowSec}
              onWindowChange={setGatewayWindowSec}
              scope={gatewayScope}
              onScopeChange={setGatewayScope}
            />
          </div>
        )}

        {/* Per-account table */}
        <div style={{ marginTop: 20, border: '1px solid var(--border)', borderRadius: 4, overflow: 'hidden' }}>
          {/* Header row */}
          <div
            className="flex items-center gap-3 px-4 py-2 text-xs uppercase"
            style={{
              background: 'var(--bg-surface)',
              color: 'var(--text-secondary)',
              letterSpacing: '0.06em',
              borderBottom: '1px solid var(--border)',
            }}
          >
            <span className="flex-1 min-w-0">{t('admin.account')}</span>
            <span style={{ width: 80, flexShrink: 0 }}>{t('admin.state')}</span>
            <span style={{ width: 88, flexShrink: 0, textAlign: 'right' }}>{t('admin.runs')}</span>
            <span style={{ width: 96, flexShrink: 0, textAlign: 'right' }}>{t('admin.lastActivity')}</span>
            <span style={{ width: 72, flexShrink: 0 }} />
          </div>

          {initialLoad ? (
            <TableSkeleton />
          ) : accounts.length === 0 ? (
            <div className="flex items-center justify-center" style={{ padding: '32px 16px', color: 'var(--text-dim)', fontSize: 13 }}>
              {t('admin.noAgentRunnerAccounts')}
            </div>
          ) : (
            <div
              ref={fleetRowsRef}
              style={{ height: fleetVirtualizer.getTotalSize(), position: 'relative', width: '100%' }}
            >
              {fleetVirtualizer.getVirtualItems().map((vi) => {
                const a = accounts[vi.index]
                const st = stateOf(a)
                return (
                  <div
                    key={vi.key}
                    data-index={vi.index}
                    ref={fleetVirtualizer.measureElement}
                    style={{ position: 'absolute', top: vi.start - fleetRowsMargin, left: 0, width: '100%' }}
                  >
                    <FleetRow
                      account={a}
                      stateColor={st.color}
                      stateLabel={t(st.labelKey)}
                      stateIdle={st.labelKey === 'admin.stateIdle'}
                      runs={a.active_runs == null ? '—' : a.active_runs}
                      lastActivity={relTime(a.last_activity_ts, t)}
                      onRestart={handleRestart}
                      onShutdown={handleShutdown}
                      entranceKey={vi.key}
                      entranceRef={rowEntranceRef}
                    />
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>

      <style>{`@keyframes fleet-spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }`}</style>
    </div>
  )
}
