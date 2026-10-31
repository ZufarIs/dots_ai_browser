"""What the GUI launcher writes to `.env` and how it parses what was there.

The window itself is a Tk surface and is not under test - the form is a
view over `EnvConfig`, and the form's only job is to be a faithful
mirror of it. The interesting rules live on `EnvConfig.from_env`,
`EnvConfig.to_env` and `write_env`, and they are what these tests judge.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Optional

import pytest

import dots.gui as gui

# ⛔ NO WARM-UP WHILE THE TESTS RUN. The launcher spawns a background process
# to read the heavy libraries and check the engine before anybody clicks
# Start, and in a test run that process is four of them hammering the same
# files at once - which is what made the suite slow and, on this machine,
# what made a `Tk()` in another thread never come back.
#
# The warm-up is the launcher's business, and the launcher is not what these
# tests are about.
os.environ.setdefault("DOTS_WARMUP", "0")


@pytest.mark.unit
def test_from_env_reads_what_the_file_holds(tmp_path, monkeypatch):
    # ⛔ THE PERSONAL FILE IS READ TOO, SO THE HOME IS ISOLATED. A person
    # who has a key in `%APPDATA%\invisible-playwright-mcp\.env` must not
    # change what these tests see.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / ".env").write_text(
        "OPENAI_API_KEY=sk-x\n"
        "OPENAI_BASE_URL=https://api.example.com/v1\n"
        "OPENAI_MODEL=foo\n",
        encoding="utf-8",
    )
    cfg = gui.EnvConfig.from_env(tmp_path)
    assert cfg.api_key == "sk-x"
    assert cfg.base_url == "https://api.example.com/v1"
    assert cfg.model == "foo"


@pytest.mark.unit
def test_from_env_returns_defaults_when_the_file_is_absent(tmp_path, monkeypatch):
    # The personal file is the other half of the answer, so "the file is
    # absent" has to mean both files.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))


@pytest.mark.unit
def test_from_env_returns_defaults_when_the_file_is_absent(tmp_path, monkeypatch):
    # No file at all - the form must still be usable, with the port at its
    # own default and nothing else lying around.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    cfg = gui.EnvConfig.from_env(tmp_path)
    assert cfg.api_key == ""
    assert cfg.base_url == ""
    assert cfg.model == ""
    assert cfg.port == 8765
    assert cfg.headed is False


@pytest.mark.unit
def test_from_env_treats_empty_values_as_absent(tmp_path, monkeypatch):
    # `OPENAI_API_KEY=` is not "no key" - it is "key is the empty string" -
    # and the loader must answer the same either way: a stray blank line
    # that someone left behind should not look like a configured key.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / ".env").write_text("OPENAI_API_KEY=\n", encoding="utf-8")
    cfg = gui.EnvConfig.from_env(tmp_path)
    assert cfg.api_key == ""


@pytest.mark.unit
def test_to_env_omits_empty_fields():
    cfg = gui.EnvConfig(api_key="", base_url="", model="")
    # Empty fields are omitted from the file entirely: a line like
    # `OPENAI_API_KEY=` is not "no key", it is "key is the empty string",
    # and the two are different facts.
    assert cfg.to_env() == {}


@pytest.mark.unit
def test_to_env_strips_trailing_slash_from_base_url():
    cfg = gui.EnvConfig(base_url="https://api.example.com/v1/")
    env = cfg.to_env()
    assert env["OPENAI_BASE_URL"] == "https://api.example.com/v1"


@pytest.mark.unit
def test_to_env_writes_only_non_default_browser_settings():
    # 8765 is the default the CLI honours, "1" is what `headed=True`
    # produces; both absent from the file leaves the loader with no line to
    # answer and the CLI with the default to fall back to.
    cfg = gui.EnvConfig(port=8765, headed=False)
    assert "INVISIBLE_MCP_PORT" not in cfg.to_env()
    assert "INVISIBLE_MCP_HEADED" not in cfg.to_env()

    cfg2 = gui.EnvConfig(port=9000, headed=True)
    env = cfg2.to_env()
    assert env["INVISIBLE_MCP_PORT"] == "9000"
    assert env["INVISIBLE_MCP_HEADED"] == "1"


@pytest.mark.unit
def test_write_env_writes_a_file_the_loader_can_read_back(tmp_path):
    cfg = gui.EnvConfig(api_key="sk-test", base_url="http://x",
                        model="m", port=9000, headed=True)
    gui.write_env(tmp_path, cfg)

    # Round-trip: the file the writer produces must parse back into the
    # same values, because the GUI saves the form and the CLI reads the
    # file, and the only contract between them is that this round trips.
    parsed = gui.EnvConfig.from_env(tmp_path)
    assert parsed.api_key == "sk-test"
    assert parsed.base_url == "http://x"
    assert parsed.model == "m"
    assert parsed.port == 9000
    assert parsed.headed is True


@pytest.mark.unit
def test_provider_name_recognises_known_endpoints():
    cases = {
        "": "OpenRouter",
        "https://openrouter.ai/api/v1": "OpenRouter",
        "https://api.openai.com/v1": "OpenAI",
        "https://api.deepseek.com/v1": "DeepSeek",
        "http://127.0.0.1:11434/v1": "Ollama (local)",
        "http://localhost:9999/v1": "Custom",
    }
    for url, expected in cases.items():
        cfg = gui.EnvConfig(base_url=url)
        assert cfg.provider_name() == expected, (url, expected)


@pytest.mark.unit
def test_parse_int_tolerates_garbage():
    assert gui._parse_int("", 8765) == 8765
    assert gui._parse_int(None, 8765) == 8765
    assert gui._parse_int("9000", 8765) == 9000
    # A non-integer is the form's fault, not the loader's; trust the form
    # and fall back rather than raise on a hand-edited file.
    assert gui._parse_int("not-a-number", 8765) == 8765


@pytest.mark.unit
def test_parse_bool_accepts_the_conventional_truthy_strings():
    assert gui._parse_bool("1", False) is True
    assert gui._parse_bool("true", False) is True
    assert gui._parse_bool("yes", False) is True
    assert gui._parse_bool("ON", False) is True
    # Anything else is the default, including an empty string and the
    # truthy-looking but not-in-the-set "y".
    assert gui._parse_bool("y", False) is False
    assert gui._parse_bool("", True) is True
    assert gui._parse_bool(None, True) is True


# --- launcher subprocess args -----------------------------------------------
#
# The frozen GUI cannot run the server itself - its bootloader is wired to
# `gui.py`, and asking it to `-m dots` reopens the launcher. Two regressions
# have been seen already: passing `ui` as an argv element, and reaching for
# `sys.executable` in a frozen build. These tests judge both.

class _FakePopen:
    """Stands in for `subprocess.Popen` and records the call."""

    instances: list = []

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self._returncode = None
        self.stdout = None
        type(self).instances.append(self)

    def poll(self):
        return self._returncode

    def wait(self, timeout=None):
        return 0

    def terminate(self):
        self._returncode = 0


@pytest.fixture
def fake_popen(monkeypatch):
    _FakePopen.instances = []
    monkeypatch.setattr(gui.subprocess, "Popen", _FakePopen)
    return _FakePopen


def _make_launcher(server_exe=None):
    return gui.Launcher(on_line=lambda _line: None,
                        on_exit=lambda _code: None,
                        server_exe=server_exe)


@pytest.mark.unit
def test_dev_invoke_does_not_pass_ui_in_argv(fake_popen, tmp_path):
    # Dev mode: `python -m dots` already runs `dots.cli.main`, which prepends
    # `ui` itself. Passing it here too would make the package's Click group
    # see `["ui", "ui", ...]` and reject the second `ui`.
    launcher = _make_launcher(server_exe=None)
    launcher.start(cwd=tmp_path, port=8765, headed=False, extra_args=[])
    args = fake_popen.instances[0].args
    assert "ui" not in args
    # The CLI is invoked via `python -m dots` so the package is found in the
    # same interpreter that runs the GUI.
    assert args[0] == gui.sys.executable
    assert "-m" in args
    assert "dots" in args


@pytest.mark.unit
def test_frozen_invoke_uses_sibling_dots_exe(fake_popen, tmp_path):
    # Frozen mode: the GUI's own exe is the wrong bootloader, so the launcher
    # uses the standalone `dots.exe` it found beside itself. `ui` is not
    # passed because `dots.exe`'s `__main__.py` already calls the `ui` path.
    server = str(tmp_path / "dots.exe")
    Path(server).write_bytes(b"")
    launcher = _make_launcher(server_exe=server)
    launcher.start(cwd=tmp_path, port=8765, headed=False, extra_args=[])
    args = fake_popen.instances[0].args
    assert args[0] == server
    assert "ui" not in args
    assert "-m" not in args


@pytest.mark.unit
def test_invoke_passes_host_127_999_and_port(fake_popen, tmp_path):
    launcher = _make_launcher()
    launcher.start(cwd=tmp_path, port=9000, headed=False, extra_args=[])
    args = fake_popen.instances[0].args
    # `127.0.0.1` is hard-coded: the GUI window is local, and a server
    # bound elsewhere is one the Open button cannot reach.
    assert "--host" in args
    assert args[args.index("--host") + 1] == "127.0.0.1"
    assert "--port" in args
    assert args[args.index("--port") + 1] == "9000"


@pytest.mark.unit
def test_frozen_server_exe_helper_returns_none_in_dev(monkeypatch):
    # `_frozen_server_exe` is what the App passes to the Launcher; in dev
    # mode it must return None so the launcher uses `-m dots`.
    monkeypatch.delattr(gui.sys, "frozen", raising=False)
    assert gui._frozen_server_exe() is None


@pytest.mark.unit
def test_frozen_server_exe_helper_finds_sibling(tmp_path, monkeypatch):
    # Frozen: the GUI's launcher looks first for `dots.exe` next to itself -
    # the layout `build.ps1` produces for distribution. Without the file,
    # the launcher falls back to `python -m dots` and lets the user see the
    # import error.
    sibling = tmp_path / "dots.exe"
    sibling.write_bytes(b"")
    fake_exe = tmp_path / "dots-gui.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(fake_exe))
    assert gui._frozen_server_exe() == str(sibling)


@pytest.mark.unit
def test_frozen_server_exe_helper_finds_sibling_dots_folder(tmp_path, monkeypatch):
    # PyInstaller's default output is one folder per `--name`: `dist/dots/`
    # and `dist/dots-gui/`. The helper looks in `../dots/dots.exe` so the
    # build still works without a manual merge.
    dist = tmp_path / "dist"
    dots_dir = dist / "dots"
    gui_dir = dist / "dots-gui"
    dots_dir.mkdir(parents=True)
    gui_dir.mkdir(parents=True)
    server = dots_dir / "dots.exe"
    server.write_bytes(b"")
    fake_exe = gui_dir / "dots-gui.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(fake_exe))
    assert gui._frozen_server_exe() == str(server)


@pytest.mark.unit
def test_frozen_server_exe_helper_returns_none_when_sibling_missing(
        tmp_path, monkeypatch):
    fake_exe = tmp_path / "dots-gui.exe"
    monkeypatch.setattr(gui.sys, "frozen", True, raising=False)
    monkeypatch.setattr(gui.sys, "executable", str(fake_exe))
    # No `dots.exe` anywhere the helper looks - the user copied only the
    # launcher. Better to fall back to `python -m dots` than to claim a
    # binary exists.
    assert gui._frozen_server_exe() is None


# --- auto-restart watchdog ---------------------------------------------------
#
# The watchdog's job is to keep a long-running task going across transient
# failures: a stale browser subprocess, an OOM kill, a `Link` MCP-socket
# drop. The agent's transcript lives on disk; the watchdog only respawns
# the server, and the user re-sends the last message.

import threading as _threading

_watch_wait_called = _threading.Event()


class _WatchPopen:
    """Popen stand-in whose lifecycle is driven by the test.

    `wait()` actually blocks on an event, the way the real thing
    blocks on a child process. Without that block the watchdog's
    `_watch_loop` returns from `proc.wait()` instantly, runs the
    "did this crash?" check, and races the test's `stop()`. The
    event-driven wait keeps the order the real Popen has: the flag
    is set BEFORE the process exits.
    """

    instances: list = []
    next_pid = 1000

    def __init__(self, args, **kwargs):
        self.args = args
        self.kwargs = kwargs
        self._returncode: Optional[int] = None
        self.stdout = None
        self.poll_calls = 0
        self.pid = type(self).next_pid
        type(self).next_pid += 1
        self._exit_event = _threading.Event()
        type(self).instances.append(self)

    def poll(self):
        self.poll_calls += 1
        return self._returncode

    def wait(self, timeout=None):
        if self._returncode is not None:
            return self._returncode
        # Block until the test signals an exit. Real Popen blocks on
        # the OS; here we block on a manual reset.
        if timeout is None:
            self._exit_event.wait()
        else:
            self._exit_event.wait(timeout=timeout)
        return self._returncode

    def terminate(self):
        self._returncode = 0
        self._exit_event.set()

    def kill(self):
        self._returncode = 0
        self._exit_event.set()


#: The launchers this module made, so that the next test can stop the
#: watchdog thread of the last one.
#:
#: ⛔ A WATCHDOG STILL BLOCKED IN `wait()` WHEN THE NEXT TEST REPLACED THE
#: INSTANCE LIST WAKES UP, RESPAWNS, AND APPENDS TO THE NEW LIST. The count
#: this suite is about then comes out one too many, and it comes out
#: DIFFERENTLY depending on which test ran before - which is what a fixed
#: sleep in the test was papering over.
_LAUNCHERS: list = []


def _make_watch_launcher(monkeypatch, **kw):
    for launcher in _LAUNCHERS:
        launcher.stop()
        # ⛔ AND THE WATCHDOG IS WAITED FOR. `stop()` asks it to stop, and the
        # watchdog checks that flag at its next step - which is a step that
        # happens AFTER it has decided to restart. So the old watchdog can
        # still spawn one more process, and it spawns it into the instance
        # list this test is about to replace. The count this suite is about
        # then comes out too high, and it comes out differently depending on
        # which test ran before.
        if launcher._watchdog is not None:
            launcher._watchdog.join(timeout=5)
    _LAUNCHERS.clear()
    monkeypatch.setattr(gui.subprocess, "Popen", _WatchPopen)
    # Unblock the waiters of the PREVIOUS test before the list is replaced -
    # the order matters, and getting it backwards unblocks nothing at all.
    for inst in _WatchPopen.instances:
        inst._exit_event.set()
    _WatchPopen.instances = []
    lines = []
    restarts = []
    launcher = gui.Launcher(
        on_line=lambda l: lines.append(l),
        on_exit=lambda c: None,
        on_restart=lambda up: restarts.append(up),
        max_restarts=kw.get("max_restarts", 3),
        restart_cooldown=kw.get("restart_cooldown", 0.0),
        auto_restart=kw.get("auto_restart", True),
        server_exe=None,
    )
    _LAUNCHERS.append(launcher)
    return launcher, lines, restarts


def _wait_for(predicate, seconds: float = 3.0) -> bool:
    """Wait for something to happen, instead of guessing how long it takes.

    ⛔ A FIXED SLEEP IS A RACE. The watchdog is a thread, and on a loaded
    machine it may not be scheduled inside the window the test guessed - so
    the count the test is about came out one short, or one too many, for
    reasons that have nothing to do with the watchdog. Waiting for the
    thing itself is what the test meant.
    """
    import time

    deadline = time.time() + seconds
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


@pytest.mark.unit
def test_watchdog_does_not_restart_after_intentional_stop(fake_popen, monkeypatch, tmp_path):
    # A user-initiated Stop is not a crash. The watchdog sees the exit,
    # sees the flag `_intentional_stop` set by `stop()`, and lets the
    # process stay down.
    launcher, lines, restarts = _make_watch_launcher(monkeypatch)
    launcher.start(cwd=tmp_path, port=8765, headed=False, extra_args=[])
    proc = _WatchPopen.instances[0]
    # stop() sets the flag, then calls terminate(); terminate() sets
    # the returncode and unblocks wait(). This is the real Popen order
    # and the only one the watchdog can rely on.
    launcher.stop()
    # Give the watchdog the moment it needs to see the exit, and check that
    # it did NOT act: a respawn here would be the bug.
    _wait_for(lambda: any("auto-restarted" in l for l in lines), seconds=0.5)
    assert len(_WatchPopen.instances) == 1
    assert restarts == []


@pytest.mark.unit
def test_watchdog_restarts_after_unexpected_exit(fake_popen, monkeypatch, tmp_path):
    # The server died on its own (Link crash, OOM, anything). The watchdog
    # respawns it; the GUI sees `on_restart(False)` then `on_restart(True)`.
    launcher, lines, restarts = _make_watch_launcher(
        monkeypatch, restart_cooldown=0.0)
    launcher.start(cwd=tmp_path, port=8765, headed=False, extra_args=[])
    proc = _WatchPopen.instances[0]
    # Simulate the OS signalling the process died on its own: returncode
    # is set, the event is fired, wait() unblocks. `_intentional_stop`
    # was never set, so the watchdog treats this as a crash.
    proc._returncode = 1
    proc._exit_event.set()
    # ⛔ WAIT FOR THE LINE, NOT FOR THE SPAWN. The watchdog respawns FIRST
    # and says so second, so a wait on the instance count is satisfied a
    # moment before the word "auto-restarted" is in the log - and the
    # assertion after it then fails for a reason that has nothing to do with
    # the watchdog.
    assert _wait_for(lambda: any("auto-restarted" in l for l in lines)), (
        "the watchdog did not say it respawned: %r" % (lines,))
    assert len(_WatchPopen.instances) == 2, (
        "expected one respawn, got %d" % len(_WatchPopen.instances))
    assert restarts == [False, True]


@pytest.mark.unit
def test_watchdog_respects_max_restarts(fake_popen, monkeypatch, tmp_path):
    # A process that crashes immediately on every start should not be
    # respawned forever. After `max_restarts` attempts, the watchdog
    # gives up and the user sees the error.
    launcher, lines, restarts = _make_watch_launcher(
        monkeypatch, max_restarts=2, restart_cooldown=0.0)
    launcher.start(cwd=tmp_path, port=8765, headed=False, extra_args=[])
    # Each iteration: crash the latest process, and WAIT for the watchdog to
    # either restart it or give up - rather than sleeping a guessed number of
    # seconds and counting whatever it managed to do in the meantime.
    for _ in range(4):
        if not _WatchPopen.instances:
            break
        before = len(_WatchPopen.instances)
        latest = _WatchPopen.instances[-1]
        latest._returncode = 1
        latest._exit_event.set()
        _wait_for(lambda: (len(_WatchPopen.instances) != before
                            or any("auto-restart limit" in l for l in lines)))
    # Original + 2 restarts = 3 spawns.
    assert len(_WatchPopen.instances) == 3
    assert any("auto-restart limit" in l for l in lines)


# --- preflight ---------------------------------------------------------------
#
# The preflight is what keeps a bad `.env` from producing a 30-line
# traceback in the log a second after Start is clicked. Each check
# corresponds to a class of error that was once a mystery to a user.
#
# The preflight tests build a real `App` - which means Tk has to
# initialise. On a CI runner without a display the import succeeds but
# the first `Tk()` fails. Skip rather than fail in that case; the
# preflight code itself is exercised whenever the user runs the GUI
# on their own machine.

# ⛔ THE TK CHECK RUNS IN A SUBPROCESS, BECAUSE IT CAN HANG. `tk.Tk()` on a
# machine whose Tk library is unhappy does not always fail - sometimes it
# never returns, and a check that never returns is a suite that never
# finishes. Measured here: the whole run stopped dead at this line, with the
# stack inside `tkinter.Tk.__init__`.
#
# So the question is asked of another process, with a deadline. A subprocess
# that hangs is killed; a subprocess that raises is a non-zero exit; and
# either way the answer is "Tk is not usable here" and the tests that need
# it skip themselves rather than hang the run.
def _tk_is_usable() -> bool:
    import subprocess
    import sys

    probe = ("import tkinter; t = tkinter.Tk(); t.withdraw(); t.destroy()")
    try:
        done = subprocess.run([sys.executable, "-c", probe],
                              stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, timeout=30)
    except Exception:
        return False
    return done.returncode == 0


_TK_OK = _tk_is_usable()
preflight_skip = pytest.mark.skipif(
    not _TK_OK, reason="Tk is not available in this environment")


# --- preflight ---------------------------------------------------------------
#
# The preflight is what keeps a bad `.env` from producing a 30-line
# traceback in the log a second after Start is clicked. Each check
# corresponds to a class of error that was once a mystery to a user.

def _free_port() -> int:
    """A port the OS will let us bind to for the duration of a test."""
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


#: The one `App` this module shares. See `_make_app`.
_APP = None


def _tcl_itself_failed(exc: BaseException) -> bool:
    """Whether the traceback ends inside tkinter rather than inside `dots`.

    The last frame is the question, and it is asked this way because it is the
    only one that separates "our code raised" from "the interpreter never
    came up".
    """
    frame = exc.__traceback__
    last = ""
    while frame is not None:
        last = frame.tb_frame.f_code.co_filename
        frame = frame.tb_next
    return "tkinter" in last


def _build_app():
    """`gui.App()`, retried while the failure is Tcl's own and not ours.

    ⛔ THIS PYTHON'S Tcl SOMETIMES REFUSES TO INITIALISE. The traceback ends
    inside `tkinter.Tk.__init__` with "This probably means that tk wasn't
    installed properly", on about one full-suite run in four on the machine
    this was written on, and never when this module runs on its own; the
    second attempt works, which is not what a broken install does. It is a
    CPython/Tcl problem that happens before a line of `dots` has run.

    Only THAT is retried, three times and no more. A traceback ending in
    `dots/gui.py` is ours, is raised at once, and so cannot be stood in for.
    """
    import tkinter

    last = None
    for _ in range(3):
        try:
            return gui.App()
        except tkinter.TclError as exc:
            last = exc
            if not _tcl_itself_failed(exc):
                raise
    raise last


def _make_app():
    """The shared `App`, built once, with the form put back as it was.

    Imports tkinter, which needs a display; these are the only tests that
    actually open Tk.

    ⛔ IT WAS BUILT AND DESTROYED PER TEST. One window for the module is what
    these tests actually needed - every one of them sets the fields it judges -
    and one window cannot decide another one's answer by typing into it, which
    is why the form is reset on the way out.
    """
    global _APP
    if _APP is None:
        _APP = _build_app()
    app = _APP
    app.update_idletasks()
    for var in (app._api_key_var, app._base_url_var, app._model_var,
                app._proxy_var, app._seed_var, app._device_var,
                app._profile_dir_var, app._workdir_var):
        var.set("")
    app._port_var.set(8765)
    app._headed_var.set(False)
    app._profile_var.set(gui.NO_PROFILE)
    return app


@pytest.fixture(scope="module", autouse=True)
def _close_the_shared_app():
    """Close the shared window when the module is done with it."""
    yield
    if _APP is not None:
        _APP.destroy()


@pytest.mark.unit
def test_the_launcher_finds_the_key_where_there_is_no_env(tmp_path, monkeypatch):
    # ⛔ THE LAUNCHER'S FOLDER IS THE PROJECT'S, AND A LAUNCHER OPENED
    # ELSEWHERE SHOWED AN EMPTY KEY. The personal file fills the gap, and the
    # project's own key still wins over it.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "home" / ".env").write_text(
        "OPENAI_API_KEY=sk-personal\nOPENAI_MODEL=personal-model\n",
        encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    config = gui.EnvConfig.from_env(elsewhere)
    assert config.api_key == "sk-personal"
    assert config.model == "personal-model"

    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("OPENAI_API_KEY=sk-project\n",
                                   encoding="utf-8")
    config = gui.EnvConfig.from_env(project)
    assert config.api_key == "sk-project"


@pytest.mark.unit
def test_the_launcher_remembers_the_folder_it_was_used_in(tmp_path, monkeypatch):
    # ⛔ THE LAUNCHER IS OPENED FROM EVERYWHERE, AND THE PROJECT IS IN ONE
    # PLACE. Without this, opening it from the desktop found no project and
    # no key, and read as a broken program.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("OPENAI_API_KEY=sk-project\n",
                                   encoding="utf-8")

    app = _make_app()
    app._cwd_var.set(str(project))
    app._remember_directory(project)
    # The window this module shares is NOT destroyed here: it belongs to the
    # module, and every test after this one needs it.
    assert app._remembered_directory() == project
    said = json.loads((tmp_path / "home" / "launcher.json")
                      .read_text(encoding="utf-8"))
    assert said["cwd"] == str(project)


@pytest.mark.unit
def test_the_buttons_stay_where_they_can_be_reached():
    # ⛔ THE FORM SCROLLS AND THE BUTTONS DO NOT. The window grew past the
    # screen - a row here, a row there - and the button bar ended up below
    # the bottom edge, where a click finds nothing. The bar is packed on the
    # window and the form is packed on a canvas above it, so the bar is
    # always the last thing on the screen and the first thing to be found.
    app = _make_app()
    app.update_idletasks()
    bar = [child for child in app.winfo_children()
           if child.winfo_class() == "TFrame" and child.winfo_name() == "!frame"]
    assert bar, "the button bar is a frame on the window"
    assert bar[0].winfo_manager() == "pack"
    # And it is BELOW the canvas, not inside it. The order they were PACKED in
    # is the order they are laid out in, and the canvas was packed first.
    canvas = app._form_canvas
    assert canvas.winfo_manager() == "pack"
    assert app.winfo_children().index(canvas) < app.winfo_children().index(bar[0])


@pytest.mark.unit
def test_the_form_scrolls_when_it_is_taller_than_the_window():
    app = _make_app()
    app.update_idletasks()
    # The content is taller than the canvas - that is the whole reason for
    # the canvas - and the scroll region says so.
    region = app._form_canvas.bbox("all")
    assert region is not None
    assert region[3] > app._form_canvas.winfo_height()


@pytest.mark.unit
def test_the_launcher_writes_its_log_to_a_file():
    # ⛔ THE PANE IS NOT A LOG. It is a view of one, and it is the only place
    # the servers' output appears - so a launcher that closes takes every word
    # of it with it, and the person is left with a window that vanished and
    # nothing to say why.
    app = _make_app()
    app._log_line("a line worth keeping")
    app._log_handle.flush()
    path = Path(app._log_handle.name)
    assert path.is_file()
    assert "a line worth keeping" in path.read_text(encoding="utf-8")


@pytest.mark.unit
def test_the_device_of_a_profile_is_shown_and_saved(tmp_path, monkeypatch):
    # The device is a field on the form like the rest: picking a profile
    # shows what it claims, and Save writes what the box says back into the
    # profile.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    from dots import profiles as registry

    made = registry.create("work", seed=4242, pin={"screen.width": 1920})
    app = _make_app()
    app._profile_var.set("work")
    app._on_profile_pick()
    assert app._device_var.get() == "screen.width=1920"

    app._device_var.set("screen.width=1600, screen.dpr=1.25")
    app._on_profile_save()
    assert registry.get("work").pin == {"screen.width": 1600, "screen.dpr": 1.25}
    assert made.name == "work"


@pytest.mark.unit
def test_the_line_under_the_device_says_what_the_profile_claims(tmp_path,
                                                                 monkeypatch):
    # The field holds the pins; the line under it holds what they add up to,
    # because a number nobody can see is a number nobody can check.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    from dots import profiles as registry

    registry.create("work", seed=1361434516, pin={"screen.dpr": 1.25})
    app = _make_app()
    app._profile_var.set("work")
    app._on_profile_pick()
    said = app._device_hint_var.get()
    assert "1920x1080" in said
    assert "ratio 1.25" in said
    assert "screen.dpr=1.25" in said


@pytest.mark.unit
def test_the_check_asks_the_server_and_says_what_it_answered(monkeypatch):
    # The browser is in another process, so the check is a question over
    # HTTP and the answer is posted back to the Tk thread.
    import urllib.request

    class _Answer:
        def read(self):
            return b'{"ok": true, "report": "the browser is the device"}'

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

    monkeypatch.setattr(urllib.request, "urlopen",
                        lambda *_a, **_k: _Answer())
    app = _make_app()
    app._ask_the_server("http://127.0.0.1:8765/live/device")
    kind, _name, payload = app._events.get_nowait()
    assert kind == "device"
    assert "the browser is the device" in payload


@pytest.mark.unit
def test_a_check_that_cannot_reach_the_server_says_so(monkeypatch):
    import urllib.request

    def _refused(*_a, **_k):
        raise OSError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _refused)
    app = _make_app()
    app._ask_the_server("http://127.0.0.1:8765/live/device")
    kind, _name, payload = app._events.get_nowait()
    assert kind == "device"
    assert "could not ask the server" in payload


@pytest.mark.unit
def test_every_text_field_has_a_context_menu():
    # ⛔ TK HAS NO CONTEXT MENU OF ITS OWN, and the report that comes out of
    # that is "paste does not work here" - the right button does nothing at
    # all, because there is no menu to open. Every field in the window gets
    # one, and the walk is over the widget tree rather than over a list of
    # names, so a field added later is covered without anybody remembering.
    app = _make_app()
    fields = app._text_fields()
    assert fields, "the window has text fields, and every one of them needs a menu"
    for field in fields:
        assert field.bind("<Button-3>") != "", "a field with no right-click menu"


@pytest.mark.unit
def test_the_menu_pastes_what_the_clipboard_holds():
    app = _make_app()
    field = app._text_fields()[0]
    app.clipboard_clear()
    app.clipboard_append("PASTED")
    app.update()
    field.delete(0, "end")
    field.focus_force()
    app.update()
    field.event_generate("<<Paste>>")
    app.update()
    assert field.get() == "PASTED"


@pytest.mark.unit
def test_the_menu_pastes_into_a_password_field_too():
    # A masked field is still a field somebody pastes a key into.
    app = _make_app()
    masked = [f for f in app._text_fields()
              if str(f.cget("show") or "") == "*"]
    assert masked, "there is a masked field, and it needs a menu as well"
    app.clipboard_clear()
    app.clipboard_append("secret")
    app.update()
    masked[0].delete(0, "end")
    masked[0].focus_force()
    app.update()
    masked[0].event_generate("<<Paste>>")
    app.update()
    assert masked[0].get() == "secret"


@pytest.mark.unit
def test_preflight_rejects_empty_api_key(monkeypatch):
    app = _make_app()
    app._api_key_var.set("")
    config = app._config_from_form()
    problem = app._preflight(config)
    assert problem is not None
    assert "API key" in problem


@pytest.mark.unit
def test_preflight_rejects_malformed_base_url(monkeypatch):
    app = _make_app()
    app._api_key_var.set("sk-test")
    app._base_url_var.set("not-a-url")
    config = app._config_from_form()
    problem = app._preflight(config)
    assert problem is not None
    assert "http" in problem or "URL" in problem


@pytest.mark.unit
def test_preflight_rejects_already_bound_port(monkeypatch):
    # Bind a port ourselves; preflight on the same port must refuse.
    import socket
    held = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    held.bind(("127.0.0.1", 0))
    held.listen(1)
    port = held.getsockname()[1]
    try:
        app = _make_app()
        app._api_key_var.set("sk-test")
        app._port_var.set(port)
        config = app._config_from_form()
        problem = app._preflight(config)
        assert problem is not None
        assert str(port) in problem
    finally:
        held.close()


@pytest.mark.unit
def test_preflight_accepts_a_valid_config(monkeypatch):
    # A free port and a sane key/URL must produce no error.
    app = _make_app()
    app._api_key_var.set("sk-test")
    app._base_url_var.set("https://api.example.com/v1")
    app._port_var.set(_free_port())
    config = app._config_from_form()
    assert app._preflight(config) is None