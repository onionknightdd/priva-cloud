export function getSplitParams() {
  if (typeof window === 'undefined') {
    return { embedded: false, sessionId: null, paneId: null }
  }
  const params = new URLSearchParams(window.location.search)
  return {
    embedded: params.get('splitPane') === '1',
    sessionId: params.get('sessionId') || null,
    paneId: params.get('paneId') || null,
  }
}

export function isSplitPane() {
  return getSplitParams().embedded
}

