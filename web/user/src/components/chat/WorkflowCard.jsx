import { memo, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useTranslation } from 'react-i18next'
import { Workflow as WorkflowIcon, X } from 'lucide-react'
import StatusRow from '@shared/components/shared/StatusRow'
import CopyButton from '@shared/components/shared/CopyButton'
import useWorkflowStore from '../../stores/workflowStore'
import { fetchWorkflowState } from '../../api/sessions'
import WorkflowStatusRow from './WorkflowStatusRow'
import ToolCallCard from './ToolCallCard'

// The Workflow tool_result text carries "Run ID: wf_…" — the key linking a
// reloaded tool_use block to its persisted workflows/<runId>.json snapshot.
function extractRunId(result) {
  if (!result) return null
  let text = result.content
  if (Array.isArray(text)) {
    text = text.map((b) => (typeof b === 'string' ? b : b?.text || '')).join('\n')
  }
  if (typeof text !== 'string') return null
  const m = text.match(/Run ID:\s*(wf_[A-Za-z0-9_-]+)/)
  return m ? m[1] : null
}

// Neutral "launched in background" fallback for a Workflow block that produced
// no task events AND seeded no meta (e.g. a reloaded session before the store
// is re-seeded, or a script with no parseable meta). Never shows a spinner.
function LaunchedRow({ block }) {
  const { t } = useTranslation()
  const [showScript, setShowScript] = useState(false)
  const script = block.input?.script
  const name = block.input?.name

  const actions = script
    ? [{ label: t('workflow.viewScript'), onClick: () => setShowScript(true) }]
    : []

  return (
    <>
      <StatusRow
        variant="full"
        status="pending"
        title={`${t('workflow.title')}${name ? ` · ${name}` : ''}`}
        description={t('workflow.launched')}
        headerChips={<span className="chip" style={{ color: 'var(--yellow)' }}>{t('workflow.pending')}</span>}
        steps={[]}
        actions={actions}
        collapsible={false}
        className="message-tool-card-square"
      />
      {showScript && createPortal(
        <div
          className="fixed inset-0 flex items-center justify-center"
          style={{ background: 'var(--bg-overlay)', backdropFilter: 'blur(4px)', zIndex: 1000 }}
          onClick={() => setShowScript(false)}
        >
          <div
            className="flex flex-col overflow-hidden"
            style={{
              background: 'var(--bg-surface)', border: '1px solid var(--border-strong)', borderRadius: 4,
              maxWidth: 820, width: '90%', maxHeight: '80vh',
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
              <button type="button" onClick={() => setShowScript(false)} className="inline-flex items-center justify-center"
                style={{ background: 'transparent', border: 'none', cursor: 'pointer', color: 'var(--text-dim)', padding: 2 }} aria-label="Close">
                <X size={16} strokeWidth={1.5} />
              </button>
            </div>
            <div className="overflow-y-auto overflow-x-hidden" style={{ padding: 12 }}>
              <div className="copyable-block relative overflow-hidden" style={{ background: 'var(--bg-elevated)', border: '1px solid var(--border)', borderRadius: 4, padding: '10px 12px' }}>
                <CopyButton content={script || ''} />
                <pre className="overflow-hidden" style={{ margin: 0, color: 'var(--text-secondary)', fontFamily: "'JetBrains Mono', monospace", fontSize: 14, lineHeight: 1.6, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
                  {script || ''}
                </pre>
              </div>
            </div>
          </div>
          <style>{`@keyframes scale-in { from { transform: scale(0.97); opacity: 0; } to { transform: scale(1); opacity: 1; } }`}</style>
        </div>,
        document.body,
      )}
    </>
  )
}

/**
 * A `Workflow` tool_use whose result is an error AND that never actually
 * started a task (no taskId, no live agents, still pending) is a failed launch
 * — a parse-error / invalid script that produced zero task events. It's just a
 * tool use that failed, so render the standard ToolCallCard (script input +
 * <tool_use_error>), not a workflow card. Meta-seeded phases do NOT count as
 * task activity, so a parseable-but-invalid script still lands here.
 */
function isFailedLaunch(block, workflow) {
  const errored = block?.result?.is_error || block?.status === 'error'
  if (!errored) return false
  if (!workflow) return true
  return workflow.status === 'pending' && !workflow.taskId && workflow.agentOrder.length === 0
}

/**
 * WorkflowCard — inline chat render for a `Workflow` tool_use block.
 *
 * Status derives ONLY from the live workflow store (workflow.status), never
 * from block.status / block.result — the "launched in background" tool_result
 * must not flip the card to done. The single exception is a failed launch
 * (see isFailedLaunch), which delegates to ToolCallCard.
 */
function WorkflowCard({ block, reverted = false }) {
  const workflow = useWorkflowStore((s) => s.workflows[block.id])
  const failed = isFailedLaunch(block, workflow)

  // On reload, sessionTransform rebuilds the tool_use block but not the live
  // task_* events — seed the store from the script so the meta scaffold paints
  // (phases + name). A historical block already carries a (non-error) result,
  // which means the launch succeeded and the turn finished, so seed it as
  // completed rather than a misleading PENDING. (Full per-agent hydration from
  // the workflow journal remains a follow-up; this fixes the stuck status.)
  useEffect(() => {
    if (failed || workflow) return
    if (!block.input?.script) return
    const settled = Boolean(block.result) || block.status === 'success'
    useWorkflowStore.getState().ensureWorkflow(block.id, {
      script: block.input.script,
      workflowName: block.input.name,
      status: settled ? 'completed' : 'pending',
    })
  }, [failed, workflow, block.id, block.input?.script, block.input?.name, block.result, block.status])

  // Rehydrate a RELOADED workflow (script-seeded scaffold, no agents, never
  // started live) from its persisted snapshot — task_progress isn't saved to the
  // transcript, so this is the only way the recovered card gets per-agent detail.
  // A live workflow gets agents from SSE (startedAt set) and is skipped.
  const hydratedRef = useRef(false)
  useEffect(() => {
    if (failed || hydratedRef.current) return
    if (!workflow || workflow.startedAt || workflow.agentOrder.length > 0) return
    const runId = extractRunId(block.result)
    if (!runId) return
    hydratedRef.current = true
    fetchWorkflowState(runId)
      .then((snap) => useWorkflowStore.getState().hydrateFromSnapshot(block.id, snap))
      .catch(() => { hydratedRef.current = false })
  }, [failed, workflow, block.id, block.result])

  if (failed) {
    return <ToolCallCard block={block} reverted={reverted} />
  }

  return (
    <div
      className="overflow-hidden"
      data-workflow-card
      data-tool-use-id={block.id}
      style={{ opacity: reverted ? 0.55 : 1, filter: reverted ? 'grayscale(0.4)' : 'none' }}
    >
      {workflow ? <WorkflowStatusRow workflow={workflow} /> : <LaunchedRow block={block} />}
    </div>
  )
}

export default memo(WorkflowCard)
