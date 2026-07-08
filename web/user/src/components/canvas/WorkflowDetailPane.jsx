import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { ChevronDown, ChevronRight, Clock } from 'lucide-react'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import { RollingInteger } from '../shared/Odometer'
import useUiStore from '@shared/stores/uiStore'
import useWorkflowStore, { phaseRollup, workflowAgentTotals } from '../../stores/workflowStore'
import { fetchWorkflowAgentTranscript } from '../../api/sessions'
import { tweenScrollIntoView } from '@shared/motion/tweenScroll'
import {
  formatDuration,
  agentStatusMeta,
  phaseStepStatus,
  activePhaseIndex,
  anyAgentFailed,
  statusColor,
  isTerminal,
  LiveChip,
  PreviewBlock,
} from '../chat/WorkflowStatusRow'

// Side-by-side phase-nav + detail above this width; stacked below it.
const SIDE_BY_SIDE_MIN = 520

/**
 * DetailAgentRow — the drill-down row. Expands inline to PROMPT / RESULT + a
 * meta line ({model} ↘ {fallback} · {n} tool · attempt n · agentId). Honors
 * the focus bridge: when this agent becomes the focus target (an inline-card
 * click), it auto-expands and scrolls into view.
 */
function DetailAgentRow({ workflow, agentIndex }) {
  const { t } = useTranslation()
  const agent = workflow.agents[agentIndex]
  const bodyId = useId()
  const [expanded, setExpanded] = useState(false)
  const rowRef = useRef(null)
  const handledRevisionRef = useRef(0)
  // Lazy full prompt/result, recovered from agent-<id>.jsonl on expand — the
  // live task_progress stream only carries truncated previews.
  const [fullText, setFullText] = useState(null)
  const fetchedKeyRef = useRef(null)

  const activeAgentIndex = useWorkflowStore((s) => s.activeAgentIndex)
  const activeWorkflowId = useWorkflowStore((s) => s.activeWorkflowId)
  const setActiveWorkflowAgent = useWorkflowStore((s) => s.setActiveWorkflowAgent)
  const inspectorFocusTarget = useWorkflowStore((s) => s.inspectorFocusTarget)
  const inspectorFocusRevision = useWorkflowStore((s) => s.inspectorFocusRevision)

  useEffect(() => {
    if (inspectorFocusRevision === 0) return undefined
    if (handledRevisionRef.current === inspectorFocusRevision) return undefined
    const tgt = inspectorFocusTarget
    if (tgt?.type !== 'workflow-agent') return undefined
    if (tgt.workflowId !== workflow.toolUseId || tgt.agentIndex !== agentIndex) return undefined
    handledRevisionRef.current = inspectorFocusRevision
    setExpanded(true)
    const raf = window.requestAnimationFrame(() => {
      tweenScrollIntoView(rowRef.current, { block: 'center', flash: true })
    })
    return () => window.cancelAnimationFrame(raf)
  }, [inspectorFocusRevision, inspectorFocusTarget, workflow.toolUseId, agentIndex])

  // On first expand (and again if the agent's state advances), pull the full
  // prompt + result from its transcript. Re-keys on state so a row opened mid-run
  // upgrades to the complete result once the agent finishes. Failures (route not
  // deployed yet, or transcript not written) fall back silently to the preview.
  useEffect(() => {
    if (!expanded || !agent?.agentId) return undefined
    const key = `${agent.agentId}:${agent.state}`
    if (fetchedKeyRef.current === key) return undefined
    fetchedKeyRef.current = key
    let cancelled = false
    fetchWorkflowAgentTranscript(agent.agentId)
      .then((data) => { if (!cancelled) setFullText(data) })
      .catch(() => { /* keep preview */ })
    return () => { cancelled = true }
  }, [expanded, agent?.agentId, agent?.state])

  if (!agent) return null
  const { Icon, color, spin } = agentStatusMeta(agent.state)
  const active = activeWorkflowId === workflow.toolUseId && activeAgentIndex === agentIndex
  const tokens = agent.tokens || 0
  const durationStr = formatDuration(agent.durationMs)

  const metaParts = []
  if (agent.model) {
    metaParts.push(agent.fallbackModel ? `${agent.model} ↘ ${agent.fallbackModel}` : agent.model)
  }
  metaParts.push(`${agent.toolCalls || 0} ${t('workflow.toolCalls')}`)
  if (agent.attempt) metaParts.push(`${t('workflow.attempt')} ${agent.attempt}`)
  if (agent.agentId) metaParts.push(agent.agentId)

  return (
    <div style={{ minWidth: 0 }}>
      <button
        type="button"
        ref={rowRef}
        data-workflow-agent-id={`${workflow.toolUseId}:${agentIndex}`}
        className="quiet-toggle flex items-center gap-1 w-full text-xs overflow-hidden"
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
          setExpanded((v) => !v)
          setActiveWorkflowAgent(workflow.toolUseId, agentIndex)
        }}
        aria-expanded={expanded}
        aria-controls={bodyId}
      >
        <span className="flex-shrink-0 inline-flex items-center justify-center" style={{ width: 10, height: 12 }}>
          <AnimatedChevron open={expanded} style={{ color: 'var(--text-dim)' }}>
            <ChevronDown size={10} strokeWidth={1.5} />
          </AnimatedChevron>
        </span>
        <Icon size={12} strokeWidth={1.5} style={{ color, flexShrink: 0 }} className={spin ? 'icon-running' : ''} />
        <span className="truncate min-w-0 flex-1" style={{ fontFamily: "'JetBrains Mono', monospace" }}>
          {agent.label || `agent ${agentIndex}`}
        </span>
        {agent.model && (
          <span className="chip flex-shrink-0" style={{ color: 'var(--cyan)' }}>{agent.model}</span>
        )}
        <span className="flex-shrink-0 inline-flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
          <RollingInteger value={tokens} height={12} color="currentColor" />
          <span>{t('workflow.tokens')}</span>
        </span>
        {durationStr && (
          <span className="flex-shrink-0 inline-flex items-center gap-1" style={{ color: 'var(--text-dim)', fontSize: 11 }}>
            <Clock size={10} strokeWidth={1.5} />{durationStr}
          </span>
        )}
      </button>

      <AnimatedCollapse open={expanded} id={bodyId} animateHeight={false}>
        {() => (
          <div className="flex flex-col gap-2" style={{ paddingLeft: 18, paddingRight: 6, paddingTop: 4, paddingBottom: 6, minWidth: 0 }}>
            <PreviewBlock label={t('workflow.prompt')} content={fullText?.prompt || agent.promptPreview} />
            <PreviewBlock label={t('workflow.result')} content={fullText?.result || agent.resultPreview} />
            <div
              className="text-xs"
              style={{ color: 'var(--text-dim)', fontFamily: "'JetBrains Mono', monospace", wordBreak: 'break-word' }}
            >
              {metaParts.join(' · ')}
            </div>
          </div>
        )}
      </AnimatedCollapse>
    </div>
  )
}

function PhaseNav({ workflow, phases, selectedPhase, onSelect, stacked }) {
  const { t } = useTranslation()
  return (
    <div
      className="flex flex-col flex-shrink-0"
      style={{
        width: stacked ? '100%' : 168,
        gap: 1,
        ...(stacked
          ? { borderBottom: '1px solid var(--border-subtle)', paddingBottom: 6, marginBottom: 6 }
          : { borderRight: '1px solid var(--border-subtle)', paddingRight: 6, marginRight: 6 }),
      }}
    >
      <div
        className="uppercase font-semibold"
        style={{ color: 'var(--text-dim)', fontSize: 10, letterSpacing: '0.06em', padding: '2px 4px' }}
      >
        {t('workflow.phasesLabel')}
      </div>
      {phases.map((phase) => {
        const rollup = phaseRollup(workflow, phase.index)
        const stStatus = phaseStepStatus(workflow, rollup, phase.index)
        const selected = selectedPhase === phase.index
        return (
          <button
            key={phase.index}
            type="button"
            className="flex items-center gap-1 w-full text-xs overflow-hidden"
            style={{
              background: selected ? 'var(--bg-elevated)' : 'transparent',
              border: 'none',
              borderLeft: `2px solid ${selected ? 'var(--blue)' : statusColor(stStatus)}`,
              paddingLeft: 6,
              paddingRight: 4,
              paddingTop: 4,
              paddingBottom: 4,
              cursor: 'pointer',
              textAlign: 'left',
              color: selected ? 'var(--text-primary)' : 'var(--text-secondary)',
              transition: 'background 150ms ease, border-left-color 150ms ease',
            }}
            onClick={() => onSelect(phase.index)}
            aria-current={selected}
          >
            <span className="flex-shrink-0" style={{ width: 8, color: 'var(--blue)' }}>
              {selected ? <ChevronRight size={10} strokeWidth={1.5} /> : null}
            </span>
            <span className="flex-shrink-0 font-light" style={{ color: 'var(--text-dim)' }}>{phase.index}</span>
            <span className="truncate min-w-0 flex-1">{phase.title || `${t('workflow.phase')} ${phase.index}`}</span>
            {rollup.total > 0 && (
              <span className="flex-shrink-0 font-light" style={{ color: 'var(--text-dim)' }}>
                {rollup.done}/{rollup.total}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

/**
 * WorkflowDetailPane — the Canvas two-pane master-detail for one workflow.
 * Left: phase nav (auto-follows the running phase; a click pins). Right: the
 * selected phase's agents, each expandable to PROMPT / RESULT + meta.
 */
export default function WorkflowDetailPane({ workflow }) {
  const { t } = useTranslation()
  const canvasWidth = useUiStore((s) => s.canvasWidth)
  const stacked = canvasWidth < SIDE_BY_SIDE_MIN
  const [pinnedPhase, setPinnedPhase] = useState(null)

  const inspectorFocusTarget = useWorkflowStore((s) => s.inspectorFocusTarget)
  const inspectorFocusRevision = useWorkflowStore((s) => s.inspectorFocusRevision)
  const handledRef = useRef(0)

  const phases = workflow.phases
  const lastPhaseIndex = phases.length ? phases[phases.length - 1].index : null
  const autoPhase = activePhaseIndex(workflow)
  // The detail pane always resolves to a concrete phase.
  const selectedPhase = pinnedPhase != null ? pinnedPhase
    : (autoPhase != null ? autoPhase : lastPhaseIndex)

  // Focus bridge: when an inline-card agent click targets this workflow, pin
  // the phase that holds that agent so the detail row mounts (and then scrolls
  // itself via its own revision effect).
  useEffect(() => {
    if (inspectorFocusRevision === 0) return
    if (handledRef.current === inspectorFocusRevision) return
    const tgt = inspectorFocusTarget
    if (tgt?.type !== 'workflow-agent' || tgt.workflowId !== workflow.toolUseId) return
    const pIdx = workflow.agents[tgt.agentIndex]?.phaseIndex
    if (pIdx == null) return
    handledRef.current = inspectorFocusRevision
    setPinnedPhase(pIdx)
  }, [inspectorFocusRevision, inspectorFocusTarget, workflow])

  const status = workflow.status
  const { done: agentsDone, total: agentsTotal } = workflowAgentTotals(workflow)

  const selRollup = selectedPhase != null ? phaseRollup(workflow, selectedPhase) : null
  const selPhase = phases.find((p) => p.index === selectedPhase) || null

  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 4, background: 'var(--bg-base)', minWidth: 0 }}>
      {/* Header (label on the top edge) */}
      <div
        className="flex items-center gap-2 overflow-hidden"
        style={{ padding: '6px 8px', borderBottom: '1px solid var(--border-subtle)' }}
      >
        <span
          className="uppercase font-semibold truncate min-w-0"
          style={{ color: 'var(--text-secondary)', fontSize: 11, letterSpacing: '0.06em' }}
        >
          {t('workflow.title')}{workflow.workflowName ? ` · ${workflow.workflowName}` : ''}
        </span>
        <span className="flex-1" />
        {agentsTotal > 0 && (
          <span className="chip flex-shrink-0" style={{ color: isTerminal(status) ? 'var(--text-secondary)' : 'var(--purple)' }}>
            {agentsDone}/{agentsTotal}
          </span>
        )}
        <span className="flex-shrink-0"><LiveChip workflow={workflow} t={t} hasErrors={anyAgentFailed(workflow)} /></span>
      </div>

      {phases.length === 0 ? (
        <div className="text-xs" style={{ color: 'var(--text-dim)', padding: '8px 10px' }}>
          {status === 'pending' ? t('workflow.launched') : t('workflow.detailEmpty')}
        </div>
      ) : (
        <div className={stacked ? 'flex flex-col' : 'flex'} style={{ padding: 8, minWidth: 0 }}>
          <PhaseNav
            workflow={workflow}
            phases={phases}
            selectedPhase={selectedPhase}
            onSelect={(idx) => setPinnedPhase((prev) => (prev === idx ? null : idx))}
            stacked={stacked}
          />
          <div className="flex flex-col min-w-0" style={{ flex: 1, minWidth: 0 }}>
            <div
              className="truncate"
              style={{ color: 'var(--text-secondary)', fontSize: 11, fontWeight: 600, padding: '2px 4px 4px' }}
            >
              {selPhase ? (selPhase.title || `${t('workflow.phase')} ${selPhase.index}`) : ''}
              {selRollup && (
                <span className="font-light" style={{ color: 'var(--text-dim)' }}>
                  {' · '}{t('workflow.agentsCount', { n: selRollup.total })}
                </span>
              )}
            </div>
            {selPhase?.detail && (
              <div className="text-xs" style={{ color: 'var(--text-dim)', padding: '0 4px 4px' }}>{selPhase.detail}</div>
            )}
            {selRollup && selRollup.agentIndices.length > 0 ? (
              selRollup.agentIndices.map((ai) => (
                <DetailAgentRow key={ai} workflow={workflow} agentIndex={ai} />
              ))
            ) : (
              <div className="text-xs" style={{ color: 'var(--text-dim)', padding: '4px' }}>
                {t('workflow.detailEmpty')}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}
