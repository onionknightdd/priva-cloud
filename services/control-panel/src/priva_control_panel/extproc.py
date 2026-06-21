"""control-panel ext_proc EPP — the routing brain agentgateway calls per runtime
request (Envoy External Processing, gRPC :9000).

Served with **grpclib** (pure-Python HTTP/2), not grpc.aio (C-core): agentgateway's
Rust ext_proc client did not interoperate with the C-core server (InvalidContentType);
grpclib's h2 stack is the workaround. The decision logic (handle_request_headers) and
the proto messages are unchanged.

On request headers: resolve the account from the platform JWT, wake the account's pod
(provisioner -> AgentTenant CR), and steer agentgateway to it by setting
``x-gateway-destination-endpoint`` = podIP:port plus the signed ``x-priva-runner-token``
the pod verifies. Cold/unauth -> an ext_proc immediate response (401 / "waking, retry").
"""

from __future__ import annotations

import asyncio
import datetime
import ssl
import tempfile
from urllib.parse import parse_qs, urlparse

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from envoy.config.core.v3.base_pb2 import HeaderValue, HeaderValueOption
from envoy.extensions.filters.http.ext_proc.v3 import processing_mode_pb2 as pm
from envoy.service.ext_proc.v3 import external_processor_pb2 as ep
from envoy.type.v3 import http_status_pb2

# Tell agentgateway, in our headers response, to stop sending us the body/trailers
# (the GIE EPP path buffers the request body for ext_proc; we only need headers, and
# not consuming the body was dropping it -> the pod saw an empty body -> 422). Header
# modes are set non-default so the override serializes (NONE=0 alone would be empty).
_MODE_OVERRIDE = pm.ProcessingMode(
    request_header_mode=pm.ProcessingMode.SEND,
    response_header_mode=pm.ProcessingMode.SKIP,
    request_body_mode=pm.ProcessingMode.NONE,
    response_body_mode=pm.ProcessingMode.NONE,
    request_trailer_mode=pm.ProcessingMode.SKIP,
    response_trailer_mode=pm.ProcessingMode.SKIP,
)
from grpclib.const import Cardinality, Handler
from grpclib.server import Server

from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

from . import provisioner
from .services.auth import authenticate_raw_token

logger = get_app_logger(__name__)

DEST_HEADER = "x-gateway-destination-endpoint"
RUNNER_TOKEN_HEADER = "x-priva-runner-token"
PROCESS_PATH = "/envoy.service.ext_proc.v3.ExternalProcessor/Process"


def _headers_to_dict(http_headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for h in http_headers.headers.headers:
        out[h.key.lower()] = h.raw_value.decode("utf-8", "replace") if h.raw_value else h.value
    return out


def _query_param(path: str, key: str) -> str | None:
    try:
        vals = parse_qs(urlparse(path).query).get(key)
        return vals[0] if vals else None
    except Exception:
        return None


WS_TOKEN_PREFIX = "priva.token."


def _token_from_subprotocol(header_value: str) -> str | None:
    """Pull the JWT out of the ``Sec-WebSocket-Protocol`` handshake header. The
    SPA offers ``priva.ws.v1, priva.token.<jwt>`` because a WS upgrade carries no
    body and no Authorization header for the edge to read — the token rides the
    subprotocol list instead. Returns the token, or None if absent."""
    for proto in header_value.split(","):
        proto = proto.strip()
        if proto.startswith(WS_TOKEN_PREFIX):
            return proto[len(WS_TOKEN_PREFIX):] or None
    return None


def _immediate(code: int, message: str) -> "ep.ProcessingResponse":
    return ep.ProcessingResponse(immediate_response=ep.ImmediateResponse(
        status=http_status_pb2.HttpStatus(code=code), body=message.encode()))


def _steer(endpoint: str, runner_token: str) -> "ep.ProcessingResponse":
    return ep.ProcessingResponse(
        mode_override=_MODE_OVERRIDE,  # stop the gateway from routing the body through us
        request_headers=ep.HeadersResponse(response=ep.CommonResponse(
            header_mutation=ep.HeaderMutation(set_headers=[
                HeaderValueOption(header=HeaderValue(key=DEST_HEADER, raw_value=endpoint.encode())),
                HeaderValueOption(header=HeaderValue(key=RUNNER_TOKEN_HEADER, raw_value=runner_token.encode())),
            ]))))


_EMPTY = {
    "request_headers": lambda: ep.ProcessingResponse(request_headers=ep.HeadersResponse()),
    "response_headers": lambda: ep.ProcessingResponse(response_headers=ep.HeadersResponse()),
    "request_trailers": lambda: ep.ProcessingResponse(request_trailers=ep.TrailersResponse()),
    "response_trailers": lambda: ep.ProcessingResponse(response_trailers=ep.TrailersResponse()),
}


def _passthrough_body(field: str, http_body) -> "ep.ProcessingResponse":
    """agentgateway sends body chunks to the EPP (mode_override is ignored). An empty
    BodyResponse drops the body, so echo the bytes back via body_mutation to forward
    them unchanged (covers both request and response bodies, buffered or streamed)."""
    resp = ep.BodyResponse(response=ep.CommonResponse(
        body_mutation=ep.BodyMutation(body=http_body.body)))
    return ep.ProcessingResponse(**{field: resp})


async def handle_request_headers(http_headers) -> "ep.ProcessingResponse":
    """Pure-ish EPP decision for one request's headers (unit-testable)."""
    headers = _headers_to_dict(http_headers)
    auth = headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    if not token:  # WS upgrade: token rides the Sec-WebSocket-Protocol header
        token = _token_from_subprotocol(headers.get("sec-websocket-protocol", ""))
    if not token:  # legacy fallback (older cached SPA bundles)
        token = _query_param(headers.get(":path", ""), "token")
    try:
        user = await authenticate_raw_token(token, headers.get("x-user-name"))
    except Exception:
        user = None
    if user is None or not getattr(user, "account_id", None):
        return _immediate(401, "Authentication required")
    try:
        endpoint = await asyncio.to_thread(provisioner.wake_and_wait, user.account_id)
    except Exception as exc:
        logger.warning("wake failed account={}: {}", user.account_id, exc)
        return _immediate(503, "agent runner unavailable, retry shortly")
    if not endpoint:
        return _immediate(503, "agent runner is waking, retry in a moment")
    return _steer(endpoint, mint(user.account_id, user.username))


class ExternalProcessor:
    """grpclib service implementing envoy.service.ext_proc.v3.ExternalProcessor/Process."""

    async def _process(self, stream) -> None:
        while True:
            req = await stream.recv_message()
            if req is None:
                break
            kind = req.WhichOneof("request")
            if kind == "request_headers":
                await stream.send_message(await handle_request_headers(req.request_headers))
            elif kind == "request_body":
                await stream.send_message(_passthrough_body("request_body", req.request_body))
            elif kind == "response_body":
                await stream.send_message(_passthrough_body("response_body", req.response_body))
            else:
                await stream.send_message(_EMPTY.get(kind, _EMPTY["request_headers"])())

    def __mapping__(self) -> dict:
        return {
            PROCESS_PATH: Handler(
                self._process, Cardinality.STREAM_STREAM, ep.ProcessingRequest, ep.ProcessingResponse
            )
        }


def _self_signed_ssl_context() -> ssl.SSLContext:
    """Self-signed h2 server context. agentgateway dials the InferencePool EPP over
    TLS (GIE convention) and skip-verifies in-cluster, so a self-signed cert suffices.
    Serving plaintext here is what caused the InvalidContentType (TLS into plaintext)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "control-panel")])
    now = datetime.datetime.utcnow()
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("control-panel"),
                x509.DNSName("control-panel.priva-cloud.svc.cluster.local"),
            ]),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    cf = tempfile.NamedTemporaryFile(delete=False, suffix=".crt")
    cf.write(cert.public_bytes(serialization.Encoding.PEM))
    cf.close()
    kf = tempfile.NamedTemporaryFile(delete=False, suffix=".key")
    kf.write(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.TraditionalOpenSSL,
                              serialization.NoEncryption()))
    kf.close()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cf.name, kf.name)
    ctx.set_alpn_protocols(["h2"])  # gRPC over HTTP/2
    return ctx


async def start_extproc_server(settings):
    server = Server([ExternalProcessor()])
    port = settings.edge.extproc_port
    await server.start("0.0.0.0", port, ssl=_self_signed_ssl_context())
    logger.info("control-panel ext_proc EPP (grpclib, TLS) serving on 0.0.0.0:{}", port)
    return server
