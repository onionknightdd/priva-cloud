import { getSdkTaskDisplayStatus } from '../../utils/sdkTaskTracker'

const STATUS_LABELS = {
  pending: 'PENDING',
  in_progress: 'IN PROGRESS',
  blocked: 'BLOCKED',
  completed: 'COMPLETED',
  deleted: 'DELETED',
}

const STATUS_COLORS = {
  pending: 'var(--status-pending)',
  in_progress: 'var(--status-running)',
  blocked: 'var(--status-error)',
  completed: 'var(--status-success)',
  deleted: 'var(--status-idle)',
}

function taskNumber(task) {
  if (task?.provisional || String(task?.id || '').startsWith('pending:')) return '#—'
  return `#${task?.id || '—'}`
}

function taskSecondary(task, status) {
  if (status === 'blocked') {
    const blockers = Array.isArray(task?.blockedBy) ? task.blockedBy : []
    if (blockers.length > 0) return `blocked by ${blockers.map((id) => `#${id}`).join(', ')}`
  }
  if (status === 'completed' || status === 'deleted') return ''
  const detail = task?.activeForm || task?.description || ''
  const owner = task?.owner ? `owner: ${task.owner}` : ''
  return [detail, owner].filter(Boolean).join(' · ')
}

export default function SdkTaskTrackerRows({ tasks, compact = false }) {
  return (
    <div className="flex flex-col" style={{ minWidth: 0 }}>
      {tasks.map((task, index) => {
        const status = getSdkTaskDisplayStatus(task)
        const secondary = taskSecondary(task, status)
        const key = task?._key || task?.id || index
        return (
          <div
            key={key}
            style={{
              minWidth: 0,
              boxSizing: 'border-box',
              padding: compact ? '6px 10px' : '8px 12px',
              borderLeft: `2px solid ${STATUS_COLORS[status] || 'var(--status-idle)'}`,
              borderBottom: index < tasks.length - 1 ? '1px solid var(--border-subtle)' : 'none',
            }}
          >
            <div
              style={{
                display: 'grid',
                gridTemplateColumns: 'auto minmax(0, 1fr) auto',
                alignItems: 'baseline',
                columnGap: 8,
                minWidth: 0,
                fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                fontSize: compact ? 11 : 12,
                lineHeight: 1.5,
              }}
            >
              <span className="flex-shrink-0" style={{ color: 'var(--text-dim)' }}>
                {taskNumber(task)}
              </span>
              <span
                style={{
                  minWidth: 0,
                  color: 'var(--text-primary)',
                  fontWeight: 600,
                  overflowWrap: 'break-word',
                  wordBreak: 'break-word',
                }}
              >
                {task?.subject || 'Untitled task'}
              </span>
              <span
                className="uppercase whitespace-nowrap"
                style={{
                  color: STATUS_COLORS[status] || 'var(--text-dim)',
                  fontSize: 10,
                  fontWeight: 600,
                  letterSpacing: '0.06em',
                }}
              >
                {STATUS_LABELS[status] || String(status).toUpperCase()}
              </span>
            </div>
            {secondary && (
              <div
                style={{
                  minWidth: 0,
                  paddingLeft: compact ? 28 : 31,
                  paddingTop: 2,
                  color: status === 'blocked' ? 'var(--red)' : 'var(--text-dim)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 10,
                  lineHeight: 1.5,
                  overflowWrap: 'break-word',
                  wordBreak: 'break-word',
                }}
              >
                {secondary}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
