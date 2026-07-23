"""channel-connector unit tests — router (slash commands), worker pipeline, reconcile
diff. All against fakes: no lark_oapi, no live pod, no cluster."""

import asyncio
import os
import sys
import uuid

import pytest

# priva_channel_connector isn't pip-installed (its lark_oapi dep isn't in the venv);
# add its src to the path. lark_oapi is imported lazily, so these modules import fine.
_REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
_CONN_SRC = os.path.join(_REPO, "services", "channel-connector", "src")
if _CONN_SRC not in sys.path:
    sys.path.insert(0, _CONN_SRC)

from priva_channel_connector.engine import ReconcileEngine          # noqa: E402
from priva_channel_connector.router import SessionRouter            # noqa: E402
from priva_channel_connector.sse import RunOutcome, StreamState, reduce_sse  # noqa: E402
from priva_channel_connector.transport import FakeTransport, InboundMessage  # noqa: E402
from priva_channel_connector.worker import AppWorker                # noqa: E402

from priva_common.dataplane import (  # noqa: E402
    BindingRecord,
    FeishuChannelConfigRecord,
    FeishuSecretRecord,
)
from priva_common.models.auth import UserRecord  # noqa: E402


# --- fakes ------------------------------------------------------------------
class FakeBindings:
    """Per-chat bindings (feat_feishu_DM.md §5.2) — keyed by (account, chat),
    mirroring the real ux_binding_account_chat unique index."""

    def __init__(self):
        self._by_key: dict[tuple[str, str], BindingRecord] = {}

    def list_bindings(self, account_id):
        return [b for (a, _), b in self._by_key.items() if a == account_id]

    def bind(self, account_id, session_uuid, feishu_chat_id=None):
        rec = BindingRecord(binding_id=uuid.uuid4().hex, account_id=account_id,
                            session_uuid=session_uuid, feishu_chat_id=feishu_chat_id)
        self._by_key[(account_id, feishu_chat_id or "")] = rec
        return rec

    def rebind(self, account_id, session_uuid, feishu_chat_id=None):
        key = (account_id, feishu_chat_id or "")
        cur = self._by_key.get(key)
        bid = cur.binding_id if cur else uuid.uuid4().hex
        rec = BindingRecord(binding_id=bid, account_id=account_id,
                            session_uuid=session_uuid, feishu_chat_id=feishu_chat_id)
        self._by_key[key] = rec
        return rec


class FakeFeishuConfigs:
    def __init__(self, effective=None, secrets=None):
        self._effective = list(effective or [])
        self._secrets = dict(secrets or {})
        self.status_calls: list[tuple] = []
        self.bind_calls: list[tuple] = []
        self.bind_result = True

    def set_effective(self, effective):
        self._effective = list(effective)

    def list_effective(self):
        return list(self._effective)

    def get(self, account_id):
        for c in self._effective:
            if c.account_id == account_id:
                return c
        return None

    def get_secret(self, account_id):
        return self._secrets.get(account_id)

    def set_status(self, account_id, *, conn_status=None, last_error_code=None,
                   last_error_message=None, last_connected_at=None):
        self.status_calls.append((account_id, conn_status, last_error_message))
        return None

    def bind_owner_with_code(self, account_id, code, union_id, open_id):
        self.bind_calls.append((account_id, code, union_id, open_id))
        return self.bind_result


class FakeAccounts:
    def get(self, account_id):
        return UserRecord(username=f"user-{account_id}", password_hash="h", account_id=account_id)


class FakeClient:
    def __init__(self, *, effective=None, secrets=None):
        self.bindings = FakeBindings()
        self.feishu_configs = FakeFeishuConfigs(effective, secrets)
        self.accounts = FakeAccounts()


class FakeDialer:
    def __init__(self, outcome):
        self._outcome = outcome
        self.calls: list[dict] = []

    async def run(self, account_id, username, *, prompt, session_id=None, model=None,
                  images=None, do_wake=True, state=None, on_permission=None):
        self.calls.append({"account_id": account_id, "prompt": prompt, "session_id": session_id,
                           "images": images})
        # dial.run folds into the worker's shared state and returns it.
        if state is None:
            state = StreamState()
        o = self._outcome
        if o.text:
            state.timeline.append(o.text)
        state.session_id = o.session_id
        state.is_error = o.is_error
        state.error_text = o.error_text
        return state


def _cfg(account_id, digest, mode="owner_only"):
    return FeishuChannelConfigRecord(account_id=account_id, app_id="cli_x", has_app_secret=True,
                                     user_enabled=True, effective_enabled=True,
                                     single_chat_access_mode=mode, desired_digest=digest)


def _secret(account_id, app_secret="s3cr3t"):
    return FeishuSecretRecord(account_id=account_id, app_id="cli_x", app_secret=app_secret, domain="feishu")


def _msg(account_id, text, chat="oc_1"):
    return InboundMessage(account_id=account_id, sender_open_id="ou_sender",
                          chat_id=chat, text=text, message_id="om_" + uuid.uuid4().hex[:6])


def _transport_factory(created):
    def factory(account_id, app_id, app_secret, domain, on_message, on_status=None):
        t = FakeTransport(account_id, app_id, app_secret, domain, on_message, on_status)
        created.append(t)
        return t
    return factory


# --- router: slash-command routing ------------------------------------------
def test_router_slash_commands_and_resume():
    client = FakeClient()
    r = SessionRouter(client)

    # /new -> detach (no run)
    assert r.decide(_msg("A", "/new")).kind == "detach"
    assert r.decide(_msg("A", "/新")).kind == "detach"

    # fresh: no binding -> run with resume None
    d = r.decide(_msg("A", "hello"))
    assert d.kind == "run" and d.prompt == "hello" and d.resume_session_id is None

    # /clear and /compact are NOT intercepted — they pass through as the run prompt
    client.bindings.bind("A", "sess-1", "oc_1")
    for cmd in ("/clear", "/compact"):
        d = r.decide(_msg("A", cmd))
        assert d.kind == "run" and d.prompt == cmd and d.resume_session_id == "sess-1", cmd

    # resume: existing binding -> that session id
    assert r.decide(_msg("A", "hi")).resume_session_id == "sess-1"


def test_router_detach_and_commit():
    client = FakeClient()
    r = SessionRouter(client)

    # detach with no binding = no-op (nothing to detach)
    r.detach("A")
    assert client.bindings.list_bindings("A") == []

    # commit_session: first ever -> bind
    r.commit_session("A", "sess-1", "oc_1")
    assert client.bindings.list_bindings("A")[0].session_uuid == "sess-1"

    # detach -> session_uuid NULL
    r.detach("A", "oc_1")
    assert client.bindings.list_bindings("A")[0].session_uuid is None

    # commit rotated id -> rebind; same id -> no visible change
    r.commit_session("A", "sess-2", "oc_1")
    assert client.bindings.list_bindings("A")[0].session_uuid == "sess-2"
    r.commit_session("A", "sess-2", "oc_1")
    assert client.bindings.list_bindings("A")[0].session_uuid == "sess-2"


# --- worker pipeline --------------------------------------------------------
def test_worker_run_captures_session_and_replies():
    async def go():
        client = FakeClient()
        dialer = FakeDialer(RunOutcome(session_id="sess-new", text="hi there"))
        created = []
        worker = AppWorker(client, dialer, SessionRouter(client), _cfg("A", "d1"),
                           _secret("A"), client.accounts.get("A"),
                           _transport_factory(created))
        await worker.start()
        t = created[0]

        await t.inject(_msg("A", "hello"))
        # streaming card: an initial running card is posted, and the terminal card
        # (patched in place) carries the assistant text — no plain-text bubble.
        assert len(t.cards) == 1 and t.cards[0][0] == "oc_1"
        assert t.patches, "final card patch expected"
        final = t.patches[-1][1]
        assert final["schema"] == "2.0" and "header" not in final          # card-json-v2, no header
        assert "hi there" in final["body"]["elements"][0]["content"]
        assert not any("Thinking" in e.get("content", "") for e in final["body"]["elements"])  # no footer
        assert t.sent == []
        # fresh run -> session captured into the binding
        assert client.bindings.list_bindings("A")[0].session_uuid == "sess-new"
        assert dialer.calls[0]["session_id"] is None and dialer.calls[0]["prompt"] == "hello"

    asyncio.run(go())


def test_worker_new_detaches_without_running():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-1", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="x", text="should not run"))
        created = []
        worker = AppWorker(client, dialer, SessionRouter(client), _cfg("A", "d1"),
                           _secret("A"), client.accounts.get("A"),
                           _transport_factory(created))
        await worker.start()
        t = created[0]

        await t.inject(_msg("A", "/new"))
        # detached, no dial, an ack was sent
        assert dialer.calls == []
        assert client.bindings.list_bindings("A")[0].session_uuid is None
        assert len(t.sent) == 1 and "New conversation" in t.sent[0][1]

        # next real message resumes fresh (session None)
        await t.inject(_msg("A", "hi again"))
        assert dialer.calls[0]["session_id"] is None

    asyncio.run(go())


def test_worker_clear_passes_through_to_sdk():
    async def go():
        client = FakeClient()
        client.bindings.bind("A", "sess-1", "oc_1")
        dialer = FakeDialer(RunOutcome(session_id="sess-1", text="cleared"))
        created = []
        worker = AppWorker(client, dialer, SessionRouter(client), _cfg("A", "d1"),
                           _secret("A"), client.accounts.get("A"),
                           _transport_factory(created))
        await worker.start()
        await created[0].inject(_msg("A", "/clear"))
        # /clear reached the SDK as the prompt on the SAME session (no detach)
        assert dialer.calls[0]["prompt"] == "/clear"
        assert dialer.calls[0]["session_id"] == "sess-1"

    asyncio.run(go())


# --- message-reaction lifecycle (Typing → CheckMark / CrossMark) -------------
def _worker_with(dialer, *, account="A"):
    client = FakeClient()
    created = []
    worker = AppWorker(client, dialer, SessionRouter(client), _cfg(account, "d1"),
                       _secret(account), client.accounts.get(account),
                       _transport_factory(created))
    return worker, created


def test_worker_reaction_typing_then_checkmark_on_success():
    async def go():
        worker, created = _worker_with(FakeDialer(RunOutcome(session_id="s", text="ok")))
        await worker.start()
        t = created[0]
        msg = _msg("A", "hello")
        await t.inject(msg)
        # Typing stamped on arrival, then swapped to CheckMark once the reply was sent.
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("DONE")]
        assert [m for m, _ in t.reactions] == [msg.message_id, msg.message_id]
        assert t.removed == ["r1"]  # the transient Typing reaction was removed

    asyncio.run(go())


def test_worker_reaction_crossmark_on_error_outcome():
    async def go():
        worker, created = _worker_with(
            FakeDialer(RunOutcome(session_id=None, is_error=True, error_text="dial_failed")))
        await worker.start()
        t = created[0]
        await t.inject(_msg("A", "hello"))
        # error outcome → terminal CrossMark, Typing removed; error text lands in the
        # final (red) card, not a plain-text bubble.
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("ERROR")]
        assert t.removed == ["r1"]
        assert t.sent == []
        body = t.patches[-1][1]["body"]["elements"][0]["content"]
        assert body.startswith("⚠️") and "dial_failed" in body

    asyncio.run(go())


def test_worker_reaction_crossmark_on_exception():
    async def go():
        class BoomDialer:
            async def run(self, *a, **k):
                raise RuntimeError("boom")

        worker, created = _worker_with(BoomDialer())
        await worker.start()
        t = created[0]
        # a crash in run() still finalizes the card (not frozen on "running") + CrossMark.
        await t.inject(_msg("A", "hello"))
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("ERROR")]
        assert t.removed == ["r1"] and t.sent == []
        # card finalized as an error (#3) — not frozen on the running placeholder
        assert "run_failed" in t.patches[-1][1]["body"]["elements"][0]["content"]

    asyncio.run(go())


def test_worker_card_matches_failed_outcome_after_output():
    """#2: a run that streamed text then hit a terminal error must render a RED card
    carrying the error — the card can't claim success while the reaction says failure."""
    async def go():
        class ErroredDialer:
            def __init__(self):
                self.calls = []

            async def run(self, *a, state=None, **k):
                # streamed some text, then a terminal error — folded into the shared state
                if state is None:
                    state = StreamState()
                state.timeline.append("partial output")
                state.is_error = True
                state.error_text = "dial_failed"
                return state

        worker, created = _worker_with(ErroredDialer())
        await worker.start()
        t = created[0]
        await t.inject(_msg("A", "hi"))
        final = t.patches[-1][1]
        assert final["schema"] == "2.0" and "header" not in final
        els = final["body"]["elements"]
        assert els[0]["content"].startswith("⚠️") and "dial_failed" in els[0]["content"]  # error headline
        assert any("partial output" in e.get("content", "") for e in els)                 # partial text kept
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("ERROR")]

    asyncio.run(go())


def test_worker_reaction_checkmark_on_detach():
    async def go():
        worker, created = _worker_with(FakeDialer(RunOutcome()))
        await worker.start()
        t = created[0]
        await t.inject(_msg("A", "/new"))
        # /new is a successful op → CheckMark (no dial happened).
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("DONE")]
        assert t.removed == ["r1"]

    asyncio.run(go())


# --- streaming reducer (StreamState.step) -----------------------------------
def test_stream_state_folds_text_tools_and_result():
    from priva_channel_connector.sse import StreamState, step

    s = StreamState()
    assert step(s, "assistant", '{"content":[{"type":"text","text":"hello "}]}') is True
    assert step(s, "tool_use",
                '{"content":[{"type":"tool_use","id":"t1","name":"Bash","input":{"command":"ls -la"}}]}') is True
    # a tool_use frame that ALSO carries text keeps the text (fixes the old drop)
    assert step(s, "tool_use",
                '{"content":[{"type":"text","text":"world"},'
                '{"type":"tool_use","id":"t2","name":"Read","input":{"file_path":"a.py"}}]}') is True
    assert step(s, "tool_result",
                '{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false}]}') is True
    assert step(s, "tool_result",
                '{"content":[{"type":"tool_result","tool_use_id":"t2","is_error":true}]}') is True
    # result is pure bookkeeping (no visible change) -> False
    assert step(s, "result",
                '{"session_id":"sx","is_error":false,"duration_ms":3200,"num_turns":2}') is False

    assert s.text == "hello \nworld"
    assert [(st.name, st.status, st.summary) for st in s.steps] == [
        ("Bash", "done", "ls -la"),
        ("Read", "error", "a.py"),
    ]
    assert s.session_id == "sx" and s.duration_ms == 3200 and s.num_turns == 2
    assert s.is_error is False


def test_stream_state_stream_error():
    from priva_channel_connector.sse import StreamState, step
    s = StreamState()
    assert step(s, "stream_error", '{"code":"Boom","message":"kaboom","fatal":true}') is True
    assert s.is_error is True and s.error_text == "kaboom"


# --- card renderer (cards.render_card) --------------------------------------
def test_render_card_streaming_inline_then_final_folds_process():
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState, ToolStep

    # --- streaming: the whole process shown EXPANDED inline, nothing folded, Thinking footer ---
    s = StreamState(timeline=["let me look", ToolStep("t1", "Bash", "running", "ls")])
    running = render_card(s, final=False)
    assert running["schema"] == "2.0" and "header" not in running          # card-json-v2, no header
    els = running["body"]["elements"]
    assert not any(e.get("tag") == "collapsible_panel" for e in els)       # nothing folds while streaming
    assert els[0]["tag"] == "markdown" and "let me look" in els[0]["content"]
    assert "⟳" in els[1]["content"] and "Bash" in els[1]["content"]        # tool step inline, expanded
    assert els[-1]["tag"] == "markdown" and "Thinking" in els[-1]["content"]  # animated footer

    # --- final: process folds ON TOP (each tool its OWN sub-fold), only the answer expanded ---
    s.steps[0].status = "done"
    s.timeline.append("all done")
    final = render_card(s, final=True)
    assert "header" not in final
    fels = final["body"]["elements"]
    assert not any("Thinking" in e.get("content", "") for e in fels)       # no footer on the final card
    outer = fels[0]
    assert outer["tag"] == "collapsible_panel" and outer["expanded"] is False   # process folded, on top
    assert "执行了 1 条 bash 命令" in outer["header"]["title"]["content"]  # aggregate summary in header
    assert outer["elements"][0]["content"] == "let me look"               # intermediate text folded in
    sub = outer["elements"][1]
    assert sub["tag"] == "collapsible_panel" and sub["expanded"] is False  # the tool is INDIVIDUALLY foldable
    assert "Bash" in sub["header"]["title"]["content"] and "green" in sub["header"]["title"]["content"]
    assert fels[1]["tag"] == "markdown" and fels[1]["content"] == "all done"    # only the answer expanded


def test_render_card_final_no_trailing_text_note():
    # a run that ends on a tool (no closing assistant text) → process folded + a plain note
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState, ToolStep
    s = StreamState(timeline=["step one", ToolStep("t1", "Read", "done", "a.py")])
    final = render_card(s, final=True)
    els = final["body"]["elements"]
    assert els[0]["tag"] == "collapsible_panel" and els[0]["expanded"] is False
    assert els[1]["content"] == "(无文本回复)"


def test_render_card_running_no_text_is_thinking_only():
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState
    c = render_card(StreamState(), final=False)
    els = c["body"]["elements"]
    # nothing streamed yet → a single markdown element carrying the "Thinking" footer
    assert c["schema"] == "2.0" and "header" not in c
    assert len(els) == 1 and els[0]["tag"] == "markdown"
    assert "Thinking" in els[0]["content"]


def test_render_card_dots_cycle():
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState
    foot = lambda d: render_card(StreamState(), final=False, dots=d)["body"]["elements"][-1]["content"]
    assert foot(1).count(".") == 1 and foot(2).count(".") == 2 and foot(3).count(".") == 3


def test_render_card_final_error():
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState
    c = render_card(StreamState(is_error=True, error_text="boom"), final=True)
    assert "header" not in c
    assert c["body"]["elements"][0]["content"].startswith("⚠️ boom")
    assert not any("Thinking" in e.get("content", "") for e in c["body"]["elements"])  # no footer


def test_render_card_error_prefix_survives_long_output():
    # #1: a long streamed text must not truncate the error prefix off the card.
    from priva_channel_connector.cards import render_card
    from priva_channel_connector.sse import StreamState
    c = render_card(StreamState(timeline=["x" * 5000], is_error=True, error_text="upstream 529"),
                    final=True)
    assert "header" not in c
    assert c["body"]["elements"][0]["content"].startswith("⚠️ upstream 529")


def test_clip_body_rebalances_odd_code_fence():
    # #5: a tail slice through a ``` block must not leave an unbalanced fence.
    from priva_channel_connector.cards import _BODY_MAX, _clip_body
    text = ("filler line\n" * 4000) + "```\nsome code that never closes\n"
    assert len(text) > _BODY_MAX          # clipping actually happens
    out = _clip_body(text)
    assert out.count("```") % 2 == 0      # fence rebalanced


# --- agent-UI tool-run summary parity (folded panel header) -----------------
def test_run_summary_groups_in_order():
    # Grouped counts, in GROUP_ORDER, joined by ", " — matching summarizeRun.
    from priva_channel_connector.cards import _run_summary
    from priva_channel_connector.sse import ToolStep
    steps = [
        ToolStep("t1", "Read", "done", "a.py"),
        ToolStep("t2", "Bash", "done", "ls"),
        ToolStep("t3", "Read", "done", "b.py"),
        ToolStep("t4", "Grep", "done", "foo"),
    ]
    # read before search before bash (GROUP_ORDER), counts aggregated per group
    assert _run_summary(steps) == "读取了 2 个文件, 搜索了 1 个模式, 执行了 1 条 bash 命令"


def test_run_summary_edit_write_deltas_and_success_gate():
    from priva_channel_connector.cards import _run_summary
    from priva_channel_connector.sse import ToolStep
    # a still-running Edit is NOT counted (isSuccessfulFileMutation gate)
    running_edit = [ToolStep("t1", "Edit", "running", "x.py",
                             {"old_string": "a", "new_string": "a\nb\nc"})]
    assert _run_summary(running_edit) == ""      # nothing groups yet → caller falls back

    # done Write + done Edit: wrote is the last-file key, deltas summed (from each step's raw
    # input, computed in the card layer) and attached to it, +added green / -removed red, then
    # the read group follows. Edit: old 1 line / new 3 lines → +3 -1; Write: 10 lines → +10.
    steps = [
        ToolStep("t1", "Edit", "done", "x.py", {"old_string": "a", "new_string": "a\nb\nc"}),
        ToolStep("t2", "Write", "done", "y.py",
                 {"content": "l1\nl2\nl3\nl4\nl5\nl6\nl7\nl8\nl9\nl10"}),
        ToolStep("t3", "Read", "done", "z.py"),
    ]
    out = _run_summary(steps)
    assert out == ("编辑了 1 个文件, 写入了 1 个文件 "
                   "<font color='green'>+13</font> <font color='red'>-1</font>, "
                   "读取了 1 个文件")


def test_run_summary_errored_edit_not_counted():
    # an errored file mutation is excluded from edited/wrote (parity with the web gate)
    from priva_channel_connector.cards import _run_summary
    from priva_channel_connector.sse import ToolStep
    steps = [ToolStep("t1", "Edit", "error", "x.py",
                      {"old_string": "a", "new_string": "b"})]
    assert _run_summary(steps) == ""


def test_run_summary_unknown_tool_is_other():
    from priva_channel_connector.cards import _run_summary
    from priva_channel_connector.sse import ToolStep
    steps = [ToolStep("t1", "TodoWrite", "done", ""), ToolStep("t2", "SomeMcpTool", "done", "")]
    assert _run_summary(steps) == "执行了 2 个其他工具"


def test_process_panel_header_uses_summary_with_fallback():
    from priva_channel_connector.cards import _process_panel
    from priva_channel_connector.sse import ToolStep
    # grouped summary as the folded header, prefixed with 过程 ·
    panel = _process_panel([ToolStep("t1", "Read", "done", "a.py")])
    assert panel["tag"] == "collapsible_panel" and panel["expanded"] is False
    assert panel["header"]["title"]["content"] == "过程 · 读取了 1 个文件"
    # nothing groups yet (lone running Edit) → plain step-count fallback, never empty
    lone = _process_panel([ToolStep("t1", "Edit", "running", "a.py")])
    assert lone["header"]["title"]["content"] == "过程 · 1 个工具步骤"
    # only intermediate text, no tools → a bare 过程 header
    text_only = _process_panel(["some thinking"])
    assert text_only["header"]["title"]["content"] == "过程"
    # nothing to fold → None
    assert _process_panel([]) is None


# --- per-tool fold, duration header, native table, truncation hints ---------
def test_fmt_duration():
    from priva_channel_connector.cards import _fmt_duration
    assert _fmt_duration(45000) == "45s"
    assert _fmt_duration(72000) == "1m 12s"
    assert _fmt_duration(600) == "0s"                 # sub-second → 0s
    assert _fmt_duration(None) == "" and _fmt_duration(-1) == ""


def test_process_panel_duration_header():
    from priva_channel_connector.cards import _process_panel
    from priva_channel_connector.sse import ToolStep
    steps = [ToolStep("t1", "Read", "done", "a.py")]
    assert _process_panel(steps, 72000)["header"]["title"]["content"] == "已运行:1m 12s, 读取了 1 个文件"
    assert _process_panel(steps, None)["header"]["title"]["content"] == "过程 · 读取了 1 个文件"  # no dur → fallback


def test_tool_panel_bash_input_and_title():
    from priva_channel_connector.cards import _tool_panel
    from priva_channel_connector.sse import ToolStep
    st = ToolStep("t1", "Bash", "done", "pytest -q", {"command": "pytest -q"})
    p = _tool_panel(st)
    assert p["tag"] == "collapsible_panel" and p["expanded"] is False       # each tool individually foldable
    title = p["header"]["title"]["content"]
    assert "Bash" in title and "green" in title and "pytest -q" in title    # glyph + name + summary
    assert any("```bash" in e.get("content", "") and "pytest -q" in e["content"] for e in p["elements"])  # full cmd


def test_tool_panel_edit_diff_and_delta_in_header():
    from priva_channel_connector.cards import _tool_panel
    from priva_channel_connector.sse import ToolStep
    st = ToolStep("t1", "Edit", "done", "dial.py",
                  {"file_path": "dial.py", "old_string": "a", "new_string": "a\nb\nc\nd"})
    p = _tool_panel(st)
    title = p["header"]["title"]["content"]
    assert "Edit" in title and "<font color='green'>+4</font>" in title and "<font color='red'>-1</font>" in title
    diff = "\n".join(e.get("content", "") for e in p["elements"])
    assert "```diff" in diff and "- a" in diff and "+ d" in diff            # old/new rendered as a diff


def test_tool_output_single_code_block_native_collapse():
    # output is ONE code block; Feishu natively collapses long code — no hand-rolled fold
    from priva_channel_connector.cards import _tool_panel
    from priva_channel_connector.sse import ToolStep
    out = "\n".join(f"line {i}" for i in range(1, 41))                       # 40 lines
    st = ToolStep("t1", "Read", "done", "big.log", {"file_path": "big.log"}, result_text=out)
    p = _tool_panel(st)
    assert not any(e.get("tag") == "collapsible_panel" for e in p["elements"])   # no nested fold in the body
    body = "\n".join(e.get("content", "") for e in p["elements"])
    assert "```" in body and "line 1" in body and "line 40" in body         # full output in one code block


def test_tool_output_truncation_hint():
    from priva_channel_connector.cards import _tool_panel, _OUTPUT_MAX_LINES, _TRUNC_HINT
    from priva_channel_connector.sse import ToolStep
    out = "\n".join(f"L{i}" for i in range(_OUTPUT_MAX_LINES + 50))
    st = ToolStep("t1", "Bash", "done", "dump", {"command": "dump"}, result_text=out)
    els = _tool_panel(st)["elements"]                                       # [input, output, hint]
    assert els[-1]["content"] == _TRUNC_HINT                                 # capped → grey size hint appended
    assert f"L{_OUTPUT_MAX_LINES - 1}" in els[-2]["content"] and f"L{_OUTPUT_MAX_LINES}" not in els[-2]["content"]


def test_answer_native_table_and_prose():
    from priva_channel_connector.cards import _answer_elements
    text = ("结果如下:\n\n"
            "| 文件 | 增删 |\n| --- | --- |\n| dial.py | +4 -1 |\n| cards.py | +40 |\n\n"
            "完成。")
    els = _answer_elements(text)
    table = next(e for e in els if e["tag"] == "table")                    # GFM table → native table element
    assert [c["display_name"] for c in table["columns"]] == ["文件", "增删"]
    assert table["rows"][0]["c0"] == "dial.py" and table["rows"][1]["c1"] == "+40"
    assert any(e["tag"] == "markdown" and "结果如下" in e["content"] for e in els)   # prose stays markdown
    assert any(e["tag"] == "markdown" and "完成。" in e["content"] for e in els)


def test_answer_without_table_is_plain_markdown():
    from priva_channel_connector.cards import _answer_elements
    els = _answer_elements("就一句话,没有表格。")
    assert len(els) == 1 and els[0]["tag"] == "markdown"


def test_result_text_captured_from_tool_result():
    # sse carries the tool_result output text forward as a fact (string form)
    from priva_channel_connector.sse import StreamState, step
    s = StreamState()
    step(s, "tool_use", '{"content":[{"type":"tool_use","id":"b1","name":"Bash","input":{"command":"echo hi"}}]}')
    step(s, "tool_result",
         '{"content":[{"type":"tool_result","tool_use_id":"b1","is_error":false,"content":"hi\\nthere"}]}')
    (bash,) = s.steps
    assert bash.status == "done" and bash.result_text == "hi\nthere"


def test_result_text_from_block_list():
    # tool_result content as a list of {type:text} blocks → joined text
    from priva_channel_connector.sse import StreamState, step
    s = StreamState()
    step(s, "tool_use", '{"content":[{"type":"tool_use","id":"r1","name":"Read","input":{"file_path":"a"}}]}')
    step(s, "tool_result",
         '{"content":[{"type":"tool_result","tool_use_id":"r1","content":[{"type":"text","text":"file body"}]}]}')
    assert s.steps[0].result_text == "file body"


def test_clip_body_truncation_hint():
    from priva_channel_connector.cards import _clip_body, _BODY_MAX, _TRUNC_HINT
    assert _TRUNC_HINT in _clip_body("x\n" * _BODY_MAX)      # tail-capped text run carries the hint


def test_fold_carries_raw_tool_input():
    # the reducer carries the raw input forward (a fact); the card layer derives deltas.
    from priva_channel_connector.sse import StreamState, step
    s = StreamState()
    step(s, "tool_use",
         '{"content":[{"type":"tool_use","id":"e1","name":"Edit",'
         '"input":{"file_path":"x.py","old_string":"a\\nb","new_string":"a\\nb\\nc\\nd"}}]}')
    (edit,) = s.steps
    assert edit.tool_input == {"file_path": "x.py", "old_string": "a\nb", "new_string": "a\nb\nc\nd"}


def test_line_delta_computed_in_card_layer():
    # cards._line_delta derives (added, removed) from the raw input, web-UI countContentLines.
    from priva_channel_connector.cards import _line_delta
    assert _line_delta("Edit", {"old_string": "a\nb", "new_string": "a\nb\nc\nd"}) == (4, 2)
    assert _line_delta("Write", {"content": "one\ntwo\nthree\n"}) == (3, 0)   # trailing \n stripped
    assert _line_delta("Read", {"file_path": "x"}) == (0, 0)
    assert _line_delta("Edit", None) == (0, 0)


# --- worker streaming path (initial card -> patches -> terminal card) --------
def test_worker_streaming_card_end_to_end():
    async def go():
        from priva_channel_connector.sse import StreamState, step

        frames = [
            ("assistant", '{"content":[{"type":"text","text":"let me look"}]}'),
            ("tool_use",
             '{"content":[{"type":"tool_use","id":"t1","name":"Read","input":{"file_path":"x.py"}}]}'),
            ("tool_result",
             '{"content":[{"type":"tool_result","tool_use_id":"t1","is_error":false}]}'),
            ("assistant", '{"content":[{"type":"text","text":"here is the summary"}]}'),
            ("result", '{"session_id":"sess-9","is_error":false,"duration_ms":1500,"num_turns":1}'),
        ]

        class FramesDialer:
            def __init__(self):
                self.calls = []

            async def run(self, account_id, username, *, prompt, session_id=None, model=None,
                          images=None, do_wake=True, state=None, on_permission=None):
                self.calls.append({"prompt": prompt})
                if state is None:
                    state = StreamState()
                for ev, ds in frames:
                    step(state, ev, ds)
                return state       # dial.run folds into the shared state and returns it

        worker, created = _worker_with(FramesDialer())
        await worker.start()
        t = created[0]
        await t.inject(_msg("A", "hi"))

        # one initial running card, at least one patch, terminal card folds the process on top
        # (intro text + Read sub-fold) with only the closing answer left expanded below.
        assert len(t.cards) == 1
        assert t.patches, "expected card patches"
        final = t.patches[-1][1]
        assert final["schema"] == "2.0" and "header" not in final
        outer = final["body"]["elements"][0]
        assert outer["tag"] == "collapsible_panel" and outer["expanded"] is False
        assert outer["header"]["title"]["content"].startswith("已运行:")     # duration header (duration_ms=1500)
        assert "读取了 1 个文件" in outer["header"]["title"]["content"]
        assert outer["elements"][0]["content"] == "let me look"             # intermediate text folded in
        sub = outer["elements"][1]
        assert sub["tag"] == "collapsible_panel" and "Read" in sub["header"]["title"]["content"]  # per-tool fold
        assert final["body"]["elements"][1]["content"] == "here is the summary"   # only the answer expanded
        assert not any("Thinking" in e.get("content", "") for e in final["body"]["elements"])  # no footer
        # reaction lifecycle still settles to CheckMark alongside the card
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("DONE")]

    asyncio.run(go())


def _worker_emoji(name):
    import priva_channel_connector.worker as w
    return {"TYPING": w._EMOJI_TYPING, "DONE": w._EMOJI_DONE, "ERROR": w._EMOJI_ERROR}[name]


# --- reconcile diff ---------------------------------------------------------
def test_reconcile_arm_teardown_and_digest_rearm():
    async def go():
        client = FakeClient(
            effective=[_cfg("A", "dA1"), _cfg("B", "dB1")],
            secrets={"A": _secret("A"), "B": _secret("B")},
        )
        dialer = FakeDialer(RunOutcome())
        created = []
        eng = ReconcileEngine(client, _transport_factory(created), dialer, poll_seconds=999)

        # initial: both arm
        await eng.reconcile_once()
        assert eng.armed_count == 2 and len(created) == 2

        # A disabled (drops from effective) -> torn down + status disabled
        client.feishu_configs.set_effective([_cfg("B", "dB1")])
        await eng.reconcile_once()
        assert eng.armed_count == 1
        assert ("A", "disabled", None) in client.feishu_configs.status_calls

        # B rotates creds (digest changes) -> re-armed (teardown+arm), new transport
        client.feishu_configs.set_effective([_cfg("B", "dB2")])
        await eng.reconcile_once()
        assert eng.armed_count == 1
        b_transports = [t for t in created if t.account_id == "B"]
        assert len(b_transports) == 2 and b_transports[0].stopped is True

    asyncio.run(go())


def test_reconcile_parks_on_undecryptable_secret():
    async def go():
        client = FakeClient(
            effective=[_cfg("A", "dA1")],
            secrets={"A": _secret("A", app_secret="")},  # "" == unset/undecryptable
        )
        eng = ReconcileEngine(client, _transport_factory([]), FakeDialer(RunOutcome()), poll_seconds=999)
        await eng.reconcile_once()
        assert eng.armed_count == 0
        assert any(c[0] == "A" and c[1] == "error" and c[2] == "secret_undecryptable"
                   for c in client.feishu_configs.status_calls)

    asyncio.run(go())


# --- sse reduction ----------------------------------------------------------
def test_reduce_sse_relays_assistant_only():
    # MVP: relay assistant text; consult `result` only for session_id/is_error, and
    # ignore its `result` field + every other event type.
    frames = [
        ("stream_init", '{"stream_id": "transport-uuid"}'),      # ignored
        ("tool_use", '{"content": [{"type": "tool_use"}]}'),     # ignored
        ("assistant", '{"content": [{"type": "text", "text": "Hello "}, {"type": "tool_use"}, {"type": "text", "text": "world"}]}'),
        ("result", '{"type": "result", "session_id": "sdk-sess-9", "is_error": false, "result": "DIFFERENT ignored text"}'),
    ]
    out = reduce_sse(frames)
    # session id from result (authoritative, for binding — not relayed to the user)
    assert out.session_id == "sdk-sess-9"
    # relayed text is the ASSISTANT message, NOT result.result; the timeline splits the
    # text around the interleaved tool_use, so the two runs join with a newline.
    assert out.text == "Hello \nworld" and out.is_error is False


def test_reduce_sse_stream_error():
    out = reduce_sse([("stream_error", '{"code": "Boom", "message": "kaboom", "fatal": true}')])
    assert out.is_error is True and out.error_text == "kaboom" and out.session_id is None
