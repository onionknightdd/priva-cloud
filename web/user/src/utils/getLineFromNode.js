function rowLineNumber(row) {
  return Number(row?.dataset?.lineNumber) || (row ? row.rowIndex + 1 : null)
}

// Resolve a range container + offset to a line number.
// When selecting across multiple table cells, browsers often set the range
// container to <tbody> or <table> with an offset pointing at child rows,
// rather than a node inside a specific <td>.
export default function getLineFromNode(node, offset) {
  if (!node) return null
  let el = node.nodeType === 3 ? node.parentElement : node
  if (!el) return null
  // If container is tbody/table, use offset to pick the row directly
  if (el.tagName === 'TBODY') {
    const row = el.rows[Math.min(offset, el.rows.length - 1)]
    return rowLineNumber(row)
  }
  if (el.tagName === 'TABLE') {
    const tbody = el.tBodies[0]
    if (!tbody) return null
    const row = tbody.rows[Math.min(offset, tbody.rows.length - 1)]
    return rowLineNumber(row)
  }
  // Virtualized renderer: the container may be the row list itself, with the
  // offset pointing at child row divs that carry data-line-number.
  if (el.dataset?.lineNumber == null
    && el.children?.length
    && el.children[0]?.dataset?.lineNumber != null) {
    const child = el.children[Math.min(offset, el.children.length - 1)]
    return rowLineNumber(child)
  }
  // Normal case: walk up from a node inside a cell to its row — either a <tr>
  // or a virtualized row div carrying data-line-number.
  while (el && el.tagName !== 'TR' && el.dataset?.lineNumber == null) el = el.parentElement
  if (!el) return null
  return rowLineNumber(el)
}
