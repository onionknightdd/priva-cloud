import { getToken } from '@shared/api/tokenStore'

/**
 * WebSocket auth.
 *
 * The edge (agentgateway ext_proc EPP) authenticates a WS on the UPGRADE
 * request, which carries no body — so it can read neither an init frame nor a
 * `fetch`-style Authorization header. We therefore pass the JWT as a WebSocket
 * subprotocol: it rides the `Sec-WebSocket-Protocol` handshake header instead of
 * the URL, keeping the token out of gateway access logs and browser history.
 *
 * Two protocols are offered: the `priva.ws.v1` sentinel (which the server echoes
 * back so the browser handshake completes) and `priva.token.<jwt>` (which the
 * edge reads for auth and never echoes back).
 *
 * Admin "console into another account" adds a third entry,
 * `priva.target.<base64url(username)>`: the EPP, when the authenticated caller is
 * an admin, resolves that username to its account and steers the WS to THAT
 * account's pod instead of the caller's own. The username is base64url-encoded
 * (no padding) so it is always a valid subprotocol token regardless of its chars.
 */
export const WS_SUBPROTOCOL = 'priva.ws.v1'
const WS_TOKEN_PREFIX = 'priva.token.'
const WS_TARGET_PREFIX = 'priva.target.'

// base64url without padding — safe for the Sec-WebSocket-Protocol token grammar.
function b64urlNoPad(str) {
  return btoa(unescape(encodeURIComponent(str)))
    .replace(/\+/g, '-')
    .replace(/\//g, '_')
    .replace(/=+$/, '')
}

export function wsProtocols(targetUsername) {
  const token = getToken()
  const protos = token ? [WS_SUBPROTOCOL, `${WS_TOKEN_PREFIX}${token}`] : [WS_SUBPROTOCOL]
  if (targetUsername) protos.push(`${WS_TARGET_PREFIX}${b64urlNoPad(targetUsername)}`)
  return protos
}
