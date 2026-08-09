import { memo, useId, useMemo, useState } from 'react'
import { ChevronRight, MessagesSquare } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import { AnimatedCollapse } from '@shared/components/shared/Accordion'
import useChatStore from '../../stores/chatStore'
import { ToolIcon } from './ToolLine'
import { formatDateTime, formatTimeOfDay } from '../../utils/formatTime'
import {
  buildAgentCommunicationIndex,
  getSendMessageBody,
  getSendMessageTarget,
  isSendMessageTool,
  parseSendMessageResult,
  resolveReceivedSource,
  resolveSentTarget,
} from '../../utils/agentCommunication'

const EMPTY_AGENT_BLOCKS = []

function communicationPartyLabel(party) {
  if (party?.isMain) return 'main'
  const name = String(party?.name || '').trim()
  if (/^agent(?:\s|$)/i.test(name)) return name
  return `Agent · ${name || party?.agentId || 'unknown'}`
}

function CommunicationEvent({ block, ownerToolUseId, communicationIndex, isLast }) {
  const { t } = useTranslation()
  const bodyId = useId()
  const [expanded, setExpanded] = useState(false)
  const isReceived = block.type === 'agent_message'
  const state = isReceived ? 'success' : parseSendMessageResult(block)
  const body = isReceived ? String(block.body || '').trim() : getSendMessageBody(block)
  const party = isReceived
    ? resolveReceivedSource(communicationIndex, block, ownerToolUseId)
    : resolveSentTarget(communicationIndex, getSendMessageTarget(block))
  const counterparty = communicationPartyLabel(party)
  const label = isReceived
    ? t('toolCall.agent.messageReceived', { source: counterparty })
    : state === 'running'
      ? t('toolCall.agent.messageSending', { target: counterparty })
      : state === 'error'
        ? t('toolCall.agent.messageSendFailed', { target: counterparty })
        : t('toolCall.agent.messageSent', { target: counterparty })
  const timestamp = block.timestamp || block.endTime || block.startTime || null
  const timestampMs = timestamp == null ? null : new Date(timestamp).getTime()
  const hasTimestamp = Number.isFinite(timestampMs)
  const timestampText = hasTimestamp ? formatTimeOfDay(timestampMs) : ''

  return (
    <div className={`tool-tree-child agent-communication-event is-${state}${isLast ? ' is-last' : ''}`}>
      <span className="chat-branch-connector" aria-hidden="true" />
      <div className="tool-tree-child-content agent-communication-branch">
        <button
          type="button"
          className={`agent-communication-label${state === 'running' ? ' is-running' : ''}`}
          aria-expanded={expanded}
          aria-controls={body ? bodyId : undefined}
          onClick={() => { if (body) setExpanded((open) => !open) }}
          style={{ cursor: body ? 'pointer' : 'default' }}
        >
          <ToolIcon icon={MessagesSquare} running={state === 'running'} size="1em" />
          <span className="agent-communication-label-text">{label}</span>
          {timestampText && (
            <time
              className="agent-communication-timestamp"
              dateTime={new Date(timestampMs).toISOString()}
              title={formatDateTime(timestampMs)}
            >
              {timestampText}
            </time>
          )}
          {body && (
            <ChevronRight
              className="agent-communication-chevron"
              size="1em"
              strokeWidth={1.5}
              aria-hidden="true"
              style={{ transform: expanded ? 'rotate(90deg)' : 'none' }}
            />
          )}
        </button>
        {body && (
          <AnimatedCollapse open={expanded} id={bodyId}>
            <div className="tool-line-details-tree agent-communication-body-tree">
              <span className="chat-branch-connector" aria-hidden="true" />
              <div className="agent-communication-body">
                {body}
              </div>
            </div>
          </AnimatedCollapse>
        )}
      </div>
    </div>
  )
}

function AgentCommunicationTrace({ ownerToolUseId }) {
  const messages = useChatStore((state) => state.messages)
  const ownerBlocks = useChatStore(
    (state) => state.subagentContent[ownerToolUseId] || EMPTY_AGENT_BLOCKS,
  )
  const chatSnapshot = useChatStore.getState()
  const communicationIndex = buildAgentCommunicationIndex(
    messages,
    chatSnapshot.subagentContent,
  )
  const directEvents = useMemo(
    () => ownerBlocks.filter((block) => (
      block?.type === 'agent_message' || isSendMessageTool(block)
    )),
    [ownerBlocks],
  )
  const events = directEvents
    .map((block, order) => {
      const rawTimestamp = block.timestamp || block.endTime || block.startTime
      const timestamp = rawTimestamp == null ? null : new Date(rawTimestamp).getTime()
      const sequence = Number(block.sequence)
      return {
        block,
        order,
        timestamp: Number.isFinite(timestamp) ? timestamp : null,
        sequence: Number.isFinite(sequence) ? sequence : null,
      }
    })
    .sort((left, right) => {
      if (left.timestamp != null && right.timestamp != null && left.timestamp !== right.timestamp) {
        return left.timestamp - right.timestamp
      }
      if (left.sequence != null && right.sequence != null && left.sequence !== right.sequence) {
        return left.sequence - right.sequence
      }
      return left.order - right.order
    })
    .map((entry) => entry.block)

  if (events.length === 0) return null

  return (
    <div className="tool-tree agent-communication-trace">
      {events.map((block, index) => (
        <CommunicationEvent
          key={block.id || `agent-communication-${index}`}
          block={block}
          ownerToolUseId={ownerToolUseId}
          communicationIndex={communicationIndex}
          isLast={index === events.length - 1}
        />
      ))}
    </div>
  )
}

export default memo(AgentCommunicationTrace)
