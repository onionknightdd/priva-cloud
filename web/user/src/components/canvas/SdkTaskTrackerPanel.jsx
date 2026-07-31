import { useMemo, useState } from 'react'
import { ChevronDown } from 'lucide-react'
import { AnimatedChevron, AnimatedCollapse } from '@shared/components/shared/Accordion'
import useTaskStore from '../../stores/taskStore'
import {
  getSdkTaskProgress,
  getSdkTaskRoundTasks,
} from '../../utils/sdkTaskTracker'
import SdkTaskTrackerRows from '../tasks/SdkTaskTrackerRows'

export default function SdkTaskTrackerPanel() {
  const tracker = useTaskStore((state) => state.sdkTaskTracker)
  const [roundOverrides, setRoundOverrides] = useState({})
  const rounds = useMemo(
    () => tracker.roundOrder
      .map((id) => tracker.rounds[id])
      .filter((round) => round?.hasTaskEvents),
    [tracker],
  )
  const totalTasks = useMemo(
    () => rounds.reduce((total, round) => total + getSdkTaskRoundTasks(round).length, 0),
    [rounds],
  )

  if (rounds.length === 0) return null

  return (
    <section
      className="py-1"
      style={{
        minWidth: 0,
        boxSizing: 'border-box',
        borderBottom: '1px solid var(--border)',
      }}
    >
      <div
        className="flex items-center gap-1 w-full px-3 py-1"
        style={{
          minWidth: 0,
          color: 'var(--text-dim)',
          fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
          fontSize: 10,
          fontWeight: 600,
          letterSpacing: '0.06em',
        }}
      >
        <span className="flex-1">TASK TRACKERS</span>
        <span className="font-light whitespace-nowrap" style={{ letterSpacing: 0 }}>
          {rounds.length} ROUNDS · {totalTasks} TASKS
        </span>
      </div>

      {rounds.map((round) => {
        const tasks = getSdkTaskRoundTasks(round)
        const progress = getSdkTaskProgress(tasks)
        const expanded = Object.prototype.hasOwnProperty.call(roundOverrides, round.id)
          ? roundOverrides[round.id]
          : round.id === tracker.currentRoundId
        const bodyId = `sdk-task-round-${round.id}`

        return (
          <div
            key={round.id}
            style={{
              minWidth: 0,
              borderTop: '1px solid var(--border-subtle)',
            }}
          >
            <button
              type="button"
              onClick={() => setRoundOverrides((current) => ({
                ...current,
                [round.id]: !expanded,
              }))}
              aria-expanded={expanded}
              aria-controls={bodyId}
              className="flex items-center gap-1 w-full"
              style={{
                minWidth: 0,
                padding: '6px 10px',
                background: 'transparent',
                border: 'none',
                borderLeft: `2px solid ${round.id === tracker.currentRoundId ? 'var(--status-running)' : 'var(--status-idle)'}`,
                color: 'var(--text-secondary)',
                cursor: 'pointer',
                textAlign: 'left',
                transition: 'background 150ms ease, border-left-color 150ms ease',
              }}
              onMouseEnter={(event) => {
                event.currentTarget.style.background = 'var(--bg-elevated)'
              }}
              onMouseLeave={(event) => {
                event.currentTarget.style.background = 'transparent'
              }}
            >
              <AnimatedChevron open={expanded} style={{ color: 'var(--text-dim)' }}>
                <ChevronDown size={10} strokeWidth={1.5} />
              </AnimatedChevron>
              <span
                className="flex-shrink-0 uppercase font-semibold"
                style={{
                  color: 'var(--text-secondary)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 10,
                  letterSpacing: '0.06em',
                }}
              >
                ROUND {round.number}
              </span>
              <span
                className="truncate min-w-0 flex-1"
                title={round.title}
                style={{ color: 'var(--text-secondary)', fontSize: 11 }}
              >
                {round.title}
              </span>
              <span
                className="flex-shrink-0 font-light whitespace-nowrap"
                style={{
                  color: 'var(--text-dim)',
                  fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                  fontSize: 10,
                }}
              >
                {progress.done}/{progress.total}
              </span>
            </button>

            <AnimatedCollapse open={expanded} id={bodyId} animateHeight={false}>
              {() => (
                tasks.length > 0 ? (
                  <SdkTaskTrackerRows tasks={tasks} compact />
                ) : (
                  <div
                    className="px-3 py-2"
                    style={{
                      color: 'var(--text-dim)',
                      fontFamily: "'JetBrains Mono', 'Source Han Mono SC', monospace",
                      fontSize: 10,
                    }}
                  >
                    No tasks returned
                  </div>
                )
              )}
            </AnimatedCollapse>
          </div>
        )
      })}
    </section>
  )
}
