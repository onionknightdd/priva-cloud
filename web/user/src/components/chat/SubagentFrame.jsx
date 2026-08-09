import { memo, useId, useState } from 'react'
import { Bot, ChevronRight } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import useTaskStore from '../../stores/taskStore'
import useUiStore from '@shared/stores/uiStore'
import useChatStore from '../../stores/chatStore'
import {
  getAgentDisplayName,
  getAgentLifecycle,
} from '../../utils/agentToolLifecycle'
import { isSendMessageTool } from '../../utils/agentCommunication'
import { ToolIcon } from './ToolLine'
import AgentCommunicationTrace from './AgentCommunicationTrace'

const STATUS_KEYS = {
  running: 'started',
  completed: 'completed',
  terminated: 'terminated',
  failed: 'failed',
}

/**
 * Agent / Task tool use in the assistant timeline. The compact identity row
 * keeps its existing Canvas focus behavior; peer communications are attached
 * directly below it in the main message flow.
 */
function SubagentFrame({ block, reverted = false }) {
  const { t } = useTranslation()
  const communicationsId = useId()
  const [communicationsExpanded, setCommunicationsExpanded] = useState(false)
  const focusSubagent = useTaskStore((state) => state.focusSubagent)
  const showCanvas = useUiStore((state) => state.showCanvas)
  const setActiveCanvasTab = useUiStore((state) => state.setActiveCanvasTab)
  const hasCommunications = useChatStore((state) => (
    (state.subagentContent[block.id] || []).some((entry) => (
      entry?.type === 'agent_message' || isSendMessageTool(entry)
    ))
  ))
  const lifecycle = getAgentLifecycle(block)
  const running = lifecycle === 'running'
  const failed = lifecycle === 'failed'
  const agentName = getAgentDisplayName(block)
  const statusText = t(`toolCall.agent.${STATUS_KEYS[lifecycle]}`)
  const title = ['Agent', agentName, statusText].filter(Boolean).join(' · ')
  const communicationsToggleLabel = t(
    communicationsExpanded
      ? 'toolCall.agent.collapseMessages'
      : 'toolCall.agent.expandMessages',
  )

  const handleClick = (event) => {
    event.stopPropagation()
    if (!block.id) return
    focusSubagent(block.id)
    showCanvas()
    setActiveCanvasTab('tasks')
  }

  return (
    <div className="agent-tool-frame">
      <div className="agent-tool-line-row">
        <button
          type="button"
          className={`agent-tool-line${running ? ' is-running' : ''}${failed ? ' is-failed' : ''}${reverted ? ' is-reverted' : ''}`}
          data-tool-card
          data-subagent-frame
          data-tool-use-id={block.id}
          title={title}
          aria-label={title}
          onClick={handleClick}
        >
          <span className="agent-tool-chip">
            <ToolIcon icon={Bot} running={running} size="1em" />
            <span className="agent-tool-chip-copy">
              <span className="agent-tool-chip-part is-kind">Agent</span>
              <span className="agent-tool-chip-part is-name">{agentName}</span>
            </span>
          </span>
          <span className="agent-tool-status" aria-live="polite">
            {statusText}
          </span>
        </button>
        {hasCommunications && (
          <button
            type="button"
            className="agent-communication-toggle"
            aria-expanded={communicationsExpanded}
            aria-controls={communicationsId}
            aria-label={communicationsToggleLabel}
            title={communicationsToggleLabel}
            onClick={(event) => {
              event.stopPropagation()
              setCommunicationsExpanded((expanded) => !expanded)
            }}
          >
            <ChevronRight
              className="agent-communication-toggle-chevron"
              size="1em"
              strokeWidth={1.5}
              aria-hidden="true"
              style={{ transform: communicationsExpanded ? 'rotate(90deg)' : 'none' }}
            />
          </button>
        )}
      </div>
      <AnimatedCollapse
        open={hasCommunications && communicationsExpanded}
        id={communicationsId}
      >
        <AgentCommunicationTrace ownerToolUseId={block.id} />
      </AnimatedCollapse>
    </div>
  )
}

export default memo(SubagentFrame)
