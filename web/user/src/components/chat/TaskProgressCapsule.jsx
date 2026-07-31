import { useEffect, useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import useTaskStore from '../../stores/taskStore'
import {
  getSdkTaskComposerTasks,
  getSdkTaskComposerRoundIds,
  getSdkTaskProgress,
} from '../../utils/sdkTaskTracker'
import SdkTaskTrackerRows from '../tasks/SdkTaskTrackerRows'

export default function TaskProgressCapsule() {
  const tracker = useTaskStore((state) => state.sdkTaskTracker)
  const [expanded, setExpanded] = useState(false)
  const tasks = useMemo(() => getSdkTaskComposerTasks(tracker), [tracker])
  const progress = useMemo(() => getSdkTaskProgress(tasks), [tasks])
  const visible = getSdkTaskComposerRoundIds(tracker).length > 0

  useEffect(() => {
    if (!visible) setExpanded(false)
  }, [visible])

  if (!visible) return null

  return (
    <div
      className="flex flex-col items-center"
      style={{
        width: '100%',
        minWidth: 0,
        marginBottom: 8,
        boxSizing: 'border-box',
      }}
    >
      <AnimatedCollapse
        open={expanded}
        style={{ width: 'min(560px, 100%)' }}
        innerStyle={{ width: '100%' }}
      >
        {() => (
          <div
            style={{
              width: '100%',
              maxHeight: 264,
              marginBottom: 6,
              boxSizing: 'border-box',
              overflowY: 'auto',
              overflowX: 'hidden',
              background: 'var(--bg-surface)',
              border: '1px solid var(--border-strong)',
              borderRadius: 4,
            }}
          >
            <div
              className="flex items-center gap-2"
              style={{
                position: 'sticky',
                top: 0,
                zIndex: 1,
                minWidth: 0,
                padding: '7px 12px',
                background: 'var(--bg-surface)',
                borderBottom: '1px solid var(--border)',
                color: 'var(--text-secondary)',
                fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                fontSize: 10,
                fontWeight: 600,
                letterSpacing: '0.06em',
              }}
            >
              <span className="flex-1">TASKS</span>
              <span className="whitespace-nowrap">
                {progress.done} / {progress.total} DONE
              </span>
            </div>
            {tasks.length > 0 ? (
              <SdkTaskTrackerRows tasks={tasks} />
            ) : (
              <div
                className="px-3 py-3"
                style={{
                  color: 'var(--text-dim)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 11,
                }}
              >
                No tasks returned
              </div>
            )}
          </div>
        )}
      </AnimatedCollapse>

      <button
        type="button"
        onClick={() => setExpanded((value) => !value)}
        aria-expanded={expanded}
        className="inline-flex items-center justify-center gap-2 uppercase"
        style={{
          minWidth: 0,
          height: 26,
          padding: '0 10px',
          boxSizing: 'border-box',
          background: expanded ? 'var(--bg-elevated)' : 'var(--bg-surface)',
          border: '1px solid var(--border)',
          borderLeft: '2px solid var(--status-running)',
          borderRadius: 4,
          color: 'var(--text-secondary)',
          cursor: 'pointer',
          fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.06em',
          transition: 'background 150ms ease, border-color 150ms ease, color 150ms ease',
        }}
        onMouseEnter={(event) => {
          event.currentTarget.style.background = 'var(--bg-elevated)'
          event.currentTarget.style.borderColor = 'var(--border-strong)'
          event.currentTarget.style.borderLeftColor = 'var(--status-running)'
          event.currentTarget.style.color = 'var(--text-primary)'
        }}
        onMouseLeave={(event) => {
          event.currentTarget.style.background = expanded ? 'var(--bg-elevated)' : 'var(--bg-surface)'
          event.currentTarget.style.borderColor = 'var(--border)'
          event.currentTarget.style.borderLeftColor = 'var(--status-running)'
          event.currentTarget.style.color = 'var(--text-secondary)'
        }}
      >
        <span>TASK PROGRESS</span>
        <span style={{ color: 'var(--text-primary)' }}>
          {progress.done} / {progress.total}
        </span>
        <ChevronDown
          size={12}
          strokeWidth={1.5}
          style={{
            flexShrink: 0,
            transform: expanded ? 'rotate(180deg)' : 'rotate(0deg)',
            transition: 'transform 150ms ease',
          }}
        />
      </button>
    </div>
  )
}
