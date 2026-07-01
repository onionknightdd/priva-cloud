/**
 * parseWorkflowMeta — tolerant, bounded parse of the `export const meta = {…}`
 * literal at the head of a generated Workflow script.
 *
 * The SDK guarantees `meta` is a PURE LITERAL (no variables / calls / spreads),
 * so `name`, `description`, and `phases[].{title,detail}` are reliably present
 * and parseable. Agents are NOT parseable (built dynamically, models assigned
 * at runtime) — they come live from `task_progress` deltas.
 *
 * NEVER eval / new Function the source — it is agent-generated. This is a
 * regex + balanced-scan reader only. Returns
 *   { name, description, phases: [{ index, title, detail, agents? }] }
 * or `null` when meta is absent/unparseable (it never throws). `index` is
 * 1-based to match the global phase index used by the live `workflow_phase`
 * deltas. `agents` is the per-phase agent count — derived by counting `agent(`
 * calls between `phase('…')` markers in the script body (see countAgentsByPhase),
 * or an explicit meta `agents:` if the script declares one. Summed across phases
 * it lets the card show the full workflow total before later phases' agents spawn.
 */

const MAX_SCAN = 40_000

// Walk from an opening bracket to its matching close, respecting string
// literals (so braces/brackets inside a string don't unbalance the scan).
// Returns the inclusive slice including both brackets, or null.
function sliceBalanced(str, openIdx, open = '{', close = '}') {
  if (str[openIdx] !== open) return null
  let depth = 0
  let quote = null
  const end = Math.min(str.length, openIdx + MAX_SCAN)
  for (let i = openIdx; i < end; i += 1) {
    const ch = str[i]
    if (quote) {
      if (ch === '\\') { i += 1; continue }   // skip escaped char
      if (ch === quote) quote = null
      continue
    }
    if (ch === "'" || ch === '"' || ch === '`') { quote = ch; continue }
    if (ch === open) depth += 1
    else if (ch === close) {
      depth -= 1
      if (depth === 0) return str.slice(openIdx, i + 1)
    }
  }
  return null
}

// Pull a single/double/backtick-quoted string value for `key:` from a block.
// Returns the (trimmed) contents or null.
function matchString(block, key) {
  const re = new RegExp(`${key}\\s*:\\s*(['"\`])([\\s\\S]*?)\\1`)
  const m = block.match(re)
  return m ? m[2].trim() : null
}

// Pull a bare non-negative integer value for `key:` (e.g. `agents: 2`). Returns
// the number or null. Used for the optional explicit per-phase agent count.
function matchNumber(block, key) {
  const m = block.match(new RegExp(`${key}\\s*:\\s*(\\d+)`))
  return m ? parseInt(m[1], 10) : null
}

// Deterministic per-phase agent count from the script BODY. Generated workflow
// scripts have a fixed shape: `phase('Name')` calls delimit phases, and each
// phase spawns agents via `agent(...)` calls. A single-pass scan — skipping
// strings/template literals and comments so an `agent(` or `phase('x')` inside a
// prompt or comment can't be miscounted — tallies `agent(` calls between
// consecutive `phase('…')` markers. Returns an ordered list `[{ name, count }]`.
//
// A literal `agent()` per spawn counts as 1; a dynamic `arr.map(() => agent())`
// fan-out reads as 1 here (one literal call), but the header uses
// max(agentsSeen, declaredTotal), so once those agents actually spawn the live
// count wins — this never over-reports and never hides running agents.
function countAgentsByPhase(script) {
  const s = String(script)
  const n = Math.min(s.length, 200_000)
  const isIdStart = (c) => c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c === '_' || c === '$'
  const isIdPart = (c) => isIdStart(c) || c >= '0' && c <= '9'
  const order = []
  let cur = null
  let i = 0
  while (i < n) {
    const ch = s[i]
    // comments
    if (ch === '/' && s[i + 1] === '/') { i += 2; while (i < n && s[i] !== '\n') i += 1; continue }
    if (ch === '/' && s[i + 1] === '*') { i += 2; while (i < n && !(s[i] === '*' && s[i + 1] === '/')) i += 1; i += 2; continue }
    // string / template literal — skip contents (respect escapes)
    if (ch === "'" || ch === '"' || ch === '`') {
      const q = ch; i += 1
      while (i < n) { if (s[i] === '\\') { i += 2; continue } if (s[i] === q) { i += 1; break } i += 1 }
      continue
    }
    // identifier
    if (isIdStart(ch)) {
      let j = i + 1
      while (j < n && isIdPart(s[j])) j += 1
      const word = s.slice(i, j)
      if (word === 'phase' || word === 'agent') {
        let k = j
        while (k < n && /\s/.test(s[k])) k += 1
        if (s[k] === '(') {
          if (word === 'agent') {
            if (cur) cur.count += 1
          } else {
            let m = k + 1
            while (m < n && /\s/.test(s[m])) m += 1
            const qc = s[m]
            let name = null
            if (qc === "'" || qc === '"' || qc === '`') {
              m += 1
              let buf = ''
              while (m < n) {
                if (s[m] === '\\') { buf += s[m + 1] || ''; m += 2; continue }
                if (s[m] === qc) break
                buf += s[m]; m += 1
              }
              name = buf
            }
            cur = { name, count: 0 }
            order.push(cur)
          }
        }
      }
      i = j
      continue
    }
    i += 1
  }
  return order
}

export function parseWorkflowMeta(script) {
  if (!script || typeof script !== 'string') return null
  try {
    const metaAt = script.search(/export\s+const\s+meta\s*=\s*\{/)
    if (metaAt < 0) return null
    const braceStart = script.indexOf('{', metaAt)
    if (braceStart < 0) return null
    const block = sliceBalanced(script, braceStart, '{', '}')
    if (!block) return null

    const name = matchString(block, 'name')
    const description = matchString(block, 'description')

    const phases = []
    const phasesKey = block.match(/phases\s*:\s*\[/)
    if (phasesKey) {
      const arrStart = block.indexOf('[', phasesKey.index)
      const arr = sliceBalanced(block, arrStart, '[', ']')
      if (arr) {
        // Each phase is a flat `{ title:'…', detail:'…' }` object (no nesting).
        const objRe = /\{[\s\S]*?\}/g
        let m
        while ((m = objRe.exec(arr)) !== null) {
          const objText = m[0]
          const title = matchString(objText, 'title')
          const detail = matchString(objText, 'detail')
          const agents = matchNumber(objText, 'agents')
          if (title || detail || agents != null) {
            phases.push({
              index: phases.length + 1,
              title: title || undefined,
              detail: detail || undefined,
              agents: agents != null ? agents : undefined,
            })
          }
        }
      }
    }

    // Fill each phase's agent count by scanning the script body (deterministic).
    // An explicit meta `agents:` value, if any, was already set above and wins.
    if (phases.length > 0) {
      const counted = countAgentsByPhase(script)
      if (counted.length > 0) {
        phases.forEach((p, idx) => {
          if (p.agents != null) return
          const byName = p.title ? counted.find((c) => c.name === p.title) : null
          const c = byName || counted[idx]
          if (c && c.count > 0) p.agents = c.count
        })
      }
    }

    if (!name && !description && phases.length === 0) return null
    return { name: name || undefined, description: description || undefined, phases }
  } catch {
    return null
  }
}

export default parseWorkflowMeta
