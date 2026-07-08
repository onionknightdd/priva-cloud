import { useTranslation } from 'react-i18next'
import { useStaggerEntrance } from '@shared/motion/useStaggerEntrance'
import useTaskStore from '../../stores/taskStore'
import TaskNode from './TaskNode'

export default function TaskTree() {
  const { t } = useTranslation()
  const tasks = useTaskStore((s) => s.tasks)
  const taskOrder = useTaskStore((s) => s.taskOrder)
  // New task nodes fade + rise in; nodes present on first commit render static.
  const entranceRef = useStaggerEntrance()

  const taskList = taskOrder.map((id) => tasks[id]).filter(Boolean)

  if (taskList.length === 0) return null

  return (
    <div className="py-1">
      <div
        className="px-3 py-1"
        style={{
          color: 'var(--text-dim)',
          fontSize: 11,
          fontWeight: 600,
          letterSpacing: '0.06em',
        }}
      >
        {t('canvas.tasksHeader')}
      </div>
      {taskList.map((task) => (
        <div key={task.tool_use_id} ref={entranceRef(task.tool_use_id)}>
          <TaskNode task={task} />
        </div>
      ))}
    </div>
  )
}
