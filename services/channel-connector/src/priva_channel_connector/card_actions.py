"""Decide what a Feishu ``card.action.trigger`` tap means and how to answer it.

``handle()`` is PURE + SYNC (it runs on the lark WS thread, which must return the card
response synchronously): it looks the PendingPrompt up by message_id, gates on the operator,
builds the response, and — when the tap resolves the prompt — returns the async
``resolve_permission`` coroutine for the caller to schedule on the loop.
lark_ws bridges: ``resp, coro = handle(account_id, parsed); schedule(coro); return P2(resp)``.

The prompt is normally EMBEDDED in the streaming process card (``prompt.state`` set): a
reveal re-renders that card with the custom input; a final answer clears the embed
(``state.pending_prompt = None``) and returns a toast, letting the worker's ticker resume
and re-render the one card. A standalone prompt (``prompt.state is None`` — the fallback when
there was no streaming card) instead swaps its own card to a terminal state.

Action shapes (confirmed live): a callback button carries ``value={"act": ...}``; a
standalone ``select_static`` fires on-change with ``option`` = the picked value; a
``form_submit`` button carries ``form_value`` (all named inputs) and no value.
"""

from __future__ import annotations

from priva_common.logging import get_app_logger

from . import cards, pending, permission_cards, resolve

logger = get_app_logger(__name__)


def _toast(text: str) -> dict:
    return {"toast": {"type": "info", "content": text}}


def _with_card(card: dict, toast: str | None = None) -> dict:
    resp: dict = {"card": {"type": "raw", "data": card}}
    if toast:
        resp["toast"] = {"type": "info", "content": toast}
    return resp


def _render_open(prompt) -> dict:
    """The card with the prompt still OPEN: embedded → re-render the streaming card (with the
    current ``reveal`` state); standalone → the prompt's own card."""
    if prompt.state is not None:
        return cards.render_card(prompt.state, final=False)
    return permission_cards.permission_card(prompt)


def _resolve_coro(prompt, decision: str, answer: str | None, deny_message: str = ""):
    updated_input = None
    if decision == "allow" and prompt.kind == "ask_user" and answer is not None:
        updated_input = {"questions": prompt.questions, "answer": answer}
    return resolve.resolve_permission(
        account_id=prompt.account_id, username=prompt.username,
        session_id=prompt.session_id, request_id=prompt.request_id,
        decision=decision, updated_input=updated_input, message=deny_message,
    )


def _finish(prompt, decision: str, answer: str | None, toast: str, *, status: str,
            deny_message: str = ""):
    """Resolve + drop the prompt. Embedded → clear the embed (ticker resumes, re-renders the
    streaming card) and return a toast; standalone → swap the card to its terminal state.

    ``deny_message`` becomes the CLI's errored tool_result content. A deny MUST carry a
    non-empty one: an empty error tool_result is invalid upstream (the Anthropic API 400s
    with "content cannot be empty if is_error is true", and lenient gateways feed the
    model a malformed empty block instead)."""
    prompt.status = status
    pending.discard(prompt)
    coro = _resolve_coro(prompt, decision, answer, deny_message)
    if prompt.state is not None:
        if getattr(prompt.state, "pending_prompt", None) is prompt:
            prompt.state.pending_prompt = None
        return _toast(toast), coro
    if decision == "deny":
        card = permission_cards.skipped_card(prompt)
    else:
        card = permission_cards.answered_card(prompt, answer or toast)
    return _with_card(card, toast), coro


def handle(account_id: str, parsed: dict):
    """Returns ``(response_dict, coro_or_None)``. response_dict → P2CardActionTriggerResponse;
    coro (if any) is the resolve POST to schedule on the event loop."""
    message_id = parsed.get("message_id") or ""
    prompt = pending.get_by_message(message_id)
    if prompt is None:
        return _toast("该请求已失效或已处理"), None

    operator = parsed.get("open_id") or ""
    if prompt.sender_open_id and operator and operator != prompt.sender_open_id:
        return _toast("仅本次对话的发起人可操作此卡片"), None
    if prompt.status != "pending":
        return _toast("已处理"), None

    tag = parsed.get("tag")
    name = parsed.get("name")
    option = parsed.get("option")
    form_value = parsed.get("form_value") or {}
    value = parsed.get("value") if isinstance(parsed.get("value"), dict) else {}
    act = value.get("act")

    # 1) Buttons carrying an explicit act: skip / permission allow-deny / model① 提交.
    if act == permission_cards.SKIP:
        return _finish(prompt, "deny", None, "已跳过", status="skipped",
                       deny_message="用户跳过了此问题")
    if act == "allow":
        return _finish(prompt, "allow", None, "已允许", status="answered")
    if act == "deny":
        return _finish(prompt, "deny", None, "已拒绝", status="skipped",
                       deny_message="用户拒绝了本次操作")
    if act == "submit":                          # model① explicit 提交 — send the held pick now
        answer = permission_cards.answer_from_option(prompt.questions, getattr(prompt, "selected", ""))
        if not answer:
            return _toast("请先选择一个选项"), None
        return _finish(prompt, "allow", answer, "已提交", status="answered")

    # 2) Model ① dropdown on-change: RECORD the pick + re-render, but DON'T submit — the answer
    #    is only sent when the user clicks 提交 (above). '其他' reveals the custom input instead.
    if tag == "select_static" and option is not None:
        if option == permission_cards.OTHER:
            prompt.reveal = True
            return _with_card(_render_open(prompt), "填写你的想法后点提交"), None
        prompt.selected, prompt.reveal = option, False
        return _with_card(_render_open(prompt), "已选,点「提交」确认"), None

    # 3) Form submit (model ② all answers, or model ① custom text via the reveal form).
    if name == "submit" or form_value:
        answer = permission_cards.answer_from_form(prompt.questions, form_value)
        if not answer:
            return _toast("请先选择或填写后再提交"), None   # keep the prompt open
        return _finish(prompt, "allow", answer, "已提交", status="answered")

    logger.info("card action: unhandled shape account={} tag={} name={}", account_id, tag, name)
    return _toast("已收到"), None
