"""Feishu image / 图文 (post) DM → AgentRunRequest.images pipeline.

Covers the three connector-side stages: lark_ws parsing (post flattening, magic-byte
sniffing, _dispatch normalization), worker fetch/cap/note orchestration, and the
dial pass-through contract (images kwarg captured by the FakeDialer). The runner side
(validation → vision content blocks) is already exercised by its own tests.
"""

import asyncio
import base64
import json
import os
import sys

# same shim as test_connector.py — lark_oapi stays lazily imported / not required here
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector.lark_ws import (  # noqa: E402
    LarkTransport,
    _parse_post_content,
    _sniff_image_media_type,
)
from priva_channel_connector.sse import RunOutcome  # noqa: E402
from priva_channel_connector.transport import InboundMessage  # noqa: E402
from priva_channel_connector.worker import (  # noqa: E402
    _IMAGE_FALLBACK_PROMPT,
    _MAX_IMAGE_BYTES,
)

from tests.api.test_connector import FakeDialer, _worker_with  # noqa: E402

_PNG = b"\x89PNG\r\n\x1a\n" + b"png-body"
_JPEG = b"\xff\xd8\xff\xe0" + b"jpeg-body"


def _img_msg(account_id, text, keys, chat="oc_1"):
    return InboundMessage(account_id=account_id, sender_open_id="ou_sender", chat_id=chat,
                          text=text, message_id="om_img", image_keys=tuple(keys))


# --- post (图文) flattening --------------------------------------------------
def test_parse_post_content_text_links_and_images():
    content = {
        "title": "周报",
        "content": [
            [{"tag": "text", "text": "第一段 "}, {"tag": "a", "text": "链接", "href": "https://x.y"}],
            [{"tag": "img", "image_key": "img_k1"}],
            [{"tag": "text", "text": "第二段"}, {"tag": "img", "image_key": "img_k2"}],
            [{"tag": "at", "user_id": "ou_z"}],   # unsupported run → dropped
        ],
    }
    text, keys = _parse_post_content(content)
    assert text == "周报\n第一段 链接 (https://x.y)\n第二段"
    assert keys == ["img_k1", "img_k2"]  # document order preserved


def test_parse_post_content_empty():
    assert _parse_post_content({}) == ("", [])


# --- magic-byte sniffing -----------------------------------------------------
def test_sniff_media_types():
    assert _sniff_image_media_type(_PNG) == "image/png"
    assert _sniff_image_media_type(_JPEG) == "image/jpeg"
    assert _sniff_image_media_type(b"GIF89a" + b"x") == "image/gif"
    assert _sniff_image_media_type(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert _sniff_image_media_type(b"BM6\x00") is None  # bmp: not runner-accepted


# --- _dispatch normalization -------------------------------------------------
class _Obj:
    pass


def _event_data(mtype, content: dict):
    data = _Obj()
    data.event = _Obj()
    m = _Obj()
    m.message_type = mtype
    m.content = json.dumps(content)
    m.chat_id = "oc_1"
    m.message_id = "om_1"
    data.event.message = m
    data.event.sender = _Obj()
    data.event.sender.sender_id = _Obj()
    data.event.sender.sender_id.open_id = "ou_s"
    return data


def _dispatch_and_collect(datas):
    received = []

    async def on_msg(m):
        received.append(m)

    t = LarkTransport("acct", "app", "secret", "feishu", on_msg)

    async def main():
        t._loop = asyncio.get_running_loop()
        for d in datas:
            t._dispatch(d)
        await asyncio.sleep(0.05)

    asyncio.run(main())
    return received


def test_dispatch_image_and_post_messages():
    got = _dispatch_and_collect([
        _event_data("image", {"image_key": "img_solo"}),
        _event_data("post", {"title": "", "content": [
            [{"tag": "text", "text": "看这两张"}],
            [{"tag": "img", "image_key": "k1"}, {"tag": "img", "image_key": "k2"}],
        ]}),
        _event_data("file", {"file_key": "f1"}),          # unsupported type → skipped
        _event_data("post", {"title": "", "content": []}),  # empty post → skipped
    ])
    assert len(got) == 2
    assert (got[0].text, got[0].image_keys) == ("", ("img_solo",))
    assert (got[1].text, got[1].image_keys) == ("看这两张", ("k1", "k2"))


def test_group_message_logs_and_caches_chat_name(monkeypatch):
    received = []

    async def on_msg(m):
        received.append(m)

    t = LarkTransport("acct", "app", "secret", "feishu", on_msg)
    monkeypatch.setattr(LarkTransport, "_get_chat_name_sync", lambda self, cid: "测试群")
    d = _event_data("text", {"text": "hi"})
    d.event.message.chat_type = "group"

    async def main():
        t._loop = asyncio.get_running_loop()
        t._dispatch(d)
        await asyncio.sleep(0.1)

    asyncio.run(main())
    # Phase 2: transport 放行群消息（裁决在 worker: effective_group_enabled + @ 触发）
    assert len(received) == 1
    assert received[0].chat_type == "group"
    assert received[0].mentioned is False  # 无 @ — worker 会跳过
    assert t._chat_names == {"oc_1": "测试群"}  # 群名仍拉取并缓存


# --- worker orchestration ----------------------------------------------------
def _run_worker(msg, images_by_key):
    dialer = FakeDialer(RunOutcome(session_id="s", text="ok"))

    async def go():
        worker, created = _worker_with(dialer)
        await worker.start()
        t = created[0]
        t.images.update(images_by_key)
        await t.inject(msg)
        return dialer, t

    return asyncio.run(go())


def test_image_only_dm_uses_chinese_fallback_prompt():
    dialer, t = _run_worker(_img_msg("A", "", ["k1"]), {"k1": (_PNG, "image/png")})
    call = dialer.calls[0]
    assert call["prompt"] == _IMAGE_FALLBACK_PROMPT
    assert t.fetches == [("om_img", "k1")]
    (img,) = call["images"]
    assert img["media_type"] == "image/png"
    assert img["filename"] == "feishu-image-1.png"
    assert base64.b64decode(img["data"]) == _PNG


def test_post_dm_keeps_text_as_prompt_with_images():
    dialer, _ = _run_worker(
        _img_msg("A", "分析这两张图", ["k1", "k2"]),
        {"k1": (_PNG, "image/png"), "k2": (_JPEG, "image/jpeg")})
    call = dialer.calls[0]
    assert call["prompt"] == "分析这两张图"
    assert [i["media_type"] for i in call["images"]] == ["image/png", "image/jpeg"]


def test_oversize_image_skipped_with_note():
    big = b"\xff\xd8\xff" + b"\x00" * _MAX_IMAGE_BYTES  # jpeg magic, > cap
    dialer, _ = _run_worker(
        _img_msg("A", "看看", ["k-big", "k-ok"]),
        {"k-big": (big, "image/jpeg"), "k-ok": (_PNG, "image/png")})
    call = dialer.calls[0]
    assert [i["media_type"] for i in call["images"]] == ["image/png"]
    assert "看看" in call["prompt"] and "5MB" in call["prompt"]


def test_more_than_five_images_capped():
    keys = [f"k{i}" for i in range(7)]
    dialer, t = _run_worker(
        _img_msg("A", "", keys), {k: (_PNG, "image/png") for k in keys})
    call = dialer.calls[0]
    assert len(call["images"]) == 5
    assert len(t.fetches) == 5          # over-cap keys are never even fetched
    assert "5 张上限" in call["prompt"]


def test_fetch_failure_noted_but_run_proceeds():
    dialer, _ = _run_worker(_img_msg("A", "", ["k-gone"]), {})  # fake has no bytes → None
    call = dialer.calls[0]
    assert call["images"] is None
    assert "下载失败" in call["prompt"]  # prompt non-empty → dial still valid


def test_text_only_dm_sends_no_images():
    dialer, t = _run_worker(_img_msg("A", "普通文本", []), {})
    call = dialer.calls[0]
    assert call["prompt"] == "普通文本"
    assert call["images"] is None
    assert t.fetches == []
