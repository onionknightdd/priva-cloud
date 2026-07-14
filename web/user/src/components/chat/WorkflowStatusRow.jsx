import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { CheckCircle, XCircle, Clock, Loader, X, ChevronDown, Workflow as WorkflowIcon } from 'lucide-react'
import { AnimatedChevron } from '@shared/components/shared/Accordion'
import CopyButton from '@shared/components/shared/CopyButton'
import { useStatusSettle } from '@shared/motion/useStatusSettle'
import { RollingInteger } from '../shared/Odometer'
import MarkdownRenderer from '../markdown/MarkdownRenderer'
import hljsCore from 'highlight.js/lib/core'
import jsonLang from 'highlight.js/lib/languages/json'
import useUiStore from '@shared/stores/uiStore'
import useWorkflowStore, { phaseRollup, workflowAgentTotals } from '../../stores/workflowStore'
import { stopActiveStream } from '../../hooks/useSSE'

// Register only the JSON grammar so RESULT/PROMPT previews can be syntax-
// highlighted without pulling in all of highlight.js.
hljsCore.registerLanguage('json', jsonLang)

// ── Shared helpers (re-used by the Canvas two-pane in WorkflowDetailPane) ──

// Copy of SubagentFrame.formatDuration — keep the two in sync.
export function formatDuration(ms) {
  if (!ms) return null
  if (ms < 1000) return `${ms}ms`
  const s = ms / 1000
  if (s < 60) return `${s.toFixed(1)}s`
  const m = Math.floor(s / 60)
  const rs = Math.round(s % 60)
  return `${m}m ${rs}s`
}

export const isTerminal = (s) => s === 'completed' || s === 'failed' || s === 'stopped'

// workflow.status → status token used by the card's left-border accent.
export function rowStatus(status) {
  if (status === 'running') return 'running'
  if (status === 'completed') return 'success'
  if (status === 'failed' || status === 'stopped') return 'error'
  return 'pending'
}

// Live-elapsed: wall-clock since start is the primary source so the 1s ticker
// actually advances the display (usage.duration_ms stops updating after the
// first progress event, which would freeze the elapsed). Falls back to
// duration_ms only when startedAt is missing.
export function elapsedMs(wf) {
  if (wf.startedAt) return (wf.endedAt || Date.now()) - wf.startedAt
  return wf.totals?.durationMs || null
}

// agent.state → row status icon.
export function agentStatusMeta(state) {
  if (state === 'done') return { Icon: CheckCircle, color: 'var(--green)', spin: false }
  if (state === 'error' || state === 'failed') return { Icon: XCircle, color: 'var(--red)', spin: false }
  if (state === 'start' || state === 'progress') return { Icon: Loader, color: 'var(--purple)', spin: true }
  return { Icon: Clock, color: 'var(--text-dim)', spin: false }
}

// phaseRollup status, reconciled with the workflow's terminal state. Honest:
// a phase only reads ✓ when its agents actually finished — never just because
// the workflow as a whole reached `completed`. A never-run / unfinished phase
// shows a dash so a premature/odd completion can't paint fake green checks.
//
// `phaseIndex` lets us avoid a *premature* ✓ while the run is still live: when
// a phase's agents are discovered incrementally (e.g. agent 1 of 2 completes
// before agent 2 is registered) the rollup momentarily reads done===total. We
// only trust that as success once execution has demonstrably moved past this
// phase (a later phase already has agents) — otherwise it stays 'running'.
export function phaseStepStatus(workflow, rollup, phaseIndex) {
  const status = workflow.status
  if (rollup.failed > 0) return 'error'
  if (rollup.status === 'success') {
    if (isTerminal(status)) return 'success'
    const movedPast = phaseIndex != null && workflow.phases.some(
      (p) => p.index > phaseIndex && phaseRollup(workflow, p.index).total > 0,
    )
    return movedPast ? 'success' : 'running'
  }
  if (!isTerminal(status)) return rollup.status            // pending | running
  if (status === 'failed') return 'error'
  return 'pending'                                         // completed/stopped but unfinished
}

// Any agent in the workflow that errored/failed — used to render the top-level
// status honestly (a "completed" workflow with a failed agent isn't a clean ✓).
export function anyAgentFailed(workflow) {
  return workflow.agentOrder.some((i) => {
    const st = workflow.agents[i]?.state
    return st === 'error' || st === 'failed'
  })
}

// The phase the run is currently working on (first with a running agent, else
// first not-yet-settled). Uses phaseStepStatus for the "settled success" test
// so a phase that only *transiently* reads done (incremental agent discovery)
// doesn't make auto-follow jump ahead to an unstarted phase. Returns null once
// every phase is settled-success.
export function activePhaseIndex(workflow) {
  const phases = workflow.phases
  if (!phases.length) return null
  for (const p of phases) {
    if (phaseRollup(workflow, p.index).status === 'running') return p.index
  }
  for (const p of phases) {
    if (phaseStepStatus(workflow, phaseRollup(workflow, p.index), p.index) !== 'success') return p.index
  }
  return null
}

export function statusColor(stStatus) {
  if (stStatus === 'success') return 'var(--green)'
  if (stStatus === 'error') return 'var(--red)'
  if (stStatus === 'running') return 'var(--purple)'
  return 'var(--border)'
}

/**
 * RailIcon — reuses the StatusRow `.status-step` rail glyph (CSS-drawn check /
 * spinner / × / dash) without the surrounding vertical-timeline grid.
 */
export function RailIcon({ status }) {
  const cls = status === 'success' ? 'done' : (status || 'pending')
  return (
    <span className={`status-step ${cls}`} style={{ display: 'inline-flex', minHeight: 0, gap: 0 }}>
      <span className="status-step-icon" />
    </span>
  )
}

export function LiveChip({ workflow, t, hasErrors = false }) {
  const { status } = workflow
  if (status === 'running') {
    return (
      <span className="chip" style={{ color: 'var(--purple)', gap: 3 }}>
        <Loader size={10} strokeWidth={1.5} className="icon-running" />{t('workflow.running')}
      </span>
    )
  }
  if (status === 'completed') {
    // A completed workflow with a failed agent is not a clean ✓.
    if (hasErrors) {
      return (
        <span className="chip" style={{ color: 'var(--orange)', gap: 3 }}>
          <XCircle size={10} strokeWidth={1.5} />{t('workflow.doneWithErrors')}
        </span>
      )
    }
    return (
      <span className="chip" style={{ color: 'var(--green)', gap: 3 }}>
        <CheckCircle size={10} strokeWidth={1.5} />{t('workflow.done')}
      </span>
    )
  }
  if (status === 'failed') {
    return (
      <span className="chip" style={{ color: 'var(--red)', gap: 3 }}>
        <XCircle size={10} strokeWidth={1.5} />{t('workflow.failed')}
      </span>
    )
  }
  if (status === 'stopped') {
    return (
      <span className="chip" style={{ color: 'var(--orange)', gap: 3 }}>
        <X size={10} strokeWidth={1.5} />{t('workflow.stopped')}
      </span>
    )
  }
  return (
    <span className="chip" style={{ color: 'var(--yellow)' }}>{t('workflow.pending')}</span>
  )
}

// A JSON blob (RESULT) is pretty-printed and syntax-highlighted; if the preview
// is truncated mid-object the parse fails and we keep the raw text — still
// wrapped, never horizontally scrolled. Prose (PROMPT) renders as markdown.
function looksLikeJson(raw) {
  const s = String(raw).trim()
  return s.startsWith('{') || s.startsWith('[')
}

// Pretty-print when fully parseable; fall back to the raw string for a truncated
// preview so it still renders. Copy always uses the original `content`, so this
// display-time formatting never affects clipboard integrity.
function formatJsonForDisplay(raw) {
  try {
    return JSON.stringify(JSON.parse(raw), null, 2)
  } catch {
    return String(raw)
  }
}

export function PreviewBlock({ label, content }) {
  if (!content) return null
  const raw = String(content)
  const isJson = looksLikeJson(raw)
  return (
    <div style={{ minWidth: 0 }}>
      <div
        className="uppercase font-semibold"
        style={{ color: 'var(--text-dim)', fontSize: 10, letterSpacing: '0.06em', marginBottom: 2 }}
      >
        {label}
      </div>
      <div
        className="copyable-block relative"
        style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4 }}
      >
        {/* Copy yields the exact original string — display-time JSON formatting
            and CSS soft-wrapping never leak into the clipboard. */}
        <CopyButton content={content} />
        {isJson ? (
          <pre
            style={{
              margin: 0,
              padding: '6px 8px',
              fontSize: 11,
              lineHeight: 1.5,
              fontFamily: "'JetBrains Mono', monospace",
              whiteSpace: 'pre-wrap',
              wordBreak: 'break-word',
              overflowWrap: 'anywhere',
              overflowX: 'hidden',
              overflowY: 'auto',
              maxHeight: 240,
              color: 'var(--text-primary)',
              background: 'transparent',
            }}
            dangerouslySetInnerHTML={{
              __html: hljsCore.highlight(formatJsonForDisplay(raw), { language: 'json' }).value,
            }}
          />
        ) : (
          <div
            className="markdown-body workflow-preview-md"
            style={{
              wordBreak: 'break-word',
              fontSize: 11,
              padding: '6px 8px',
              maxHeight: 240,
              overflowY: 'auto',
              overflowX: 'hidden',
            }}
          >
            <MarkdownRenderer content={raw} />
          </div>
        )}
      </div>
    </div>
  )
}

export function ScriptModal({ script, name, onClose, t }) {
  return createPortal(
    <div
      className="fixed inset-0 flex items-center justify-center"
      style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 1000 }}
      onClick={onClose}
    >
      <div
        className="flex flex-col overflow-hidden"
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-strong)',
          borderRadius: 4,
          maxWidth: 820,
          width: '90%',
          maxHeight: '80vh',
          animation: 'scale-in 200ms cubic-bezier(0.16, 1, 0.3, 1)',
        }}
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center gap-2 px-4 py-3 flex-shrink-0" style={{ borderBottom: '1px solid var(--border)' }}>
          <WorkflowIcon size={14} strokeWidth={1.5} style={{ color: 'var(--purple)' }} />
          <span className="font-semibold text-sm truncate" style={{ color: 'var(--text-primary)' }}>
            {t('workflow.viewScript')}{name ? ` · ${name}` : ''}
          </span>
          <span className="flex-1" />
          <button
            type="button"
            onClick={onClose}
            className="inline-flex items-center justify-center"
            style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }}
            aria-label="Close"
          >
            <X size={16} strokeWidth={1.5} />
          </button>
        </div>
        <div className="overflow-y-auto overflow-x-hidden" style={{ padding: 12 }}>
          <div className="copyable-block relative overflow-hidden" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '10px 12px' }}>
            <CopyButton content={script || ''} />
            <pre
              className="overflow-hidden"
              style={{
                margin: 0,
                color: 'var(--text-secondary)',
                fontFamily: "'JetBrains Mono', monospace",
                fontSize: 12,
                lineHeight: 1.6,
                whiteSpace: 'pre-wrap',
                wordBreak: 'break-word',
              }}
            >
              {script || ''}
            </pre>
          </div>
        </div>
      </div>
      <style>{`@keyframes scale-in { from { transform: scale(0.97); opacity: 0; } to { transform: scale(1); opacity: 1; } }`}</style>
    </div>,
    document.body,
  )
}

// ── Inline card ──

// Display-only agent row inside the inline accordion. Click jumps to the
// Canvas Tasks detail (where prompt/result drill-down lives). No inline expand.
function InlineAgentRow({ workflow, agentIndex }) {
  const { t } = useTranslation()
  const agent = workflow.agents[agentIndex]
  const activeAgentIndex = useWorkflowStore((s) => s.activeAgentIndex)
  const activeWorkflowId = useWorkflowStore((s) => s.activeWorkflowId)
  const focusWorkflowAgent = useWorkflowStore((s) => s.focusWorkflowAgent)
  const showCanvas = useUiStore((s) => s.showCanvas)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)

  // M9: one-shot 0.98→1 settle on the status icon when the agent resolves live.
  const settleRef = useStatusSettle(agent?.state)
  if (!agent) return null
  const { Icon, color, spin } = agentStatusMeta(agent.state)
  const active = activeWorkflowId === workflow.toolUseId && activeAgentIndex === agentIndex
  const tokens = agent.tokens || 0
  const durationStr = formatDuration(agent.durationMs)

  return (
    <button
      type="button"
      data-workflow-agent-id={`${workflow.toolUseId}:${agentIndex}`}
      className="flex items-center gap-1 w-full text-xs overflow-hidden"
      style={{
        background: active ? 'var(--bg-elevated)' : 'transparent',
        border: 'none',
        borderLeft: `2px solid ${active ? 'var(--purple)' : color}`,
        paddingLeft: 8,
        paddingRight: 6,
        paddingTop: 3,
        paddingBottom: 3,
        marginTop: 2,
        cursor: 'pointer',
        textAlign: 'left',
        color: 'var(--text-primary)',
        transition: 'background 150ms ease, border-left-color 150ms ease',
      }}
      onClick={() => {
        focusWorkflowAgent(workflow.toolUseId, agentIndex)
        showCanvas()
        setActiveCanvasTab('tasks')
      }}
      onMouseEnter={(e) => { if (!active) e.currentTarget.style.background = 'var(--bg-elevated)' }}
      onMouseLeave={(e) => { if (!active) e.currentTarget.style.background = 'transparent' }}
    >
      <span ref={settleRef} className="inline-flex flex-shrink-0">
        <Icon size={12} strokeWidth={1.5} style={{ color }} className={spin ? 'icon-running' : ''} />
      </span>
      <span className="truncate min-w-0 flex-1" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
        {agent.label || `agent ${agentIndex}`}
      </span>
      {agent.model && (
        <span className="chip flex-shrink-0" style={{ color: 'var(--cyan)' }}>→ {agent.model}</span>
      )}
      <span className="flex-shrink-0 inline-flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
        <RollingInteger value={tokens} height={12} color="currentColor" />
        <span>{t('workflow.tokens')}</span>
      </span>
      <span className="flex-shrink-0" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
        · {agent.toolCalls || 0} {t('workflow.toolCalls')}
      </span>
      {durationStr && (
        <span className="flex-shrink-0 inline-flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
          <Clock size={10} strokeWidth={1.5} />{durationStr}
        </span>
      )}
    </button>
  )
}

function PhaseRow({ workflow, phase, expanded, onToggle }) {
  const { t } = useTranslation()
  const rollup = phaseRollup(workflow, phase.index)
  const stStatus = phaseStepStatus(workflow, rollup, phase.index)
  const isExpanded = expanded && rollup.agentIndices.length > 0
  const countColor = stStatus === 'success' ? 'var(--green)' : 'var(--purple)'

  return (
    <div style={{ minWidth: 0 }}>
      <button
        type="button"
        className="flex items-center gap-2 w-full text-xs overflow-hidden"
        style={{
          background: isExpanded ? 'var(--bg-elevated)' : 'transparent',
          border: 'none',
          paddingTop: 4,
          paddingBottom: 4,
          paddingRight: 6,
          cursor: 'pointer',
          textAlign: 'left',
          color: stStatus === 'running' ? 'var(--purple)' : 'var(--text-secondary)',
          transition: 'background 150ms ease',
        }}
        onClick={onToggle}
        aria-expanded={isExpanded}
      >
        <RailIcon status={stStatus} />
        <span className="truncate min-w-0 flex-1 font-semibold">
          {phase.title || `${t('workflow.phase')} ${phase.index}`}
        </span>
        {rollup.total > 0 && (
          <span className="chip flex-shrink-0" style={{ color: countColor }}>
            {rollup.done}/{rollup.total}
          </span>
        )}
      </button>

      {isExpanded && (
        <div className="flex flex-col" style={{ paddingLeft: 26, paddingBottom: 4 }}>
          {phase.detail && (
            <div className="text-xs" style={{ color: 'var(--text-dim)', marginBottom: 2 }}>{phase.detail}</div>
          )}
          {rollup.agentIndices.map((ai) => (
            <InlineAgentRow key={ai} workflow={workflow} agentIndex={ai} />
          ))}
        </div>
      )}
    </div>
  )
}

/**
 * WorkflowStatusRow — inline chat card. Workflow meta + accordion phase rail:
 * the running phase auto-expands to its agent rows; other phases collapse to a
 * one-line `{title} {done}/{total}` summary. On completion all phases collapse;
 * a click re-expands any. Click an agent → jump to the Canvas Tasks detail.
 */
export default function WorkflowStatusRow({ workflow }) {
  const { t } = useTranslation()
  const focusWorkflowAgent = useWorkflowStore((s) => s.focusWorkflowAgent)
  const showCanvas = useUiStore((s) => s.showCanvas)
  const setActiveCanvasTab = useUiStore((s) => s.setActiveCanvasTab)
  const [showScript, setShowScript] = useState(false)
  const [pinnedPhase, setPinnedPhase] = useState(null)
  const [, forceTick] = useState(0)

  const status = workflow.status
  const terminal = isTerminal(status)
  const running = status === 'running'

  // Collapsible card: expanded while running, auto-collapses to the header +
  // progress bar the instant the run finishes. The user can re-expand freely;
  // we only force-collapse on the running → terminal transition.
  const [collapsed, setCollapsed] = useState(terminal)
  const prevTerminalRef = useRef(terminal)
  useEffect(() => {
    if (terminal && !prevTerminalRef.current) setCollapsed(true)
    prevTerminalRef.current = terminal
  }, [terminal])

  // Tick once a second while running so the header elapsed stays live even
  // between store updates.
  useEffect(() => {
    if (!running) return undefined
    const id = setInterval(() => forceTick((n) => n + 1), 1000)
    return () => clearInterval(id)
  }, [running])

  const phases = workflow.phases
  const { done: agentsDone, total: agentsTotal } = workflowAgentTotals(workflow)
  const hasErrors = anyAgentFailed(workflow)
  const neutralPending = status === 'pending' && phases.length === 0 && agentsTotal === 0

  // Accordion: pinned phase wins; otherwise auto-follow the running phase
  // (collapsed entirely once the run is terminal — click to re-expand).
  const autoPhase = activePhaseIndex(workflow)
  const expandedPhase = pinnedPhase != null ? pinnedPhase : (terminal ? null : autoPhase)
  const togglePhase = (idx) => setPinnedPhase((prev) => (prev === idx ? null : idx))

  const elapsed = formatDuration(elapsedMs(workflow))

  const actions = []
  if (workflow.script) {
    actions.push({ label: t('workflow.viewScript'), onClick: () => setShowScript(true) })
  }
  actions.push({
    label: t('workflow.openInCanvas'),
    onClick: () => {
      const firstAgent = workflow.agentOrder[0]
      focusWorkflowAgent(workflow.toolUseId, firstAgent != null ? firstAgent : null)
      showCanvas()
      setActiveCanvasTab('tasks')
    },
  })
  if (running) {
    actions.push({ label: t('workflow.stop'), danger: true, onClick: () => stopActiveStream() })
  }

  return (
    <>
      <div className={`status-row full ${rowStatus(status)} message-tool-card-square overflow-hidden`}>
        {/* Header — click (or Enter/Space) to collapse/expand. Auto-collapses
            when the run finishes; the chevron rotates to signal state. */}
        <div
          className="status-full-header"
          role="button"
          tabIndex={0}
          aria-expanded={!collapsed}
          onClick={() => setCollapsed((c) => !c)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); setCollapsed((c) => !c) }
          }}
          style={{ cursor: 'pointer' }}
        >
          <div style={{ minWidth: 0 }}>
            {/* Chevron + title on one flex line (items-center, mirroring PhaseRow)
                so the chevron centers on the title text; description indented to match. */}
            <div className="flex items-center" style={{ gap: 6, minWidth: 0 }}>
              <AnimatedChevron open={!collapsed} style={{ color: 'var(--text-dim)' }}>
                <ChevronDown size={12} strokeWidth={1.5} />
              </AnimatedChevron>
              <h3 style={{ margin: 0, minWidth: 0, lineHeight: 1.3, wordBreak: 'break-word' }}>
                {t('workflow.title')}{workflow.workflowName ? ` · ${workflow.workflowName}` : ''}
              </h3>
            </div>
            {(workflow.summary || workflow.description || neutralPending) && (
              <p style={{ paddingLeft: 18 }}>{neutralPending ? t('workflow.launched') : (workflow.summary || workflow.description)}</p>
            )}
          </div>
          <div className="status-header-chips">
            {agentsTotal > 0 && (
              <span className="chip" style={{ color: terminal ? 'var(--text-secondary)' : 'var(--purple)' }}>
                {agentsDone}/{agentsTotal} {t('workflow.agents')}
              </span>
            )}
            <LiveChip workflow={workflow} t={t} hasErrors={hasErrors} />
          </div>
        </div>

        {/* Progress strip — stays visible in the collapsed state (header + bar). */}
        {agentsTotal > 0 && (
          <div className="progress-strip" aria-label="progress" style={{ marginLeft: 18 }}>
            <span
              className="progress-fill"
              style={{
                width: `${Math.round((agentsDone / agentsTotal) * 100)}%`,
                // red on failure, green when cleanly completed, purple while running/pending.
                background: (status === 'failed' || status === 'stopped' || hasErrors)
                  ? 'var(--red)'
                  : status === 'completed'
                    ? 'var(--green)'
                    : 'var(--purple)',
              }}
            />
          </div>
        )}

        {/* Collapsible body: meta · phases · action buttons. Keep content mounted
            and use a CSS grid transition so toggling avoids JS height measurement. */}
        <div
          className={`workflow-card-collapse${collapsed ? '' : ' open'}`}
          aria-hidden={collapsed}
        >
          <div className="workflow-card-collapse-inner">
            <div className="flex flex-col" style={{ gap: 10, marginLeft: 18 }}>
              {(elapsed || workflow.taskId) && (
                <div className="status-meta">
                  {elapsed && <span>{elapsed}</span>}
                  {workflow.taskId && <span>task:{workflow.taskId}</span>}
                </div>
              )}

              {phases.length > 0 && (
                <div
                  className="flex flex-col"
                  style={{ background: 'var(--bg-surface)', border: '1px solid var(--border)', borderRadius: 4, padding: '4px 6px' }}
                >
                  {phases.map((phase) => (
                    <PhaseRow
                      key={phase.index}
                      workflow={workflow}
                      phase={phase}
                      expanded={expandedPhase === phase.index}
                      onToggle={() => togglePhase(phase.index)}
                    />
                  ))}
                </div>
              )}

              <div className="status-actions">
                {actions.map((a, i) => (
                  <button
                    key={i}
                    type="button"
                    className={`status-action-btn${a.danger ? ' danger' : ''}`}
                    onClick={a.onClick}
                  >
                    {a.label}
                  </button>
                ))}
              </div>
            </div>
          </div>
        </div>
      </div>

      {showScript && (
        <ScriptModal
          script={workflow.script}
          name={workflow.workflowName}
          onClose={() => setShowScript(false)}
          t={t}
        />
      )}
    </>
  )
}
