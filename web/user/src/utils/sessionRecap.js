import { fetchSessionRecap } from '../api/sessions'

// The backend generates a recap in a detached task after the turn's last
// event, so asking the instant a turn ends usually returns the *previous* one.
// Re-ask a couple of times, spread out enough to cover a slow model call
// without turning into a polling loop.
const ATTEMPT_DELAYS_MS = [0, 3000, 8000]

// One in-flight poll per session. A user firing turns back to back would
// otherwise stack pollers that race to write older text over newer.
const generations = new Map()

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/**
 * Pull a session's recap into its chat slice.
 *
 * @param {string|null} sessionId
 * @param {() => object} chatSlice  Accessor for the runtime's chat slice
 *   (`S.chat()` in useSSE, `useChatStore.getState` elsewhere). Passed as a
 *   thunk because a resume rotates the slice under us.
 * @param {object} [opts]
 * @param {number} [opts.knownTurns]  Message count already on screen. Polling
 *   stops as soon as the server reports a higher one.
 * @param {boolean} [opts.poll]  Retry while the answer is still stale. Only
 *   turn-end needs this; opening a session reads whatever already exists, and
 *   retrying there would be three requests for a session that may simply have
 *   no recap at all.
 */
export async function refreshSessionRecap(sessionId, chatSlice, opts = {}) {
  if (!sessionId) return
  const { knownTurns = 0, poll = false } = opts

  const generation = (generations.get(sessionId) || 0) + 1
  generations.set(sessionId, generation)

  for (const delay of poll ? ATTEMPT_DELAYS_MS : [0]) {
    if (delay) await sleep(delay)
    if (generations.get(sessionId) !== generation) return  // superseded

    let data
    try {
      data = await fetchSessionRecap(sessionId)
    } catch {
      // Recaps are optional; a failed read is not worth surfacing.
      return
    }
    if (generations.get(sessionId) !== generation) return

    if (data?.recap) {
      // setRecap ignores anything not newer than what it holds, so a stale
      // read here is a no-op rather than a regression.
      chatSlice().setRecap(data.recap, data.turns || 0)
      if ((data.turns || 0) > knownTurns) break
    }
  }

  if (generations.get(sessionId) === generation) generations.delete(sessionId)
}
