"""读取账号 ar pod 上的 skill 清单，供 `/skill` 指令 / 自定义菜单 ``list_skill`` 渲染卡片。

复用 dial.py 完全相同的接入面：wake（AgentTenant 唯一的 scale-up 触发）+ 签名的
``X-Priva-Runner-Token``（ar pod 的 ``require_user`` 就是这张票），只是换成一个只读
GET。失败一律返回 ``None`` —— 调用方转成一句用户看得懂的话，绝不静默。
"""

from __future__ import annotations

import httpx

from priva_common.config import get_settings
from priva_common.logging import get_app_logger
from priva_common.runner_token import mint

from . import wake

logger = get_app_logger(__name__)

_TIMEOUT = httpx.Timeout(15.0, connect=10.0)


def _url(account_id: str) -> str:
    s = get_settings()
    ns = s.kubernetes.namespace_tenants
    port = s.kubernetes.runner_service_port
    # 末尾斜杠是路由本身（``@router.get("/")`` + prefix），去掉会吃一次 307。
    return f"http://ar-{account_id}.{ns}.svc:{port}/api/sandbox/resource/skills/"


async def list_skills(
    account_id: str,
    username: str | None,
    *,
    waker=None,
    transport: "httpx.AsyncBaseTransport | None" = None,
) -> dict | None:
    """``{"personal": [...], "groups": [...]}``，取不到返回 None。"""
    waker = waker or wake.wake_and_wait
    if not await waker(account_id):
        logger.warning("skills fetch: wake failed account={}", account_id)
        return None
    headers = {"X-Priva-Runner-Token": mint(account_id, username or "")}
    try:
        async with httpx.AsyncClient(trust_env=False, transport=transport, timeout=_TIMEOUT) as cx:
            resp = await cx.get(_url(account_id), headers=headers)
        if resp.status_code != 200:
            logger.warning("skills fetch {} -> {}: {}", account_id, resp.status_code,
                           resp.text[:200])
            return None
        data = resp.json()
        return data if isinstance(data, dict) else None
    except Exception:
        logger.exception("skills fetch crashed account={}", account_id)
        return None
