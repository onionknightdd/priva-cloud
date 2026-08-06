import ToolLine from './ToolLine'

export default function ToolCallCard({ block, reverted = false, compact = false, livePreview = false }) {
  return (
    <ToolLine
      block={block}
      reverted={reverted}
      compact={compact}
      livePreview={livePreview}
    />
  )
}
