/**
 * Collect one visible run of collapsible tool blocks.
 *
 * SSE envelopes are transport boundaries, not message-flow boundaries, so
 * blocks remain in the same run even when their processGroupId values differ.
 * Empty text blocks are transparent; any other visible block ends the run.
 */
export function collectToolRun(blocks, startIndex, isToolBlock, isTransparentBlock) {
  const run = [blocks[startIndex]]
  const toolIndexes = [startIndex]
  let endIndex = startIndex
  let lastToolIndex = startIndex

  while (endIndex + 1 < blocks.length) {
    const nextBlock = blocks[endIndex + 1]
    if (isToolBlock(nextBlock)) {
      endIndex += 1
      lastToolIndex = endIndex
      run.push(blocks[endIndex])
      toolIndexes.push(endIndex)
      continue
    }
    if (isTransparentBlock(nextBlock)) {
      endIndex += 1
      continue
    }
    break
  }

  return { run, toolIndexes, endIndex, lastToolIndex }
}
