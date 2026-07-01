import { useTranslation } from 'react-i18next'
import useWorkflowStore from '../../stores/workflowStore'
import WorkflowDetailPane from './WorkflowDetailPane'

/**
 * Canvas mirror of the live workflows. Renders a WORKFLOW section header (like
 * SubagentInspector's) then a two-pane master-detail per workflow: left phase
 * nav (auto-follows the running phase; a click pins it) ▸ right agent-detail
 * pane (each agent expands inline to PROMPT / RESULT + meta). Returns null when
 * there are no workflows so it adds zero layout cost.
 *
 * The focus → expand → scroll behavior lives inside WorkflowDetailPane, driven
 * by workflowStore.inspectorFocusRevision.
 */
export default function WorkflowTree() {
  const { t } = useTranslation()
  const workflowOrder = useWorkflowStore((s) => s.workflowOrder)
  const workflows = useWorkflowStore((s) => s.workflows)

  if (!workflowOrder.length) return null

  const activeCount = workflowOrder.filter((id) => workflows[id]?.status === 'running').length

  return (
    <div className="py-1" style={{ borderBottom: '1px solid var(--border-subtle)' }}>
      <div
        className="flex items-center gap-1 w-full px-3 py-1"
        style={{
          color: 'var(--text-dim)',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
          textAlign: 'left',
        }}
      >
        <span className="flex-1">{t('workflow.inspectorTitle')}</span>
        <span className="font-light" style={{ letterSpacing: 0 }}>
          {activeCount > 0 ? `${activeCount} ${t('workflow.running')} · ` : ''}
          {workflowOrder.length}
        </span>
      </div>

      <div className="flex flex-col gap-2" style={{ padding: '4px 10px 8px' }}>
        {workflowOrder.map((id) => (
          workflows[id] ? <WorkflowDetailPane key={id} workflow={workflows[id]} /> : null
        ))}
      </div>
    </div>
  )
}
