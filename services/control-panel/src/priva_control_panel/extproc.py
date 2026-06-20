"""control-panel ext_proc EPP — the routing brain agentgateway calls per runtime
request (Envoy External Processing, gRPC :9000).

On request headers: resolve the account from the platform JWT, wake the account's
pod (provisioner -> AgentTenant CR), and steer agentgateway to it by setting
``x-gateway-destination-endpoint`` = podIP:port (the GIE EndpointPicker contract)
plus the signed ``x-priva-runner-token`` the pod verifies. Cold/unauth -> an
ext_proc immediate response (401 / "waking, retry"). CP never carries the bytes.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

import grpc
from envoy.config.core.v3.base_pb2 import HeaderValue, HeaderValueOption
from envoy.service.ext_proc.v3 import external_processor_pb2 as ep
from envoy.service.ext_proc.v3 import external_processor_pb2_grpc as epg
from envoy.type.v3 import http_status_pb2

from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

from . import provisioner
from .services.auth import authenticate_raw_token

logger = get_app_logger(__name__)

DEST_HEADER = "x-gateway-destination-endpoint"
RUNNER_TOKEN_HEADER = "x-priva-runner-token"


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


def _immediate(code: int, message: str) -> "ep.ProcessingResponse":
    return ep.ProcessingResponse(immediate_response=ep.ImmediateResponse(
        status=http_status_pb2.HttpStatus(code=code), body=message.encode()))


def _steer(endpoint: str, runner_token: str) -> "ep.ProcessingResponse":
    return ep.ProcessingResponse(request_headers=ep.HeadersResponse(response=ep.CommonResponse(
        header_mutation=ep.HeaderMutation(set_headers=[
            HeaderValueOption(header=HeaderValue(key=DEST_HEADER, raw_value=endpoint.encode())),
            HeaderValueOption(header=HeaderValue(key=RUNNER_TOKEN_HEADER, raw_value=runner_token.encode())),
        ]))))


_EMPTY = {
    "request_headers": lambda: ep.ProcessingResponse(request_headers=ep.HeadersResponse()),
    "response_headers": lambda: ep.ProcessingResponse(response_headers=ep.HeadersResponse()),
    "request_body": lambda: ep.ProcessingResponse(request_body=ep.BodyResponse()),
    "response_body": lambda: ep.ProcessingResponse(response_body=ep.BodyResponse()),
    "request_trailers": lambda: ep.ProcessingResponse(request_trailers=ep.TrailersResponse()),
    "response_trailers": lambda: ep.ProcessingResponse(response_trailers=ep.TrailersResponse()),
}


async def handle_request_headers(http_headers) -> "ep.ProcessingResponse":
    """Pure-ish EPP decision for one request's headers (unit-testable)."""
    headers = _headers_to_dict(http_headers)
    auth = headers.get("authorization", "")
    token = auth[7:] if auth.lower().startswith("bearer ") else None
    if not token:
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


class ExternalProcessor(epg.ExternalProcessorServicer):
    async def Process(self, request_iterator, context):
        async for req in request_iterator:
            kind = req.WhichOneof("request")
            if kind == "request_headers":
                yield await handle_request_headers(req.request_headers)
            else:
                yield _EMPTY.get(kind, _EMPTY["request_headers"])()


async def start_extproc_server(settings):
    server = grpc.aio.server()
    epg.add_ExternalProcessorServicer_to_server(ExternalProcessor(), server)
    addr = f"0.0.0.0:{settings.edge.extproc_port}"
    server.add_insecure_port(addr)
    await server.start()
    logger.info("control-panel ext_proc EPP serving on {}", addr)
    return server
