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
 */
export const WS_SUBPROTOCOL = 'priva.ws.v1'
const WS_TOKEN_PREFIX = 'priva.token.'

export function wsProtocols() {
  const token = getToken()
  return token ? [WS_SUBPROTOCOL, `${WS_TOKEN_PREFIX}${token}`] : [WS_SUBPROTOCOL]
}
