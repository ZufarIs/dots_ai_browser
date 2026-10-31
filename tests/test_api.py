"""The API: the key in front of it, and the run behind it.

These are the tests a change to `dots serve` is judged by. The API is the one
door of this product that a machine uses rather than a person, and the two
things that can go wrong with it are the same two things: a door that opens
for somebody it should not, and a run that comes back with the wrong thing
in it.
"""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

import dots.api
import dots.cli
from dots import handover, profiles

ROOT = Path(__file__).resolve().parents[1]


# --- the pieces the API is put together from ---------------------------------

class _StubService:
    """A conversation that says what it was told to say.

    The shape of the package's own `ChatService`, reduced to what the API
    touches: a listener queue, `start`, `busy`, `usage` and a link.
    """

    def __init__(self, events, on_start=None, session_id="default"):
        self._events = events
        self._on_start = on_start
        self._listeners = []
        self.busy = False
        self.usage = {"prompt": 10, "completion": 4, "calls": 1}
        self.link = None
        self.session_id = session_id
        self.gate = asyncio.Event()
        self._started = False
        self.was_reset = False

    def subscribe(self):
        q = asyncio.Queue()
        self._listeners.append(q)
        return q

    def unsubscribe(self, q):
        if q in self._listeners:
            self._listeners.remove(q)

    def start(self, text):
        async def _run():
            # The question is asked once, and only once: a conversation that
            # asked it again on the answer would never be answered at all.
            if self._on_start is not None and not self._started:
                self._started = True
                self._on_start()
            ended = False
            for kind, said in self._events:
                # A `hold` is where a run waits for the test to let it go on.
                if kind == "hold":
                    await self.gate.wait()
                    continue
                if kind == "busy" and said == "0":
                    ended = True
                for q in list(self._listeners):
                    await q.put({"kind": kind, "text": said})
            if not ended:
                # A conversation that never says it is done would leave the
                # run waiting for the timeout, and a test that waits for a
                # timeout is a test that takes minutes to fail.
                for q in list(self._listeners):
                    await q.put({"kind": "busy", "text": "0"})

        return asyncio.create_task(_run())

    def stop(self):
        return True

    def reset(self):
        self.was_reset = True
        return True


class _StubLink:
    """The connection to a browser server, as far as the API reaches it."""

    def __init__(self):
        self.calls = []

    async def call(self, name, args):
        self.calls.append((name, args))
        return None

    async def call_text(self, name):
        return '{"browsers": [], "focus": ""}'


class _StubSessions:
    """The registry of conversations, as far as the API reaches it."""

    def __init__(self, service, live=()):
        self.service = service
        self.model_label = "test-model"
        self.asked = []
        self.live = tuple(live)

    async def get(self, at=None):
        self.asked.append(at)
        if at is not None:
            self.service.session_id = at
        return self.service

    def listing(self):
        return [{"id": name, "name": name, "turns": 0, "live": True}
                for name in self.live]


async def _root(request):
    return JSONResponse({"page": True})


class _FakeRoutes:
    """The package's routes, as far as the API's `install` reaches them."""

    def __init__(self):
        self.build_app = self._build

    def _build(self, sessions):
        app = Starlette(routes=[Route("/", _root)])
        app.state.sessions = sessions
        return app


def _app(service, key="", live=()) -> Starlette:
    """The app the API installs itself into."""
    fake = _FakeRoutes()
    dots.api.install(fake, key=key)
    return fake.build_app(_StubSessions(service, live))


def _paths(app) -> list[str]:
    return [getattr(r, "path", "") for r in app.router.routes]


@pytest.fixture(autouse=True)
def _clean_runs(monkeypatch):
    """Every test starts with no runs remembered.

    The registry of runs is a module-level one - it has to be, the server is
    one process and a run has to be findable from any request - and a test
    that leaves a run in it decides what the next test sees.
    """
    monkeypatch.setattr(dots.api, "_RUNS", {})


# --- the key ------------------------------------------------------------------

@pytest.mark.unit
def test_a_request_without_the_key_is_refused():
    app = _app(_StubService([]), key="secret")
    with TestClient(app) as client:
        got = client.get("/api/v1/status")
    assert got.status_code == 401
    assert got.json()["ok"] is False


@pytest.mark.unit
def test_the_key_in_the_header_opens_the_door():
    app = _app(_StubService([]), key="secret")
    with TestClient(app) as client:
        got = client.get("/api/v1/status", headers={"X-Dots-Key": "secret"})
    assert got.status_code == 200
    assert got.json()["model"] == "test-model"


@pytest.mark.unit
def test_the_key_as_a_bearer_token_opens_the_door():
    app = _app(_StubService([]), key="secret")
    with TestClient(app) as client:
        got = client.get("/api/v1/status",
                         headers={"Authorization": "Bearer secret"})
    assert got.status_code == 200


@pytest.mark.unit
def test_the_key_in_the_query_string_opens_the_door():
    # An `EventSource` cannot carry a header, and the stream of a run's events
    # is one of the doors.
    app = _app(_StubService([]), key="secret")
    with TestClient(app) as client:
        got = client.get("/api/v1/status?key=secret")
    assert got.status_code == 200


@pytest.mark.unit
def test_the_health_door_is_open():
    # A load balancer in front of this has to be able to ask whether anything
    # is behind it.
    app = _app(_StubService([]), key="secret")
    with TestClient(app) as client:
        got = client.get("/api/v1/health")
    assert got.status_code == 200
    assert got.json()["ok"] is True


@pytest.mark.unit
def test_a_server_with_no_key_serves_everybody():
    # The loopback case: nobody else can reach it, and the key is optional
    # there. The refusal for a server that IS on the network is made before
    # the server starts - see the CLI tests below.
    app = _app(_StubService([]))
    with TestClient(app) as client:
        got = client.get("/api/v1/status")
    assert got.status_code == 200


@pytest.mark.unit
def test_only_loopback_is_this_machine():
    assert dots.api.is_local("127.0.0.1")
    assert dots.api.is_local("localhost")
    assert dots.api.is_local("::1")
    # ⛔ `0.0.0.0` is every interface, and `::` is every interface too. An
    # empty bind address is every interface as well - it is what a host of ""
    # means to a server. A server on any of them is on the network whatever
    # it looks like.
    assert not dots.api.is_local("0.0.0.0")
    assert not dots.api.is_local("::")
    assert not dots.api.is_local("")
    assert not dots.api.is_local("192.168.1.10")


@pytest.mark.unit
def test_the_address_and_the_port_are_read_out_of_the_argv():
    args = ["--host", "0.0.0.0", "--port", "8080"]
    assert dots.api.host_of(args) == "0.0.0.0"
    assert dots.api.port_of(args) == 8080
    assert dots.api.host_of(["--host=127.0.0.1"]) == "127.0.0.1"
    assert dots.api.port_of(["--port=9000"]) == 9000
    assert dots.api.port_of([]) == 8765
    # A server that was not told where to listen listens on this machine.
    assert dots.api.host_of([]) == "127.0.0.1"
    assert dots.api.is_local(dots.api.host_of([]))


# --- the doors ----------------------------------------------------------------

@pytest.mark.unit
def test_the_interface_doors_are_closed_when_the_api_opens():
    # The package's own routes can send an instruction and read the picture of
    # a browser that is logged in somewhere, and not one of them asks for a
    # key. They are taken away, not served beside the API's.
    app = _app(_StubService([]))
    paths = _paths(app)
    assert "/" not in paths
    assert "/api/v1/run" in paths
    assert "/api/v1/handover" in paths


@pytest.mark.unit
def test_a_task_comes_back_with_its_answer():
    service = _StubService([("busy", "1"),
                             ("tool", "browser_navigate https://example.com"),
                             ("said", "the price is 42"),
                             ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        got = client.post("/api/v1/run", json={"task": "what is the price"})
    body = got.json()
    assert body["ok"] is True
    assert body["state"] == "done"
    assert body["answer"] == "the price is 42"
    # And everything the run said, not only the last line.
    assert [e["kind"] for e in body["events"]] == ["busy", "tool", "said", "busy"]


@pytest.mark.unit
def test_a_json_answer_is_parsed():
    service = _StubService([("busy", "1"),
                            ("said", '{"price": 42, "currency": "EUR"}'),
                            ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        got = client.post("/api/v1/run", json={"task": "the price", "json": True})
    body = got.json()
    assert body["data"] == {"price": 42, "currency": "EUR"}


@pytest.mark.unit
def test_an_answer_that_is_not_json_says_so():
    # An application asked for data and given prose has been given nothing it
    # can use, and the honest answer to that is the sentence next to the text.
    service = _StubService([("busy", "1"), ("said", "the price is 42"),
                             ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        got = client.post("/api/v1/run", json={"task": "the price", "json": True})
    body = got.json()
    assert body["data"] is None
    assert body["error"]
    assert body["answer"] == "the price is 42"


@pytest.mark.unit
def test_a_task_with_no_text_is_refused():
    app = _app(_StubService([]))
    with TestClient(app) as client:
        got = client.post("/api/v1/run", json={})
    assert got.status_code == 400
    assert got.json()["ok"] is False


@pytest.mark.unit
def test_a_conversation_is_only_driven_by_one_run_at_a_time():
    # One browser, one pair of hands - the package's own rule, enforced here
    # for callers who are not people.
    service = _StubService([("busy", "1"), ("said", "done"),
                             ("hold", ""), ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        first = client.post("/api/v1/tasks", json={"task": "one"})
        second = client.post("/api/v1/run", json={"task": "two"})
    assert first.status_code == 202
    assert second.status_code == 409
    assert second.json()["ok"] is False


@pytest.mark.unit
def test_a_task_keeps_its_own_conversation():
    service = _StubService([("busy", "1"), ("said", "done"), ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        client.post("/api/v1/run", json={"task": "one", "session": "prices"})
    assert app.state.sessions.asked == ["prices"]


@pytest.mark.unit
def test_a_task_without_a_name_uses_the_servers_own_conversation():
    # Not the interface's default conversation: that one holds the words a
    # person said this afternoon, and every turn of it is paid for again.
    service = _StubService([("busy", "1"), ("said", "done"), ("busy", "0")])
    app = _app(service)
    with TestClient(app) as client:
        client.post("/api/v1/run", json={"task": "one"})
    assert app.state.sessions.asked == ["api"]


@pytest.mark.unit
def test_the_transcript_can_be_forgotten_and_the_browser_kept():
    # Every turn sends the whole transcript again, so a server that has been
    # answering for a week pays for a week of transcript on every task.
    service = _StubService([])
    app = _app(service)
    with TestClient(app) as client:
        got = client.post("/api/v1/reset", json={})
    assert got.json()["fresh"] is True
    assert service.was_reset is True


@pytest.mark.unit
def test_the_browser_moves_to_the_conversation_that_asked():
    # One profile is one browser directory, and Firefox will not open the
    # same directory twice - so the browser of the conversation that is not
    # in use is closed before the new one opens it. Measured on a real
    # server: without this the second conversation's browser_open never
    # answered and the task waited for a browser that was never coming.
    service = _StubService([("busy", "1"), ("said", "done"), ("busy", "0")])
    service.link = _StubLink()
    app = _app(service, live=("default",))
    with TestClient(app) as client:
        client.post("/api/v1/run", json={"task": "one", "session": "prices"})
    assert ("browser_close", {"browser": "main"}) in service.link.calls
    assert ("browser_close", {"browser": "support"}) in service.link.calls


# --- the handback -------------------------------------------------------------

@pytest.mark.unit
def test_a_run_that_asked_a_question_reports_it(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("DOTS_PROFILE", "test")
    profiles.create("test")

    def _ask():
        handover.ask("test", "the six digits from your authenticator app")

    service = _StubService([("busy", "1"),
                             ("said", "I need the code for example.com"),
                             ("busy", "0")], on_start=_ask)
    app = _app(service)
    with TestClient(app) as client:
        got = client.post("/api/v1/run", json={"task": "log in"})
    body = got.json()
    assert body["state"] == "waiting"
    assert body["handover"]["question"].startswith("the six digits")


@pytest.mark.unit
def test_the_answer_to_a_question_carries_the_run_on(tmp_path, monkeypatch):
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("DOTS_PROFILE", "test")
    profiles.create("test")

    def _ask():
        handover.ask("test", "the six digits?")

    service = _StubService([("busy", "1"), ("said", "I need the code"),
                             ("busy", "0")], on_start=_ask)
    app = _app(service)
    with TestClient(app) as client:
        first = client.post("/api/v1/run", json={"task": "log in"})
        assert first.json()["state"] == "waiting"
        got = client.post("/api/v1/handover", json={"answer": "481902"})
    body = got.json()
    assert body["ok"] is True
    # And the question has been answered, so it is not asked again.
    assert handover.current("test") is None


@pytest.mark.unit
def test_the_answer_to_a_question_that_was_not_asked_is_refused(tmp_path,
                                                                 monkeypatch):
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path))
    monkeypatch.setenv("DOTS_PROFILE", "test")
    profiles.create("test")
    app = _app(_StubService([]))
    with TestClient(app) as client:
        got = client.post("/api/v1/handover", json={"answer": "481902"})
    assert got.status_code == 404


# --- the names ----------------------------------------------------------------

@pytest.mark.unit
def test_a_callers_session_name_lives_inside_the_profile(monkeypatch):
    # Two profiles running at once share nothing, and a name the caller
    # invents would be the one thing that breaks that.
    monkeypatch.setenv("DOTS_PROFILE", "work")
    assert dots.api._scoped("prices") == "work-prices"
    monkeypatch.delenv("DOTS_PROFILE")
    assert dots.api._scoped("prices") == "prices"


@pytest.mark.unit
def test_a_callers_session_name_cannot_escape_anywhere():
    name = dots.api._scoped("../../etc/passwd")
    assert "/" not in name
    assert ".." not in name


# --- the command --------------------------------------------------------------

def _run(*args: str, cwd: Path, env: dict | None = None):
    return subprocess.run([sys.executable, "-m", "dots", *args],
                          cwd=cwd, env=env, capture_output=True, text=True,
                          timeout=120)


def _clean_env(home: Path) -> dict:
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"OPENROUTER_API_KEY", "OPENAI_API_KEY",
                                "OPENAI_BASE_URL", "OPENAI_MODEL",
                                "DOTS_PROFILE", "DOTS_API_KEY"}}
    env["INVISIBLE_MCP_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


@pytest.mark.integration
def test_the_server_variant_prints_its_own_usage(tmp_path):
    done = _run("serve", "--help", cwd=tmp_path,
                env=_clean_env(tmp_path / "home"))
    assert done.returncode == 0, done.stderr
    assert "dots serve" in done.stdout
    assert "/api/v1/run" in done.stdout


@pytest.mark.integration
def test_a_server_on_the_network_without_a_key_refuses_to_start(tmp_path):
    # ⛔ REFUSED BEFORE ANYTHING IS STARTED. A gate that says no to every
    # request still leaves a browser server running on somebody's machine;
    # refusing to start says the thing once and stops.
    done = _run("serve", "--host", "0.0.0.0", cwd=tmp_path,
                env=_clean_env(tmp_path / "home"))
    assert done.returncode != 0
    said = done.stdout + done.stderr
    assert "--api-key" in said
    assert "needs a key" in said


@pytest.mark.unit
def test_the_key_leaves_the_environment(monkeypatch):
    # The browser server this process spawns inherits the environment, and the
    # key is a password to a browser that carries somebody's cookies.
    monkeypatch.setenv("DOTS_API_KEY", "secret")
    assert dots.cli._serve_key("") == "secret"
    assert "DOTS_API_KEY" not in os.environ


@pytest.mark.unit
def test_the_flag_beats_the_environment(monkeypatch):
    monkeypatch.setenv("DOTS_API_KEY", "from-env")
    assert dots.cli._serve_key("from-flag") == "from-flag"
