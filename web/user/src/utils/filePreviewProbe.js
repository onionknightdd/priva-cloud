const DEFAULT_POSITIVE_TTL_MS = 60_000
const DEFAULT_NEGATIVE_TTL_MS = 5_000
const DEFAULT_MAX_ENTRIES = 256
const DEFAULT_MAX_CONCURRENCY = 4

/**
 * Wrap a preview request with bounded concurrency, in-flight deduplication, and
 * short-lived result caching. The probe must resolve to file metadata or null.
 */
export function createFilePreviewProbe(probe, options = {}) {
  const positiveTtlMs = options.positiveTtlMs ?? DEFAULT_POSITIVE_TTL_MS
  const negativeTtlMs = options.negativeTtlMs ?? DEFAULT_NEGATIVE_TTL_MS
  const maxEntries = options.maxEntries ?? DEFAULT_MAX_ENTRIES
  const maxConcurrency = Math.max(1, options.maxConcurrency ?? DEFAULT_MAX_CONCURRENCY)
  const now = options.now || Date.now
  const cache = new Map()
  const queue = []
  let activeCount = 0

  const trimCache = () => {
    while (cache.size > maxEntries) {
      const oldestSettledKey = Array.from(cache).find(([, entry]) => !entry.promise)?.[0]
      // Keep in-flight/queued promises deduplicated even during a temporary
      // burst above the settled-cache limit; they are trimmed as they resolve.
      if (oldestSettledKey === undefined) break
      cache.delete(oldestSettledKey)
    }
  }

  const schedule = (work) => new Promise((resolve) => {
    queue.push({ work, resolve })

    const drain = () => {
      while (activeCount < maxConcurrency && queue.length > 0) {
        const job = queue.shift()
        activeCount += 1
        Promise.resolve()
          .then(job.work)
          .catch(() => null)
          .then(job.resolve)
          .finally(() => {
            activeCount -= 1
            drain()
          })
      }
    }

    drain()
  })

  return (path, cacheKey = path) => {
    if (!path) return Promise.resolve(null)

    const cached = cache.get(cacheKey)
    if (cached?.promise) return cached.promise
    if (cached && cached.expiresAt > now()) {
      // Refresh insertion order so the bounded cache behaves like a small LRU.
      cache.delete(cacheKey)
      cache.set(cacheKey, cached)
      return Promise.resolve(cached.value)
    }
    if (cached) cache.delete(cacheKey)

    const promise = schedule(() => probe(path)).then((value) => {
      const normalizedValue = value || null
      cache.set(cacheKey, {
        value: normalizedValue,
        expiresAt: now() + (normalizedValue ? positiveTtlMs : negativeTtlMs),
      })
      trimCache()
      return normalizedValue
    })

    cache.set(cacheKey, { promise, expiresAt: Number.POSITIVE_INFINITY })
    trimCache()
    return promise
  }
}
