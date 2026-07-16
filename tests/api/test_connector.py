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
from priva_channel_connector.sse import RunOutcome, reduce_sse      # noqa: E402
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
    def __init__(self):
        self._by_account: dict[str, BindingRecord] = {}

    def list_bindings(self, account_id):
        b = self._by_account.get(account_id)
        return [b] if b else []

    def bind(self, account_id, session_uuid, feishu_chat_id=None):
        rec = BindingRecord(binding_id=uuid.uuid4().hex, account_id=account_id,
                            session_uuid=session_uuid, feishu_chat_id=feishu_chat_id)
        self._by_account[account_id] = rec
        return rec

    def rebind(self, account_id, session_uuid, feishu_chat_id=None):
        cur = self._by_account.get(account_id)
        bid = cur.binding_id if cur else uuid.uuid4().hex
        rec = BindingRecord(binding_id=bid, account_id=account_id,
                            session_uuid=session_uuid, feishu_chat_id=feishu_chat_id)
        self._by_account[account_id] = rec
        return rec


class FakeFeishuConfigs:
    def __init__(self, effective=None, secrets=None):
        self._effective = list(effective or [])
        self._secrets = dict(secrets or {})
        self.status_calls: list[tuple] = []

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

    async def run(self, account_id, username, *, prompt, session_id=None, model=None, do_wake=True):
        self.calls.append({"account_id": account_id, "prompt": prompt, "session_id": session_id})
        return self._outcome


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
        assert t.sent == [("oc_1", "hi there")]
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
        # error outcome (an ⚠️ reply is still sent) → terminal CrossMark, Typing removed.
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("ERROR")]
        assert t.removed == ["r1"]
        assert t.sent and t.sent[0][1].startswith("⚠️")

    asyncio.run(go())


def test_worker_reaction_crossmark_on_exception():
    async def go():
        class BoomDialer:
            async def run(self, *a, **k):
                raise RuntimeError("boom")

        worker, created = _worker_with(BoomDialer())
        await worker.start()
        t = created[0]
        # _on_message swallows the exception; the finally still settles to CrossMark.
        await t.inject(_msg("A", "hello"))
        assert t.emojis == [_worker_emoji("TYPING"), _worker_emoji("ERROR")]
        assert t.removed == ["r1"] and t.sent == []

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
    # relayed text is the ASSISTANT message, NOT result.result
    assert out.text == "Hello world" and out.is_error is False


def test_reduce_sse_stream_error():
    out = reduce_sse([("stream_error", '{"code": "Boom", "message": "kaboom", "fatal": true}')])
    assert out.is_error is True and out.error_text == "kaboom" and out.session_id is None
