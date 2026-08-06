import ToolLine from './ToolLine'
import useFileOpsStore from '../../stores/fileOpsStore'
import { findFileOp } from '../../utils/toolPresentation'

export default function FileToolCard({
  kind,
  block = null,
  op = null,
  reverted = false,
  compact = false,
  livePreview = false,
}) {
  const matchedOp = useFileOpsStore((state) => op || findFileOp(state.fileOps, block))
  return (
    <ToolLine
      kind={kind}
      block={block}
      op={matchedOp}
      reverted={reverted}
      compact={compact}
      livePreview={livePreview}
    />
  )
}
