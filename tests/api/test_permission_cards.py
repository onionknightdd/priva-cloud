"""Unit tests for the interactive AskUserQuestion / permission card pipeline:
permission_cards (render + answer building) and card_actions (tap → decision).

Pure logic only — card_actions.handle creates but never awaits the resolve coroutine
(the caller schedules it), so tests close it to avoid 'never awaited' warnings.
"""

import os
import sys

# priva_channel_connector isn't pip-installed (its lark_oapi dep isn't in the venv);
# add its src to the path (same shim as test_connector.py). resolve.py imports
# priva_common only inside the coroutine body, so these modules import fine here.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector import card_actions, pending, permission_cards as pc  # noqa: E402
from priva_channel_connector.pending import PendingPrompt  # noqa: E402
from priva_channel_connector.sse import StreamState  # noqa: E402
from priva_channel_connector.cards import render_card  # noqa: E402


def _mk(request_id="r1", message_id="m1", kind="ask_user", questions=None, sender=""):
    return PendingPrompt(
        request_id=request_id, session_id="s1", account_id="a1", username="u",
        chat_id="c1", kind=kind, questions=questions or [], sender_open_id=sender,
        message_id=message_id,
    )


_Q_SINGLE = [{"header": "格式", "question": "输出用什么格式?", "multiSelect": False,
              "options": [{"label": "摘要", "description": "简短概览"},
                          {"label": "详细", "description": "完整说明"}]}]
_Q_MULTI = _Q_SINGLE + [{"header": "章节", "question": "包含哪些章节?", "multiSelect": True,
                         "options": [{"label": "引言"}, {"label": "结论"}, {"label": "附录"}]}]


def _close(coro):
    if coro is not None:
        coro.close()


def _register(p):
    pending.register(p)
    return p


# --- answer building -------------------------------------------------------
def test_answer_from_option_maps_index_to_label_desc():
    assert pc.answer_from_option(_Q_SINGLE, "o0") == "- 格式 -> 摘要 · 简短概览"
    assert pc.answer_from_option(_Q_SINGLE, "o1") == "- 格式 -> 详细 · 完整说明"


def test_answer_from_option_bad_index_is_empty():
    assert pc.answer_from_option(_Q_SINGLE, "o9") == ""
    assert pc.answer_from_option(_Q_SINGLE, "__other__") == ""


def test_answer_from_form_single_select():
    assert pc.answer_from_form(_Q_SINGLE, {"q0": "o1"}) == "- 格式 -> 详细 · 完整说明"


def test_answer_from_form_multi_collects_checked():
    fv = {"q0": "o0", "q1o0": True, "q1o1": False, "q1o2": True}
    out = pc.answer_from_form(_Q_MULTI, fv)
    assert "- 格式 -> 摘要 · 简短概览" in out
    assert "- 章节 -> 引言; 附录" in out


def test_answer_from_form_custom_appended_to_first_question():
    assert pc.answer_from_form(_Q_SINGLE, {"custom": "我想要 JSON"}) == "- 格式 -> 我想要 JSON"


# --- render ----------------------------------------------------------------
def _tags(card):
    return [e.get("tag") for e in card["body"]["elements"]]


def test_model1_renders_dropdown_with_other_option():
    card = pc.permission_card(_mk(questions=_Q_SINGLE))
    assert "select_static" in _tags(card)
    sel = next(e for e in card["body"]["elements"] if e["tag"] == "select_static")
    values = [o["value"] for o in sel["options"]]
    assert values == ["o0", "o1", pc.OTHER]


def test_model2_renders_form_with_submit():
    card = pc.permission_card(_mk(questions=_Q_MULTI))
    form = next(e for e in card["body"]["elements"] if e["tag"] == "form")
    inner = [e["tag"] for e in form["elements"]]
    assert "select_static" in inner and "checker" in inner
    submit = next(e for e in form["elements"] if e["tag"] == "button")
    assert submit["action_type"] == "form_submit" and "behaviors" not in submit


def test_reveal_keeps_dropdown_and_adds_input():
    card = pc.reveal_card(_mk(questions=_Q_SINGLE))
    sel = next(e for e in card["body"]["elements"] if e["tag"] == "select_static")
    assert sel["initial_option"] == pc.OTHER          # dropdown stays, 其他 preselected (reversible)
    form = next(e for e in card["body"]["elements"] if e["tag"] == "form")
    assert any(e["tag"] == "input" for e in form["elements"])


def test_permission_kind_is_allow_deny_buttons():
    card = pc.permission_card(_mk(kind="permission", questions=[]))
    btns = [e for e in card["body"]["elements"] if e["tag"] == "button"]
    acts = [b["behaviors"][0]["value"]["act"] for b in btns]
    assert "allow" in acts and "deny" in acts


def test_permission_card_shows_bash_command():
    p = _mk(kind="permission", questions=[])
    p.tool_name, p.tool_input = "Bash", {"command": "rm ./"}
    body = " ".join(e.get("content", "") for e in pc.permission_card(p)["body"]["elements"])
    assert "Bash" in body and "执行的命令" in body and "rm ./" in body


def test_permission_card_shows_file_arg_for_other_tools():
    p = _mk(kind="permission", questions=[])
    p.tool_name, p.tool_input = "Write", {"file_path": "/tmp/x", "content": "..."}
    body = " ".join(e.get("content", "") for e in pc.permission_card(p)["body"]["elements"])
    assert "/tmp/x" in body


# --- card_actions decision -------------------------------------------------
def test_handle_select_records_pick_without_submitting():
    p = _register(_mk(request_id="ra", message_id="ma", questions=_Q_SINGLE))
    resp, coro = card_actions.handle("a1", {"message_id": "ma", "tag": "select_static", "option": "o0"})
    assert coro is None                            # NOT submitted on selection
    assert p.selected == "o0" and p.status == "pending"
    # the explicit 提交 button sends it
    resp2, coro2 = card_actions.handle("a1", {"message_id": "ma", "tag": "button",
                                              "value": {"act": "submit"}})
    _close(coro2)
    assert coro2 is not None and p.status == "answered"


def test_handle_submit_without_selection_is_toast():
    p = _register(_mk(request_id="rg", message_id="mg", questions=_Q_SINGLE))
    resp, coro = card_actions.handle("a1", {"message_id": "mg", "tag": "button",
                                            "value": {"act": "submit"}})
    assert coro is None and "toast" in resp and p.status == "pending"
    pending.discard(p)


def test_model1_has_explicit_submit_button():
    card = pc.permission_card(_mk(questions=_Q_SINGLE))
    btns = [e for e in card["body"]["elements"] if e.get("tag") == "button"]
    acts = [b["behaviors"][0]["value"]["act"] for b in btns if b.get("behaviors")]
    assert "submit" in acts


def test_handle_select_other_reveals_without_resolving():
    p = _register(_mk(request_id="rb", message_id="mb", questions=_Q_SINGLE))
    resp, coro = card_actions.handle("a1", {"message_id": "mb", "tag": "select_static", "option": pc.OTHER})
    assert coro is None                            # reveal, no resolve
    form = next(e for e in resp["card"]["data"]["body"]["elements"] if e["tag"] == "form")
    assert any(e["tag"] == "input" for e in form["elements"])
    assert p.status == "pending"                   # still open
    pending.discard(p)


def test_handle_skip_denies():
    p = _register(_mk(request_id="rc", message_id="mc", questions=_Q_SINGLE))
    resp, coro = card_actions.handle("a1", {"message_id": "mc", "tag": "button",
                                            "value": {"act": pc.SKIP}})
    _close(coro)
    assert coro is not None and p.status == "skipped"


def test_handle_form_submit_builds_answer():
    p = _register(_mk(request_id="rd", message_id="md", questions=_Q_MULTI))
    resp, coro = card_actions.handle("a1", {"message_id": "md", "tag": "button", "name": "submit",
                                            "form_value": {"q0": "o1", "q1o1": True}})
    _close(coro)
    assert coro is not None and p.status == "answered"


def test_handle_sender_gate_blocks_other_user():
    p = _register(_mk(request_id="re", message_id="me", questions=_Q_SINGLE, sender="ou_owner"))
    resp, coro = card_actions.handle("a1", {"message_id": "me", "tag": "select_static",
                                            "option": "o0", "open_id": "ou_intruder"})
    assert coro is None and "toast" in resp and p.status == "pending"
    pending.discard(p)


def test_handle_unknown_message_id_is_toast():
    resp, coro = card_actions.handle("a1", {"message_id": "does-not-exist", "tag": "button"})
    assert coro is None and "toast" in resp


def test_handle_empty_form_keeps_card_open():
    p = _register(_mk(request_id="rf", message_id="mf", questions=_Q_MULTI))
    resp, coro = card_actions.handle("a1", {"message_id": "mf", "tag": "button", "name": "submit",
                                            "form_value": {}})
    assert coro is None and "toast" in resp and p.status == "pending"
    pending.discard(p)


# --- embedded mode: prompt lives INSIDE the streaming process card ----------
def _mk_embedded(**kw):
    st = StreamState()
    st.timeline.append("处理中…")
    p = _mk(**kw)
    p.state = st
    st.pending_prompt = p
    pending.register(p)
    return st, p


def test_render_card_embeds_prompt_instead_of_thinking():
    st = StreamState()
    st.timeline.append("正在处理…")
    p = _mk(questions=_Q_SINGLE)
    p.state = st
    st.pending_prompt = p
    card = render_card(st, final=False)
    contents = " ".join(e.get("content", "") for e in card["body"]["elements"])
    assert any(e.get("tag") == "select_static" for e in card["body"]["elements"])  # embedded inline
    assert "Thinking" not in contents                                             # footer suppressed


def test_render_card_thinking_when_no_prompt():
    st = StreamState()
    st.timeline.append("hi")
    card = render_card(st, final=False)
    assert any("Thinking" in e.get("content", "") for e in card["body"]["elements"])


def test_handle_embedded_select_records_then_submit_clears():
    st, p = _mk_embedded(request_id="e1", message_id="em1", questions=_Q_SINGLE)
    resp, coro = card_actions.handle("a1", {"message_id": "em1", "tag": "select_static", "option": "o0"})
    assert coro is None and p.selected == "o0" and st.pending_prompt is p  # recorded, still open
    assert "card" in resp                                                  # re-rendered (pick shown)
    resp2, coro2 = card_actions.handle("a1", {"message_id": "em1", "tag": "button",
                                              "value": {"act": "submit"}})
    _close(coro2)
    assert coro2 is not None and st.pending_prompt is None and p.status == "answered"


def test_handle_embedded_other_reveals_input_in_streaming_card():
    st, p = _mk_embedded(request_id="e2", message_id="em2", questions=_Q_SINGLE)
    resp, coro = card_actions.handle("a1", {"message_id": "em2", "tag": "select_static", "option": pc.OTHER})
    assert coro is None and st.pending_prompt is p and p.reveal is True   # still open, reveal on
    els = resp["card"]["data"]["body"]["elements"]                        # re-rendered streaming card
    form = next((e for e in els if e.get("tag") == "form"), None)
    assert form is not None and any(x["tag"] == "input" for x in form["elements"])
    pending.discard(p)
    st.pending_prompt = None


def test_handle_embedded_custom_submit_clears_embed():
    st, p = _mk_embedded(request_id="e3", message_id="em3", questions=_Q_SINGLE)
    p.reveal = True
    resp, coro = card_actions.handle("a1", {"message_id": "em3", "tag": "button", "name": "submit",
                                            "form_value": {"custom": "我要 JSON"}})
    _close(coro)
    assert coro is not None and st.pending_prompt is None and p.status == "answered"
    assert "card" not in resp


# --- AskUserQuestion result renders in the clean answer style (in the process fold) ---
def test_askuser_tool_panel_renders_clean_answer_style():
    from priva_channel_connector.sse import ToolStep
    from priva_channel_connector.cards import _tool_panel
    st = ToolStep("t1", "AskUserQuestion", "done",
                  result_text='Your questions have been answered: "偏好风格？"="详细深入 (Detailed)". Continue.')
    body = " ".join(e.get("content", "") for e in _tool_panel(st)["elements"])
    assert "已收到你的选择" in body
    assert "偏好风格？ -> 详细深入 (Detailed)" in body
    assert "Your questions have been answered" not in body   # raw dump replaced


def test_askuser_tool_panel_falls_back_when_unparseable():
    from priva_channel_connector.sse import ToolStep
    from priva_channel_connector.cards import _tool_panel
    st = ToolStep("t2", "AskUserQuestion", "done", result_text="no pairs here at all")
    body = " ".join(e.get("content", "") for e in _tool_panel(st)["elements"])
    assert "no pairs here" in body                            # kept the raw output


def test_askuser_errored_tool_panel_keeps_raw():
    # a model schema-mistake (is_error) must still show the validation error, not the clean style
    from priva_channel_connector.sse import ToolStep
    from priva_channel_connector.cards import _tool_panel
    st = ToolStep("t3", "AskUserQuestion", "error",
                  tool_input={"options": []}, result_text="InputValidationError: questions missing")
    body = " ".join(e.get("content", "") for e in _tool_panel(st)["elements"])
    assert "已收到你的选择" not in body
