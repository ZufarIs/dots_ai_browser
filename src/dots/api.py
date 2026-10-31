"""The API: how another program asks this browser to do a thing.

The interface is a page for a person. This is the other door: a POST with a
task in it, and an answer at the end - for an application that needs data out
of a site the way a person gets it, by opening it and reading it.

⛔ THE BROWSER IS THE SAME ONE. Nothing here opens a page, types a login or
reads a table: the model does all of that through the same tools, in the same
engine, under the same profile. An application asking this API and a person
watching the same server's browser are the same hands, and the fingerprint the
site sees is the one the profile has always been.

⛔ AND THE LOOP IS NOT REWRITTEN HERE. The answer comes out of the package's
own `ChatService` - the same object the page talks to - and the run is driven
through the same `start`, the same `busy`, the same `stop`. A second agent loop
would be a second browser, a second fingerprint and a second set of answers
that do not agree with the first.

⛔ AND THE INTERFACE'S DOORS ARE CLOSED WHEN THE API'S ARE OPENED. The
package's own routes can send an instruction, read the browser's picture and
change a profile's pins, and none of them ask for a key. A server on 0.0.0.0
that serves them is a browser anybody on the network can drive - with the
profile's cookies and the logins saved for it. So `dots serve` replaces those
routes with these ones rather than serving both, and the key in front of these
is the only key there is.

⛔ AND THE KEY IS REQUIRED THE MOMENT THE SERVER IS NOT LOCAL. On loopback
there is nobody else to answer, and the interface itself is served with no key
either; on any other address the key is not optional, and `dots serve` refuses
to start without one rather than serving a browser to the network.

The doors, all under `/api/v1`:

    GET  /health              the server is alive; the one open door
    GET  /status              the model, the profile, the device, the waiting
    POST /run                 a task, and the answer to it
    POST /tasks               a task, and an id to come back for
    GET  /tasks/{id}          what became of that task
    GET  /tasks/{id}/events   the same, as it happens
    GET  /sessions            the conversations this server holds
    POST /sessions            a new one, under a name of the caller's
    POST /reset               forget the transcript, keep the browser
    GET  /handover            the question the agent is waiting on
    POST /handover            the answer to it, and the run carries on
    GET  /browser             where the browsers are
    GET  /frame               a picture of the browser window
    POST /stop                stop the run in a conversation
"""
from __future__ import annotations

import asyncio
import hmac
import json
import re
import time
from typing import Any, Optional

from . import handover, profiles

#: Where the API lives. One prefix, so a door added later cannot land outside
#: it by accident, and so a caller can find every one of them by name.
PREFIX = "/api/v1"

#: The variable the key is read from when it is not on the command line.
#: The same name the `.env` of a project would carry it under.
KEY_VARIABLE = "DOTS_API_KEY"

#: How long a caller may wait for an answer before it is told to come back.
DEFAULT_WAIT = 300
MAX_WAIT = 3600

#: The paths, named once.
HEALTH = PREFIX + "/health"
STATUS = PREFIX + "/status"
RUN = PREFIX + "/run"
TASKS = PREFIX + "/tasks"
TASK = PREFIX + "/tasks/{run_id}"
TASK_EVENTS = PREFIX + "/tasks/{run_id}/events"
SESSIONS = PREFIX + "/sessions"
RESET = PREFIX + "/reset"
HANDOVER = PREFIX + "/handover"
BROWSER = PREFIX + "/browser"
FRAME = PREFIX + "/frame"
STOP = PREFIX + "/stop"

#: The hosts that are this machine and nobody else. A server on one of them
#: is not reachable from the network, which is why the key is optional there.
LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1", "[::1]")

#: How many finished runs are remembered. A server that runs for months would
#: otherwise hold every answer it ever gave, and the answers are the point -
#: but not forever.
KEPT_RUNS = 200

#: How long the API waits for another conversation to let go of the browser
#: before it stops asking. A server that has stopped answering must not be
#: able to hold up the conversation that is asking for the browser.
CLOSE_WAIT = 15

#: What the task says when the caller wants the answer as JSON rather than
#: as prose. Appended to the task, never replacing it: the task is what the
#: agent does, and this is only how it is asked to report.
JSON_ASK = (
    "\n\nAnswer with one JSON object and nothing else: no prose, no code "
    "fences, no explanation. Every value is a string, a number, a boolean, "
    "null, an array or an object. If a value is not on the page, use null "
    "rather than guessing."
)

#: Every run this server has, by id. A run is kept after it ends, because an
#: application that timed out has to be able to come back for the answer.
_RUNS: dict[str, "Run"] = {}

#: One lock per conversation, so two calls cannot drive the same browser at
#: the same moment. The package's rule - one browser, one pair of hands - is
#: the reason; this is where it is enforced for callers who are not people.
#:
#: ⛔ THE LOOP IS PART OF THE KEY, because a lock belongs to the loop it was
#: made in. A server runs one loop for its life, so the extra word costs
#: nothing; a test that runs two servers one after the other would otherwise
#: find the second one holding a lock the first loop made, and an
#: `asyncio.Lock` that has seen another loop is an error rather than a lock.
_LOCKS: dict[tuple[Any, str], asyncio.Lock] = {}


# ---------------------------------------------------------------------------
# The small things
# ---------------------------------------------------------------------------

def is_local(host: str) -> bool:
    """Whether this address is this machine and nobody else.

    ⛔ LOOPBACK ONLY, and not "close enough". `0.0.0.0` is every interface
    and `::` is every interface too, and an empty bind address is every
    interface as well - it is what a host of "" means to a server. All of
    them are the network, whatever they look like.
    """
    clean = (host or "").strip().lower()
    if clean in LOCAL_HOSTS:
        return True
    return clean.startswith("127.")


def host_of(args: list[str], default: str = "127.0.0.1") -> str:
    """The address the server was told to listen on.

    Read out of the argv the same way everything else in this project reads a
    flag: both spellings, and the last one written wins. The default is the
    package's own - loopback - so a server that was not told otherwise is a
    server nobody else can reach.
    """
    found = None
    rest = list(args)
    while rest:
        item = rest.pop(0)
        if item == "--host":
            if rest:
                found = rest.pop(0)
            continue
        if item.startswith("--host="):
            found = item.split("=", 1)[1]
    if found is None:
        return default
    return found.strip()


def port_of(args: list[str], default: int = 8765) -> int:
    """The port the server was told to listen on."""
    found = ""
    rest = list(args)
    while rest:
        item = rest.pop(0)
        if item == "--port":
            if rest:
                found = rest.pop(0)
            continue
        if item.startswith("--port="):
            found = item.split("=", 1)[1]
    try:
        number = int(found)
    except (TypeError, ValueError):
        return default
    return number if 1 <= number <= 65535 else default


def key_from_env(env: Optional[dict] = None) -> str:
    """The key the environment carries, if it carries one."""
    import os

    where = os.environ if env is None else env
    return (where.get(KEY_VARIABLE) or "").strip()


def _run_id() -> str:
    """An id no run has. The clock, like the package's own."""
    return "r%d" % int(time.time() * 1000)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _profile_name() -> str:
    """The profile this server works as, or an empty string for none."""
    return profiles.active_name()


#: The conversation the API uses when the caller names none.
#:
#: ⛔ ITS OWN, AND NOT THE INTERFACE'S DEFAULT. The interface's default
#: conversation is the one a person has been talking in all afternoon, and its
#: transcript is what every turn of that conversation resends - measured here:
#: fifty-six thousand prompt tokens for the first task an API caller asked,
#: because the words in it were somebody else's. So the API keeps a
#: conversation of its own, which starts empty and is the server's to forget
#: with `POST /api/v1/reset`.
API_SESSION = "api"


def _scoped(name: str) -> str:
    """A caller's session name, inside the profile's own namespace.

    ⛔ INSIDE THE PROFILE, because a conversation is a browser and a browser
    belongs to somebody. Two profiles running at once share nothing - not the
    browser, not the cookies, not the transcript - and a name a caller invents
    would be the one thing that breaks that: `dots serve --profile work` and
    `dots serve --profile play` would answer the same `scrape` with the same
    conversation and the same identity.
    """
    clean = re.sub(r"[^A-Za-z0-9_-]+", "-", (name or "").strip()).strip("-")
    clean = clean[:80] or "api"
    here = _profile_name()
    return "%s-%s" % (here, clean) if here else clean


async def _conversation(sessions, wanted: str):
    """The conversation this request is about, and the id it has.

    ⛔ THE DEFAULT CONVERSATION IS ASKED FOR BY NAME, NOT NAMED HERE. The
    package's `DEFAULT_CHAT_ID` is the same string in three modules, and the
    profile scope rewrites all three - so naming it in a fourth place would be
    a fourth copy of a rule that already has three, and the copy would be the
    one that is wrong when a profile is in use.

    A name of the caller's own is scoped to the profile first, and a
    conversation nobody has used yet is made on the way: the browser server it
    needs is a process, and it is started when it is first asked for.
    """
    at = _scoped(wanted if (wanted or "").strip() else API_SESSION)
    service = await sessions.get(at)
    return service, getattr(service, "session_id", "") or at


def _lock_for(session_id: str) -> asyncio.Lock:
    """The lock that keeps one browser to one run at a time."""
    key = (asyncio.get_running_loop(), _lock_key(session_id))
    lock = _LOCKS.get(key)
    if lock is None:
        lock = asyncio.Lock()
        _LOCKS[key] = lock
    return lock


def _release(session_id: str) -> None:
    """Give the browser back, whether the run ended well or not."""
    key = (asyncio.get_running_loop(), _lock_key(session_id))
    lock = _LOCKS.get(key)
    if lock is not None and lock.locked():
        lock.release()


def _lock_key(session_id: str) -> str:
    """What the lock is about: the profile, or the conversation.

    ⛔ ONE RUN AT A TIME FOR A PROFILE, and not merely one per conversation.
    One profile is one browser directory, and Firefox will not open the same
    directory twice - so two conversations of one profile cannot have a
    browser each, whichever of them asked first. A server with no profile
    gives every conversation an ephemeral browser of its own, and there is
    nothing there to serialise.
    """
    name = _profile_name()
    return "profile:%s" % name if name else "conversation:%s" % session_id


async def _free_the_browser(sessions, keep: str) -> None:
    """Close the browser of any other conversation that is holding one.

    ⛔ ONE BROWSER AT A TIME, BECAUSE ONE PROFILE IS ONE BROWSER DIRECTORY.
    The engine opens Firefox with the profile's own directory, and Firefox
    will not open the same directory twice: a second conversation's
    `browser_open` simply does not answer, and the run that asked for it waits
    for a browser that is never coming. Measured, with the API serving two
    conversations: the second one's task sat there until the caller's own
    timeout fired, with nothing in the log to say why.

    So the browser of the conversation that is not in use is closed before the
    new one opens it. The cookies are in the profile's directory and stay
    either way - what is lost is the page that was open, which belongs to the
    conversation and not to the caller.

    ⛔ AND A CONVERSATION THAT IS BUSY IS LEFT ALONE. A browser is never
    taken from under a run that is using it.

    ⛔ AND THE CLOSE IS GIVEN A DEADLINE. A conversation whose own server has
    stopped answering must not be able to hold up the conversation that is
    asking for the browser - so the wait is bounded, and a server that does
    not answer in time is simply left alone.
    """
    for row in sessions.listing():
        at = str(row.get("id") or "")
        if not at or at == keep or not row.get("live"):
            continue
        try:
            service = await sessions.get(at)
        except Exception:
            continue
        if getattr(service, "busy", False):
            continue
        for role in ("main", "support"):
            try:
                await asyncio.wait_for(
                    service.link.call("browser_close", {"browser": role}),
                    timeout=CLOSE_WAIT)
            except Exception:
                pass


def _json_of(text: str) -> tuple[Optional[Any], str]:
    """The answer as JSON, and what is wrong with it when it is not.

    ⛔ THE ANSWER IS NOT TRUSTED TO BE JSON, and a failure here is reported
    rather than smoothed over. An application asked for data and given prose
    has been given nothing it can use, and the honest answer to that is "the
    answer was not JSON" next to the text that was - not an empty object,
    which reads as "there was nothing there" and is a different fact.
    """
    raw = (text or "").strip()
    if not raw:
        return None, "the answer was empty"
    if raw.startswith("```"):
        raw = raw.strip("`")
        raw = re.sub(r"^[A-Za-z]+[ \t]*\n", "", raw, count=1).strip()
    start = raw.find("{")
    end = raw.rfind("}")
    if start < 0 or end <= start:
        return None, "the answer held no JSON object"
    try:
        return json.loads(raw[start:end + 1]), ""
    except ValueError as exc:
        return None, "the answer was not JSON: %s" % exc


# ---------------------------------------------------------------------------
# One run
# ---------------------------------------------------------------------------

class Run:
    """One instruction, in flight or finished, and everything it said.

    A run is kept after it ends. An application that waited and timed out has
    to be able to come back for the answer, and the answer is the whole point
    of the exercise.
    """

    def __init__(self, run_id: str, session_id: str, task: str) -> None:
        self.id = run_id
        self.session_id = session_id
        self.task = task
        self.state = "running"
        self.answer = ""
        self.data: Optional[Any] = None
        self.error = ""
        self.events: list[dict] = []
        self.handover: Optional[dict] = None
        self.usage: dict = {}
        self.started = _now()
        self.finished = ""
        self.as_json = False
        self.done = asyncio.Event()
        self.service = None

    def as_dict(self) -> dict:
        """The run, as it is handed to a caller."""
        return {
            "ok": self.state != "failed",
            "id": self.id,
            "session": self.session_id,
            "state": self.state,
            "task": self.task,
            "answer": self.answer,
            "data": self.data,
            "error": self.error,
            "handover": self.handover,
            "events": self.events,
            "usage": self.usage,
            "started": self.started,
            "finished": self.finished,
        }


def _remember(run: Run) -> None:
    """Keep the run, and let the oldest ones go when there are too many."""
    _RUNS[run.id] = run
    while len(_RUNS) > KEPT_RUNS:
        oldest = min(_RUNS.values(), key=lambda r: r.started)
        _RUNS.pop(oldest.id, None)


def _waiting_for(run: Run) -> Optional[dict]:
    """The question the agent is waiting on, if this run is the one that asked.

    ⛔ ONLY IF THIS RUN ASKED IT. The record outlives the run that wrote it -
    it is on disk until somebody answers it - so a run that did not ask would
    otherwise inherit the question of an earlier one and report that it was
    waiting when it was not.
    """
    name = _profile_name()
    if not name:
        return None
    record = handover.current(name)
    if not record:
        return None
    if str(record.get("asked") or "") < run.started:
        return None
    return record


async def _drive(run: Run, service, text: str) -> None:
    """One instruction to its end, keeping everything it said.

    ⛔ THE EVENTS ARE THE TRANSCRIPT, and the answer is the last thing the
    agent said. The loop emits what it does - a tool call, its result, an
    error - and then says the answer, and a caller that wants to know what
    actually happened gets all of it and not only the last line.

    ⛔ AND THE END OF THE RUN IS THE `busy` THAT FOLLOWS IT, not a guess. The
    conversation says it is busy before the work and not busy after it, and
    that pair is the only place the package marks the end of a turn.
    """
    queue = service.subscribe()
    try:
        service.start(text)
        started = False
        while True:
            event = await queue.get()
            kind = str(event.get("kind") or "")
            said = str(event.get("text") or "")
            run.events.append({"kind": kind, "text": said})
            if kind == "busy":
                if said == "1":
                    started = True
                elif started:
                    break
            elif kind == "said" and said.strip():
                run.answer = said
            elif kind == "err":
                run.error = said
            elif kind == "note":
                run.state = "stopped"
    except asyncio.CancelledError:
        run.error = run.error or "the run was cancelled"
        run.state = "failed"
        raise
    except Exception as exc:
        run.error = "%s: %s" % (type(exc).__name__, exc)
        run.state = "failed"
    finally:
        service.unsubscribe(queue)
        run.finished = _now()
        try:
            run.usage = dict(service.usage)
        except Exception:
            run.usage = {}
        if run.state == "running":
            run.state = "done"
        waiting = _waiting_for(run)
        if waiting:
            run.handover = waiting
            if run.state == "done":
                run.state = "waiting"
        if run.as_json:
            run.data, problem = _json_of(run.answer)
            if problem and not run.error:
                run.error = problem
        _release(run.session_id)
        run.done.set()


# ---------------------------------------------------------------------------
# The doors
# ---------------------------------------------------------------------------

async def _body(request) -> dict:
    """The body of a request, as a dict, whatever it turned out to be."""
    try:
        body = await request.json()
    except Exception:
        return {}
    return body if isinstance(body, dict) else {}


async def _wait_of(body: dict) -> float:
    """How long the caller is willing to wait, in seconds."""
    try:
        number = int(body.get("wait") or DEFAULT_WAIT)
    except (TypeError, ValueError):
        number = DEFAULT_WAIT
    return max(1, min(number, MAX_WAIT))


async def health(request):
    """The server is alive. The one door that asks for nothing.

    Open on purpose: a load balancer in front of this has to be able to ask
    whether anything is behind it, and the answer carries nothing but the
    fact that something is.
    """
    from starlette.responses import JSONResponse

    return JSONResponse({"ok": True, "service": "dots"})


async def status(request):
    """What this server is: the model, the profile, the device, the waiting."""
    from starlette.responses import JSONResponse

    sessions = request.app.state.sessions
    name = _profile_name()
    profile = profiles.find(name) if name else None
    waiting = handover.current(name) if name else None
    return JSONResponse({
        "ok": True,
        "model": getattr(sessions, "model_label", ""),
        "profile": name,
        "device": profile.device_line() if profile is not None else "",
        "conversations": len(sessions.listing()),
        "running": len([r for r in _RUNS.values() if r.state == "running"]),
        "handover": waiting,
    })


async def run_task(request):
    """A task, and the answer to it.

    The body is `{"task": ..., "session": ..., "json": ..., "wait": ...}`.
    The answer comes back with the run, its events and whatever it is waiting
    on. A task that is still going when the wait is over is answered with the
    id to come back for, not with an error.
    """
    from starlette.responses import JSONResponse

    body = await _body(request)
    task = str(body.get("task") or body.get("instruction") or "").strip()
    if not task:
        return JSONResponse({"ok": False, "error": "no task was given"},
                            status_code=400)

    sessions = request.app.state.sessions
    service, at = await _conversation(sessions, str(body.get("session") or ""))

    lock = _lock_for(at)
    if lock.locked() or service.busy:
        return JSONResponse({"ok": False, "error": "another task is already "
                             "running on this browser", "session": at},
                            status_code=409)
    await lock.acquire()

    await _free_the_browser(sessions, at)

    run = Run(_run_id(), at, task)
    run.as_json = bool(body.get("json"))
    run.service = service
    _remember(run)
    asyncio.create_task(_drive(run, service, _text_of(run)))

    wait = await _wait_of(body)
    try:
        await asyncio.wait_for(run.done.wait(), timeout=wait)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "id": run.id, "session": at,
                             "state": "running",
                             "error": "the run is still going; ask for it "
                                      "again by its id"},
                            status_code=202)
    return JSONResponse(run.as_dict())


async def start_task(request):
    """A task, and an id to come back for.

    The same body as `run`, and the same run - but the answer is not waited
    for here. An application that has other work to do while the browser works
    gets its id and comes back.
    """
    from starlette.responses import JSONResponse

    body = await _body(request)
    task = str(body.get("task") or body.get("instruction") or "").strip()
    if not task:
        return JSONResponse({"ok": False, "error": "no task was given"},
                            status_code=400)

    sessions = request.app.state.sessions
    service, at = await _conversation(sessions, str(body.get("session") or ""))

    lock = _lock_for(at)
    if lock.locked() or service.busy:
        return JSONResponse({"ok": False, "error": "another task is already "
                             "running on this browser", "session": at},
                            status_code=409)
    await lock.acquire()

    await _free_the_browser(sessions, at)

    run = Run(_run_id(), at, task)
    run.as_json = bool(body.get("json"))
    run.service = service
    _remember(run)
    asyncio.create_task(_drive(run, service, _text_of(run)))
    return JSONResponse({"ok": True, "id": run.id, "session": at,
                         "state": "running"}, status_code=202)


async def task_state(request):
    """What became of a task."""
    from starlette.responses import JSONResponse

    run = _RUNS.get(request.path_params.get("run_id") or "")
    if run is None:
        return JSONResponse({"ok": False, "error": "no such run"},
                            status_code=404)
    return JSONResponse(run.as_dict())


async def task_events(request):
    """The same, as it happens.

    A stream of the events the run produced, the ones already behind it first
    and the ones still to come after them. The connection ends when the run
    does.
    """
    from starlette.responses import StreamingResponse

    from invisible_playwright_mcp.routes import sse

    run = _RUNS.get(request.path_params.get("run_id") or "")
    if run is None:
        from starlette.responses import JSONResponse

        return JSONResponse({"ok": False, "error": "no such run"},
                            status_code=404)

    async def stream():
        sent = 0
        while True:
            if sent < len(run.events):
                event = run.events[sent]
                sent += 1
                yield sse(event)
                continue
            if run.done.is_set():
                break
            await asyncio.sleep(0.05)
        yield sse({"kind": "end", "text": run.state})

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store"})


async def sessions_list(request):
    """The conversations this server holds."""
    from starlette.responses import JSONResponse

    sessions = request.app.state.sessions
    return JSONResponse({"ok": True, "sessions": sessions.listing()})


async def sessions_open(request):
    """A new conversation, under a name of the caller's.

    The name is the caller's own; the conversation behind it is a browser with
    its own cookies, its own fingerprint and its own transcript, and it stays
    open between calls until the server stops.
    """
    from starlette.responses import JSONResponse

    body = await _body(request)
    wanted = str(body.get("session") or body.get("name") or "").strip()
    if not wanted:
        return JSONResponse({"ok": False, "error": "no session name was given"},
                            status_code=400)
    sessions = request.app.state.sessions
    at = _scoped(wanted)
    service = await sessions.get(at)
    return JSONResponse({"ok": True, "id": at, "name": service.name})


async def sessions_reset(request):
    """Forget the transcript of a conversation, and keep its browser.

    ⛔ THE BROWSER STAYS, and that is the whole point. Every turn sends the
    whole transcript again, so a server that has been answering for a week
    pays for a week of transcript on every task - and dropping it costs
    nothing that matters: the cookies, the logins and the page the agent was
    on are the browser's, not the transcript's.

    Refused while a run is in flight, by the package's own rule: a transcript
    that something is still writing into is not one to throw away.
    """
    from starlette.responses import JSONResponse

    body = await _body(request)
    sessions = request.app.state.sessions
    service, at = await _conversation(sessions, str(body.get("session") or ""))
    return JSONResponse({"ok": True, "session": at,
                         "fresh": bool(service.reset())})


async def handover_state(request):
    """The question the agent is waiting on, if it is waiting on one."""
    from starlette.responses import JSONResponse

    name = _profile_name()
    record = handover.current(name) if name else None
    return JSONResponse({"ok": True, "profile": name, "waiting": record})


async def handover_answer(request):
    """The answer to that question, and the run carries on from where it stopped.

    ⛔ THE ANSWER GOES BACK THE SAME WAY THE QUESTION CAME. The agent stopped
    with the page, the browser and the session exactly as it left them, and
    the answer is the next thing said in that conversation - not a new one,
    and not a summary of the old one. The record of the question is forgotten
    as it is answered, so the next run does not report a question that has
    already been answered.
    """
    from starlette.responses import JSONResponse

    body = await _body(request)
    answer = str(body.get("answer") or "").strip()
    if not answer:
        return JSONResponse({"ok": False, "error": "no answer was given"},
                            status_code=400)

    sessions = request.app.state.sessions
    wanted = str(body.get("session") or "").strip()
    if wanted:
        service, at = await _conversation(sessions, wanted)
    else:
        at = _last_waiting()
        if not at:
            return JSONResponse({"ok": False, "error": "nothing is waiting for "
                                 "an answer"}, status_code=404)
        service = await sessions.get(at)

    lock = _lock_for(at)
    if lock.locked() or service.busy:
        return JSONResponse({"ok": False, "error": "another task is already "
                             "running on this browser", "session": at},
                            status_code=409)
    await lock.acquire()

    await _free_the_browser(sessions, at)

    name = _profile_name()
    if name:
        handover.clear(name)

    run = Run(_run_id(), at, answer)
    run.service = service
    _remember(run)
    asyncio.create_task(_drive(run, service, answer))

    wait = await _wait_of(body)
    try:
        await asyncio.wait_for(run.done.wait(), timeout=wait)
    except asyncio.TimeoutError:
        return JSONResponse({"ok": False, "id": run.id, "session": at,
                             "state": "running"}, status_code=202)
    return JSONResponse(run.as_dict())


def _last_waiting() -> str:
    """The conversation that asked the question nobody has answered yet."""
    waiting = [r for r in _RUNS.values() if r.handover]
    if not waiting:
        return ""
    return max(waiting, key=lambda r: r.started).session_id


async def browser_state(request):
    """Where the browsers are, and what each of them is showing."""
    from starlette.responses import JSONResponse

    sessions = request.app.state.sessions
    service, at = await _conversation(
        sessions, str(request.query_params.get("session") or ""))
    try:
        said = await service.link.call_text("browser_list")
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                            status_code=503)
    try:
        got = json.loads(said)
    except ValueError:
        got = None
    if not isinstance(got, dict):
        return JSONResponse({"ok": False,
                             "error": (said or "")[:200] or
                             "the workspace could not be read"},
                            status_code=503)
    return JSONResponse({"ok": True, "session": at,
                         "browsers": got.get("browsers") or [],
                         "focus": got.get("focus") or ""})


async def browser_frame(request):
    """A picture of the browser window, as the interface's pane shows it.

    The window and not the page: the engine draws the pointer outside the
    page on purpose, so no screenshot of the page can show where the agent is
    pointing.
    """
    from starlette.responses import JSONResponse, Response

    from invisible_playwright_mcp.link import image_of

    sessions = request.app.state.sessions
    service, at = await _conversation(
        sessions, str(request.query_params.get("session") or ""))
    try:
        result = await service.link.call("browser_watch", {})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                            status_code=503)
    got = image_of(result)
    if got is None:
        # Nothing to look at is not a failure: a browser that is not open is
        # an idle picture, and the interface's pane draws it as an empty one.
        return Response(status_code=204)
    jpeg, mime = got
    return Response(jpeg, media_type=mime, headers={"Cache-Control": "no-store"})


async def stop_run(request):
    """Stop the run in a conversation."""
    from starlette.responses import JSONResponse

    body = await _body(request)
    sessions = request.app.state.sessions
    service, at = await _conversation(sessions, str(body.get("session") or ""))
    return JSONResponse({"ok": True, "session": at,
                         "stopped": bool(service.stop())})


def _text_of(run: Run) -> str:
    """The instruction, as the agent is given it."""
    return run.task + (JSON_ASK if run.as_json else "")


# ---------------------------------------------------------------------------
# The key
# ---------------------------------------------------------------------------

class _Gate:
    """The one key, checked before any of this server's doors open.

    ⛔ COMPARED IN CONSTANT TIME, because a key that is compared with `==`
    answers a question about itself: how much of it was right. It is a
    comparison of two secrets and it is the one place in this file where the
    difference between a fast answer and a slow one is the whole of the
    defence.

    ⛔ AND IT IS CHECKED HERE, IN FRONT OF EVERYTHING, rather than in each
    door. A door that forgot to ask is a door anybody can walk through, and
    there are fourteen of them.
    """

    def __init__(self, app, key: str = "") -> None:
        self.app = app
        self.key = key or ""

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http" or not self.key:
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if path == HEALTH:
            await self.app(scope, receive, send)
            return
        if not self._matches(scope):
            await self._refuse(send)
            return
        await self.app(scope, receive, send)

    def _matches(self, scope) -> bool:
        """Whether this request carries the key."""
        for name, value in scope.get("headers") or []:
            text = name.decode("latin-1").lower()
            if text == "x-dots-key":
                if hmac.compare_digest(value.decode("latin-1"), self.key):
                    return True
            elif text == "authorization":
                said = value.decode("latin-1")
                if said.lower().startswith("bearer "):
                    if hmac.compare_digest(said[7:].strip(), self.key):
                        return True
        # The last way in, and the only one with a reason: an `EventSource`
        # cannot carry a header, so the stream of a run's events has to be
        # able to ask for the key in the query string. It is also the way
        # that puts the key in a server's access log, which is why it is the
        # last one tried and not the first.
        query = (scope.get("query_string") or b"").decode("latin-1")
        for part in query.split("&"):
            name, _, value = part.partition("=")
            if name == "key" and hmac.compare_digest(value, self.key):
                return True
        return False

    async def _refuse(self, send) -> None:
        """No. Said plainly, and with the shape every other answer has."""
        body = json.dumps({"ok": False,
                           "error": "the API key is missing or wrong"}
                          ).encode("utf-8")
        await send({"type": "http.response.start", "status": 401,
                    "headers": [(b"content-type", b"application/json"),
                                (b"content-length", str(len(body)).encode()),
                                (b"www-authenticate", b"Bearer")]})
        await send({"type": "http.response.body", "body": body})


# ---------------------------------------------------------------------------
# Putting it in
# ---------------------------------------------------------------------------

def install(routes, *, key: str = "") -> None:
    """Put the API where the interface was.

    ⛔ THE INTERFACE'S ROUTES ARE REMOVED, NOT MERELY KEPT. They can send an
    instruction, read the picture of a browser that is logged in somewhere and
    change a profile's pins, and not one of them asks for a key. A server that
    answers them is a browser anybody who can reach the port can drive, and
    the API's key would be a lock on a door next to an open one.

    The exception handler the package's app carries is left alone: it answers
    a request for a conversation that is not there, and nothing here reaches
    it.
    """
    original = routes.build_app

    def build_app(sessions):
        app = original(sessions)
        app.router.routes = []
        app.add_middleware(_Gate, key=key)
        app.add_route(HEALTH, health, methods=["GET"])
        app.add_route(STATUS, status, methods=["GET"])
        app.add_route(RUN, run_task, methods=["POST"])
        app.add_route(TASKS, start_task, methods=["POST"])
        app.add_route(TASK, task_state, methods=["GET"])
        app.add_route(TASK_EVENTS, task_events, methods=["GET"])
        app.add_route(SESSIONS, sessions_list, methods=["GET"])
        app.add_route(SESSIONS, sessions_open, methods=["POST"])
        app.add_route(RESET, sessions_reset, methods=["POST"])
        app.add_route(HANDOVER, handover_state, methods=["GET"])
        app.add_route(HANDOVER, handover_answer, methods=["POST"])
        app.add_route(BROWSER, browser_state, methods=["GET"])
        app.add_route(FRAME, browser_frame, methods=["GET"])
        app.add_route(STOP, stop_run, methods=["POST"])
        return app

    routes.build_app = build_app
