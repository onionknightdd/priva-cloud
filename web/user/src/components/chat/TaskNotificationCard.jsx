import { memo } from 'react'
import { useTranslation } from 'react-i18next'
import { Workflow, SquareTerminal } from 'lucide-react'
import { taskNotificationIsError, taskNotificationStatusKey } from '../../utils/taskNotification'

/**
 * TaskNotificationCard — the slim system card for a CLI-injected
 * <task-notification> (a background Workflow / Bash finishing and re-invoking
 * the model). Renders in the message flow in place of the raw XML user bubble;
 * the model's summary streams below as its own assistant turn.
 *
 * Status is carried by a 2px left border (green = completed, red = otherwise),
 * per the design system — never a dot or filled background. `notif.kind` picks
 * the glyph: workflow vs. background task.
 */
function TaskNotificationCard({ notif }) {
  const { t } = useTranslation()
  if (!notif) return null

  const isWorkflow = notif.kind === 'workflow'
  const isError = taskNotificationIsError(notif.status)
  const color = isError ? 'var(--red)' : 'var(--green)'
  const Icon = isWorkflow ? Workflow : SquareTerminal
  const label = t(isWorkflow ? 'notification.workflowLabel' : 'notification.taskLabel', {
    status: t(`notification.status.${taskNotificationStatusKey(notif.status)}`),
  })

  return (
    <div className="my-2 overflow-hidden">
      <div
        className="flex items-start gap-2 px-3 py-2 overflow-hidden"
        style={{
          background: 'var(--bg-surface)',
          border: '1px solid var(--border-subtle)',
          borderLeft: `2px solid ${color}`,
          borderRadius: 4,
        }}
      >
        <Icon size={14} strokeWidth={1.5} style={{ color, flexShrink: 0, marginTop: 1 }} />
        <div className="min-w-0">
          <div
            className="text-xs font-semibold uppercase"
            style={{ color, letterSpacing: '0.06em' }}
          >
            {label}
          </div>
          {notif.summary && (
            <div
              className="text-sm"
              style={{
                color: 'var(--text-secondary)',
                marginTop: 2,
                wordBreak: 'break-word',
                overflowWrap: 'break-word',
              }}
            >
              {notif.summary}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

export default memo(TaskNotificationCard)
