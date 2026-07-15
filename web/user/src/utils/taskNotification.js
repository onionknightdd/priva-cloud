// Parse a CLI-injected <task-notification> user message into structured fields.
//
// When a background task (a `Workflow` run or a `run_in_background` Bash) that
// was launched in an earlier turn finishes, the CLI re-invokes the model by
// injecting a synthetic *user* message of this exact shape:
//
//   <task-notification>
//   <task-id>btc55yywf</task-id>
//   <tool-use-id>call_1bb6b3de</tool-use-id>
//   <output-file>/tmp/…/tasks/btc55yywf.output</output-file>   (optional)
//   <status>completed</status>
//   <summary>Background command "…" completed (exit code 0)</summary>
//   </task-notification>
//
// Rendered raw, that XML shows up as an ugly user bubble; instead we surface it
// as a slim notification card (see TaskNotificationCard) with the model's
// summary streaming below as its own assistant turn. Returns null for anything
// that isn't a task-notification envelope.

// Terminal statuses that mean the task did NOT finish cleanly → red border.
const ERROR_STATUSES = new Set([
  'failed', 'stopped', 'killed', 'aborted', 'cancelled', 'error',
])

// Statuses we render a specific word for; anything else collapses to
// completed / failed by colour.
const KNOWN_STATUSES = new Set([
  'completed', 'failed', 'stopped', 'killed', 'aborted', 'cancelled',
])

export function parseTaskNotification(text) {
  if (typeof text !== 'string') return null
  // Require the full envelope — a bare mention of the tag (e.g. a user quoting
  // it) must not masquerade as a completion. The CLI always injects a complete,
  // single-frame <task-notification>…</task-notification>.
  if (!/<task-notification>[\s\S]*<\/task-notification>/.test(text)) return null

  const pick = (tag) => {
    const m = text.match(new RegExp(`<${tag}>([\\s\\S]*?)</${tag}>`))
    return m ? m[1].trim() : null
  }

  const status = (pick('status') || 'completed').toLowerCase()
  return {
    taskId: pick('task-id') || null,
    toolUseId: pick('tool-use-id') || null,
    status,
    summary: pick('summary') || '',
    outputFile: pick('output-file') || null,
  }
}

export function taskNotificationIsError(status) {
  return ERROR_STATUSES.has(String(status || '').toLowerCase())
}

// The i18n status word key: exact status when we translate it, else the
// completed/failed bucket implied by the colour.
export function taskNotificationStatusKey(status) {
  const s = String(status || '').toLowerCase()
  if (KNOWN_STATUSES.has(s)) return s
  return taskNotificationIsError(s) ? 'failed' : 'completed'
}
