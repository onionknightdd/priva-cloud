import { memo, useMemo } from 'react'
import { MessagesSquare } from 'lucide-react'
import { useTranslation } from 'react-i18next'
import useChatStore from '../../stores/chatStore'
import { ToolIcon } from './ToolLine'
import {
  buildAgentCommunicationIndex,
  buildReceivedFromMainEvents,
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

  return (
    <div className={`tool-tree-child agent-communication-event is-${state}${isLast ? ' is-last' : ''}`}>
      <span className="chat-branch-connector" aria-hidden="true" />
      <div className="tool-tree-child-content agent-communication-branch">
        <div className={`agent-communication-label${state === 'running' ? ' is-running' : ''}`}>
          <ToolIcon icon={MessagesSquare} running={state === 'running'} size="1em" />
          <span className="agent-communication-label-text">{label}</span>
        </div>
        {body && (
          <div className="tool-line-details-tree agent-communication-body-tree">
            <span className="chat-branch-connector" aria-hidden="true" />
            <div className="tool-detail-block agent-communication-body">
              <div className="tool-detail-section">
                <pre className="tool-detail-code">{body}</pre>
              </div>
            </div>
          </div>
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
  const receivedFromMain = buildReceivedFromMainEvents(communicationIndex, ownerToolUseId)
  const actualMainBodies = new Set(
    directEvents
      .filter((block) => (
        block.type === 'agent_message'
        && resolveReceivedSource(communicationIndex, block, ownerToolUseId).isMain
      ))
      .map((block) => String(block.body || '').trim()),
  )
  const events = [
    ...directEvents,
    ...receivedFromMain.filter((block) => !actualMainBodies.has(block.body)),
  ]

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
