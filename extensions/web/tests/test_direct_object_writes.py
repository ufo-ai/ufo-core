"""The bridge's write endpoints: an app frame's object create/update/delete, admitted as a
prepared-intent turn under the member's own session. These tests drive the handler with a captured
context — the admit+tail machinery is the same one `submit_intent` rides and is proven elsewhere;
what is new here is input validation, the ToolIntent the endpoint builds, and how it formats the
terminal."""

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from ufo_ext_web import surface

from ufo.schema.records import TerminalFrame
from ufo.sdk.hub import Parked, Terminal
from ufo.sdk.surfaces import Admitted

EMAIL = "member@work.com"


class _Agent:
    def __init__(self, agent_id, main):
        self.id = agent_id
        self.main = main


class _Audience:
    def __init__(self, agents, admin=False):
        self.agents = agents
        self.admin = admin


class _Request:
    def __init__(self, path_params, query_params=None, body=None, bad_json=False):
        self.path_params = path_params
        self.query_params = query_params or {}
        self._body = body
        self._bad_json = bad_json

    async def json(self):
        if self._bad_json:
            raise ValueError("not json")
        return self._body


class _Ctx:
    def __init__(self, terminal):
        self._terminal = terminal
        self.admitted_intent = None
        self.speaker = None
        self.queue_key = None

    def object_kind(self, kind):
        return object()

    async def conversation_for(self, queue_key, audience, *, agent_id):
        self.queue_key = queue_key
        return uuid4()

    async def admit(self, conversation_id, body, *, speaker_member_id, intent):
        self.admitted_intent = intent
        self.speaker = speaker_member_id
        return Admitted(turn_id=uuid4(), opened_run=True)

    def tail(self, turn_id, since=""):
        terminal = self._terminal

        class _Tail:
            async def __aenter__(self_):
                async def frames():
                    yield ("cursor", terminal)

                return frames()

            async def __aexit__(self_, *args):
                return False

        return _Tail()


@pytest.fixture
def member(monkeypatch: pytest.MonkeyPatch):
    member_id, main_id = uuid4(), uuid4()
    audience = _Audience((_Agent(main_id, main=True),))

    async def _audience_for(ctx, request):
        return member_id, EMAIL, audience

    monkeypatch.setattr(surface, "_audience_for", _audience_for)
    return member_id, main_id


async def test_object_write_admits_an_object_apply_intent(member) -> None:
    member_id, main_id = member
    ctx = _Ctx(Terminal(frame=TerminalFrame(status="done", text='{"result": "created"}')))
    request = _Request({"kind": "scheduled_task"}, body={"name": "daily", "spec": {"cron": "0 9"}})

    response = await surface.object_write(ctx, request)

    assert response.status_code == 200
    assert json.loads(bytes(response.body)) == {
        "ok": True,
        "name": "daily",
        "detail": '{"result": "created"}',
    }
    intent = ctx.admitted_intent
    assert intent.tool == "object_apply"
    manifest = json.loads(intent.input["manifest"])
    assert manifest == {"kind": "scheduled_task", "name": "daily", "spec": {"cron": "0 9"}}
    assert ctx.queue_key == f"intent/{main_id}/{EMAIL}"
    assert ctx.speaker == member_id


async def test_object_write_lands_in_the_agent_the_body_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    member_id, main_id, app_id = uuid4(), uuid4(), uuid4()
    audience = _Audience((_Agent(main_id, main=True), _Agent(app_id, main=False)))

    async def _audience_for(ctx, request):
        return member_id, EMAIL, audience

    monkeypatch.setattr(surface, "_audience_for", _audience_for)
    ctx = _Ctx(Terminal(frame=TerminalFrame(status="done", text="")))
    request = _Request(
        {"kind": "scheduled_task"},
        body={"name": "daily", "spec": {"cron": "0 9"}, "agent": str(app_id)},
    )

    response = await surface.object_write(ctx, request)

    assert response.status_code == 200
    assert ctx.queue_key == f"intent/{app_id}/{EMAIL}"


async def test_object_remove_admits_an_object_delete_intent(member) -> None:
    ctx = _Ctx(Terminal(frame=TerminalFrame(status="done", text="")))
    request = _Request({"kind": "scheduled_task", "name": "daily"})

    response = await surface.object_write(ctx, request)

    assert response.status_code == 200
    assert json.loads(bytes(response.body))["ok"] is True
    intent = ctx.admitted_intent
    assert intent.tool == "object_delete"
    assert intent.input == {"kind": "scheduled_task", "name": "daily"}


async def test_the_audit_read_is_admin_only(monkeypatch: pytest.MonkeyPatch) -> None:
    audience = _Audience((_Agent(uuid4(), main=True),), admin=False)

    async def _audience_for(ctx, request):
        return uuid4(), EMAIL, audience

    monkeypatch.setattr(surface, "_audience_for", _audience_for)
    response = await surface.object_changes(_Ctx(None), _Request({}))
    assert response.status_code == 404


async def test_the_audit_read_states_the_change_and_never_the_spec(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    audience = _Audience((_Agent(uuid4(), main=True),), admin=True)

    async def _audience_for(ctx, request):
        return uuid4(), EMAIL, audience

    monkeypatch.setattr(surface, "_audience_for", _audience_for)

    class _Change:
        kind = "scheduled_task"
        name = "daily"
        verb = "update"
        caller = "member:m1"
        agent_id = uuid4()
        spec_before = '{"prompt": "private words"}'
        spec_after = '{"prompt": "more private words"}'
        created_at = datetime(2026, 8, 22, tzinfo=UTC)

    ctx = _Ctx(None)
    ctx.recent_object_changes = _recent([_Change()])
    response = await surface.object_changes(ctx, _Request({}))

    assert response.status_code == 200
    (row,) = json.loads(bytes(response.body))["changes"]
    assert row["kind"] == "scheduled_task"
    assert row["verb"] == "update"
    assert "spec_before" not in row
    assert "spec_after" not in row
    assert "private" not in json.dumps(row)


def _recent(changes):
    async def read(limit):
        return changes

    return read


async def test_a_refusal_terminal_returns_ok_false(member) -> None:
    ctx = _Ctx(
        Terminal(frame=TerminalFrame(status="failed", error_message="editing requires an admin"))
    )
    request = _Request({"kind": "scheduled_task"}, body={"name": "daily", "spec": {}})

    response = await surface.object_write(ctx, request)

    body = json.loads(bytes(response.body))
    assert body["ok"] is False
    assert body["detail"] == "editing requires an admin"


async def test_a_parked_terminal_returns_ok_false(member) -> None:
    ctx = _Ctx(Parked(message="over the cap"))
    request = _Request({"kind": "scheduled_task"}, body={"name": "daily", "spec": {}})

    response = await surface.object_write(ctx, request)

    assert json.loads(bytes(response.body)) == {
        "ok": False,
        "name": "daily",
        "detail": "over the cap",
    }


async def test_object_write_rejects_a_body_without_a_spec(member) -> None:
    ctx = _Ctx(Terminal(frame=TerminalFrame(status="done")))
    response = await surface.object_write(
        ctx, _Request({"kind": "scheduled_task"}, body={"name": "x"})
    )
    assert response.status_code == 400
    assert ctx.admitted_intent is None


async def test_object_write_rejects_a_missing_name(member) -> None:
    ctx = _Ctx(Terminal(frame=TerminalFrame(status="done")))
    response = await surface.object_write(
        ctx, _Request({"kind": "scheduled_task"}, body={"spec": {}})
    )
    assert response.status_code == 400
    assert ctx.admitted_intent is None
