"""`dots-gui`: a native Windows launcher for the `dots` server.

The web UI served at `http://127.0.0.1:8765` is the product; this window is
the friendly way to configure `.env`, start the server and read its log
without typing on a command line. It is a launcher and nothing more: every
click writes a file or runs the same CLI everyone else runs.

The package depends only on `tkinter`, which ships with the Python that
ships with `uv` and the one from python.org. No third-party GUI library is
pulled in, so installing `dots-gui` does not change what the CLI install
already weighs.
"""
from __future__ import annotations

# ⛔ OPT-IN DIAGNOSTIC LOG. The frozen GUI runs without a console, so
# anything that crashes on import or in `main()` is invisible. Set
# `DOTS_GUI_LOG=path/to/file` to capture tracebacks. Empty by default -
# the user paid for a clean install, not a `dots-gui.log` next to the
# exe that they have to know is safe to delete.
import os as _os_log

_DIAG_PATH = _os_log.environ.get("DOTS_GUI_LOG")


def _diag_log(msg: str) -> None:
    if not _DIAG_PATH:
        return
    try:
        with open(_DIAG_PATH, "a", encoding="utf-8") as f:
            f.write(msg + "\n")
    except Exception:
        pass


import json
import os
import queue
import shlex
import subprocess
import sys
import threading
import time
import traceback
import webbrowser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from tkinter import filedialog, messagebox, simpledialog, ttk
import tkinter as tk

from dotenv import dotenv_values

from . import auths as vault
from . import geometry
from . import handover
from . import profiles as registry

ENV_FILENAME = ".env"

#: The name in `.env` that says which profile this directory runs as. It is
#: the GUI's own file next to `OPENAI_API_KEY` and the rest, and `dots.cli`
#: reads the same name - which is the point of it being written rather than
#: passed only as a flag.
ACTIVE_PROFILE_VARIABLE = registry.ACTIVE_VARIABLE

#: What the profile dropdown shows when no profile is chosen. Not an empty
#: string, because an entry a user can put the cursor in is an entry they will
#: type into; and not a real profile name, because "(no profile)" must never
#: become a profile.
NO_PROFILE = "(no profile)"

#: Recognised providers, with the base URL each one implies. `Custom` is the
#: catch-all that exposes the base-URL field free-form.
PROVIDERS: list[tuple[str, str]] = [
    ("OpenRouter", ""),
    ("OpenAI", "https://api.openai.com/v1"),
    ("DeepSeek", "https://api.deepseek.com/v1"),
    ("Together AI", "https://api.together.xyz/v1"),
    ("Groq", "https://api.groq.com/openai/v1"),
    ("Mistral", "https://api.mistral.ai/v1"),
    ("Ollama (local)", "http://127.0.0.1:11434/v1"),
    ("LM Studio (local)", "http://127.0.0.1:1234/v1"),
    ("llama.cpp (local)", "http://127.0.0.1:8080/v1"),
    ("Custom", ""),
]

#: Substrings that name a provider when the URL itself is not in the list.
#: OpenRouter has no entry in `PROVIDERS` because the GUI keeps its base URL
#: empty for that case - a non-empty URL still has to be recognised as
#: OpenRouter when somebody pasted the full endpoint in.
PROVIDER_HINTS: dict[str, str] = {
    "openrouter.ai": "OpenRouter",
    "api.openai.com": "OpenAI",
    "deepseek": "DeepSeek",
    "together.xyz": "Together AI",
    "groq.com": "Groq",
    "mistral.ai": "Mistral",
    "11434": "Ollama (local)",
    "1234": "LM Studio (local)",
    "8080": "llama.cpp (local)",
}


# ---------------------------------------------------------------------------
# .env handling
# ---------------------------------------------------------------------------

@dataclass
class EnvConfig:
    """The values the GUI writes to `.env`.

    Empty strings mean "not set". The launcher never asks which provider is
    in use: an empty `base_url` is OpenRouter, anything else is whatever the
    URL points at. That is the rule `dots.cli` already follows, and the GUI
    just mirrors it so the file the GUI writes is the file the CLI reads.

    `profile_name` is the one field here that is not a value the server reads:
    the browser options are passed as flags, but a profile is a NAME, and the
    name has to be in `.env` so that `dots` run from a terminal in this
    directory comes up as the same person the GUI started.
    """

    api_key: str = ""
    base_url: str = ""
    model: str = ""
    proxy: str = ""
    seed: str = ""
    profile_dir: str = ""
    profile_name: str = ""
    port: int = 8765
    headed: bool = False

    @classmethod
    def from_env(cls, directory: Path) -> "EnvConfig":
        """Read `.env` from `directory`, only what it actually sets.

        A line that is missing or commented out is an empty field, never a
        default: the default belongs to `dots`, and the GUI loading the
        defaults on an absent field would let an empty file look the same as
        the one shipped as an example.

        ⛔ AND THE FILE THAT APPLIES TO EVERY DIRECTORY FILLS THE GAPS. The
        launcher's own directory is the project's, and the project has its
        key; but a launcher opened in a folder that has no `.env` at all would
        show an empty key and refuse to start, which reads as a broken
        program. So the personal file is read second and can only supply what
        the project did not - the project's own key still wins.
        """
        path = directory / ENV_FILENAME
        values = dict(dotenv_values(path)) if path.is_file() else {}
        personal = registry.user_env_path()
        if personal.is_file() and personal != path:
            for name, value in dotenv_values(personal).items():
                if value is not None and name not in values:
                    values[name] = value
        return cls(
            api_key=values.get("OPENAI_API_KEY", "") or "",
            base_url=values.get("OPENAI_BASE_URL", "") or "",
            model=values.get("OPENAI_MODEL", "") or "",
            proxy=values.get("INVISIBLE_MCP_PROXY", "") or "",
            seed=values.get("INVISIBLE_MCP_SEED", "") or "",
            profile_dir=values.get("INVISIBLE_MCP_PROFILE_DIR", "") or "",
            profile_name=values.get(ACTIVE_PROFILE_VARIABLE, "") or "",
            port=_parse_int(values.get("INVISIBLE_MCP_PORT"), 8765),
            headed=_parse_bool(values.get("INVISIBLE_MCP_HEADED"), False),
        )

    def to_env(self) -> dict[str, str]:
        """The variables the file should hold, with only what is non-empty.

        An empty field is omitted: a `.env` line like `OPENAI_BASE_URL=` is
        not the same as no line at all - it pins an empty value, and the
        loader treats an empty value as absent, which is the silent half of
        the rule the file's whole format is in.  Setting it to empty is not
        "use the default", it is "this name has an empty value".  Omitting
        it is what lets the loader answer "who decides".

        ⛔ AND THE PORT IS NOT WRITTEN WHEN A PROFILE IS CHOSEN. The port
        belongs to the profile then - it is in the registry, and two profiles
        running at once need two of them - and a second copy in `.env` is a
        second copy to disagree with the first: the file would say 8765 and
        the profile 8766, and the one that wins would depend on which was
        read last.
        """
        out: dict[str, str] = {}
        if self.api_key:
            out["OPENAI_API_KEY"] = self.api_key
        if self.base_url:
            out["OPENAI_BASE_URL"] = self.base_url.rstrip("/")
        if self.model:
            out["OPENAI_MODEL"] = self.model
        if self.proxy:
            out["INVISIBLE_MCP_PROXY"] = self.proxy
        if self.seed:
            out["INVISIBLE_MCP_SEED"] = self.seed
        if self.profile_dir:
            out["INVISIBLE_MCP_PROFILE_DIR"] = self.profile_dir
        if self.profile_name:
            out[ACTIVE_PROFILE_VARIABLE] = self.profile_name
        if not self.profile_name and self.port and self.port != 8765:
            out["INVISIBLE_MCP_PORT"] = str(self.port)
        if self.headed:
            out["INVISIBLE_MCP_HEADED"] = "1"
        return out

    def provider_name(self) -> str:
        """The dropdown row the form should land on when re-opened.

        Best-effort match: empty base URL is OpenRouter, a substring hits the
        obvious entries, anything else is `Custom` with the URL filled in.
        Wrong guesses are harmless: the user can re-pick, and what is written
        is what the dropdown says, not what the matcher thought.
        """
        if not self.base_url:
            return "OpenRouter"
        low = self.base_url.lower()
        for name, url in PROVIDERS:
            if name == "Custom" or not url:
                continue
            if url.lower() in low:
                return name
        for hint, name in PROVIDER_HINTS.items():
            if hint in low:
                return name
        return "Custom"

    def model_suggestion(self, provider: str) -> str:
        """A reasonable default for `provider`, kept in the field as a hint.

        Empty when nothing obvious comes to mind. The field is editable; the
        suggestion is a starting point and the only purpose of having one is
        to save the user typing the model name they were about to type.
        """
        return {
            "OpenRouter": "z-ai/glm-5.3-flash",
            "OpenAI": "gpt-4o",
            "DeepSeek": "deepseek-chat",
            "Together AI": "meta-llama/Llama-3.3-70B-Instruct-Turbo",
            "Groq": "llama-3.3-70b-versatile",
            "Mistral": "mistral-large-latest",
            "Ollama (local)": "qwen2.5:14b",
            "LM Studio (local)": "",
            "llama.cpp (local)": "",
            "Custom": "",
        }.get(provider, "")


def _parse_int(value: Optional[str], default: int) -> int:
    """The integer the string holds, or `default` on garbage.

    A bad value is not the form's job to scream about: the form trusts
    itself to produce integers, and the loader is a best-effort read of a
    file a person might have edited by hand.
    """
    if value is None or value == "":
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _parse_bool(value: Optional[str], default: bool) -> bool:
    """`1` / `true` / `yes` (case-insensitive) is True, the rest is `default`.

    Mirrors what people type by hand: `1` is the convention `.env` files
    inherit from shell, and `true` is what an `export FOO=true` line
    produces when copy-pasted from a tutorial.
    """
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def write_env(directory: Path, config: EnvConfig) -> None:
    """Write `.env` in `directory` from `config`, replacing any old file.

    The comment header is what the next person reading the file sees first,
    and it tells them what each line is for and where the precedence lives.
    A file without a comment is a file that someone has to read before they
    know what it does.
    """
    lines = [
        "# dots configuration. Read by dots-gui, written by dots-gui.",
        "# Precedence is: --flag > the shell > this file > the default.",
        "",
    ]
    for name, value in config.to_env().items():
        lines.append("%s=%s" % (name, value))
    Path(directory / ENV_FILENAME).write_text(
        "\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# Subprocess management
# ---------------------------------------------------------------------------

#: Windows-only flag that hides the child's console window. The CLI in this
#: package emits progress lines on stdout; when a person is running the GUI,
#: they are not at a terminal, and a console popping up under the window is
#: the second thing they would have to close before anything works.
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _frozen_server_exe() -> Optional[str]:
    """The `dots.exe` to launch from a frozen GUI, or None in dev mode.

    A frozen `dots-gui.exe` cannot run the server itself: its bootloader
    is wired to `gui.py`, and asking it to start one spawns another copy of
    the window. The launcher must call a separate `dots.exe` and the build
    can place it in any of a few predictable places.

    ⛔ THE FROZEN `dots.exe` ITSELF IS NOT USABLE FOR THE WEB UI. The
    pinned `invisible-playwright-mcp` package spawns its MCP server
    child as `python -m invisible_playwright_mcp` (Link._params), and a
    PyInstaller bootloader is not a Python interpreter. The web UI
    requires a real Python to start, and the supported way to get one
    is to run the GUI via `uvx --from ... dots-gui`, which provides one.

    This helper still finds the binary when the build produced one, so
    a frozen `dots-mcp.exe` (the MCP-server-only build) is reachable
    from the same launcher if the user knows to point it at the right
    place. The web UI is not.
    """
    if not getattr(sys, "frozen", False):
        return None
    here = Path(sys.executable).parent
    candidates = [
        here / "dots.exe",                       # distribution layout
        here / "dots" / "dots.exe",              # build.ps1 default output
        here.parent / "dots" / "dots.exe",       # PyInstaller default output
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


class Launcher:
    """The running `dots` process, or the absence of one.

    A process and its log are bound together: the thread that reads stdout
    is the one that closes it, and `start` / `stop` are the only places
    that ever touch either. The GUI calls back into the Tk thread to
    surface log lines, and `call_on_ui_thread` is the seam that does it.

    A watchdog sits on a third thread and re-spawns the server when it
    dies on its own. The user does not have to click `Start` again after
    a transient crash: a stale browser subprocess, an out-of-memory
    kill, a `Link` MCP-socket drop - any of these take the process down
    and the watchdog brings it back, up to `max_restarts` times, with
    exponential cooldown. The agent's transcript is on disk and is
    reloaded on reconnect, so the task can be re-sent.
    """

    def __init__(self, on_line: Callable[[str], None],
                 on_exit: Callable[[int], None], *,
                 server_exe: Optional[str] = None,
                 auto_restart: bool = True,
                 max_restarts: int = 3,
                 restart_cooldown: float = 5.0,
                 on_restart: Optional[Callable[[bool], None]] = None) -> None:
        self._on_line = on_line
        self._on_exit = on_exit
        self._on_restart = on_restart
        self._proc: Optional[subprocess.Popen] = None
        self._reader: Optional[threading.Thread] = None
        self._server_exe = server_exe
        self._auto_restart = auto_restart
        self._max_restarts = max_restarts
        self._restart_cooldown = restart_cooldown
        self._restart_count = 0
        self._intentional_stop = False
        self._last_cwd: Optional[Path] = None
        self._last_port: Optional[int] = None
        self._last_headed: Optional[bool] = None
        self._last_extra_args: Optional[list] = None
        self._watchdog: Optional[threading.Thread] = None

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def start(self, *, cwd: Path, port: int, headed: bool,
              extra_args: list[str]) -> None:
        """Spawn the server in `cwd`, with the GUI's port and headed flag.

        Two shapes, picked by `server_exe`:

          * dev (default) - the same Python interpreter that runs the GUI
            is asked to do `import dots` via `-m`. `dots/__main__.py` calls
            `dots.cli.main`, which prepends `ui` to argv and hands the rest
            to the package's Click group.

          * frozen - the GUI exe is not the right bootloader for the server
            (it would just reopen the GUI), so we spawn the sibling
            `dots.exe` directly. Its `__main__.py` runs the same code path;
            we do NOT prepend `ui` ourselves because the exe already runs in
            that mode.

        `--host 127.0.0.1` is hard-coded on both paths: the GUI window is
        local, and a server bound elsewhere is one the Open button cannot
        reach.
        """
        if self.running:
            raise RuntimeError("dots is already running")
        self._last_cwd = cwd
        self._last_port = port
        self._last_headed = headed
        self._last_extra_args = list(extra_args)
        self._restart_count = 0
        self._intentional_stop = False
        self._spawn(cwd, port, headed, extra_args)
        self._start_watchdog()

    def _spawn(self, cwd: Path, port: int, headed: bool,
               extra_args: list[str]) -> None:
        """Start the server subprocess, raising RuntimeError on failure.

        Public callers go through `start`; the watchdog reuses this so a
        restart reuses the same args the user clicked Start with, and
        `_intentional_stop` distinguishes a user Stop from a watchdog
        restart.
        """
        if self._server_exe is not None:
            args = [
                self._server_exe,
                "--host", "127.0.0.1",
                "--port", str(port),
            ]
        else:
            # ⛔ `-u`, BECAUSE THE CHILD'S OUTPUT GOES INTO A PIPE. A
            # process whose stdout is a terminal writes a line when it says
            # it; a process whose stdout is a PIPE buffers, and the buffer is
            # eight kilobytes. The server's banner - "connected, 27 tools" -
            # sat in the child's buffer and never reached the log pane, so the
            # pane was empty for the whole life of the window and the person
            # watching had nothing to read when something went wrong.
            args = [
                sys.executable, "-u", "-m", "dots",
                "--host", "127.0.0.1",
                "--port", str(port),
            ]
        if headed:
            args.append("--headed")
        args.extend(extra_args)
        try:
            self._proc = subprocess.Popen(
                args, cwd=str(cwd),
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, bufsize=1,
                # The same reason as `-u`, for a frozen build that has no
                # command line to put it on.
                env={**os.environ, "PYTHONUNBUFFERED": "1"},
                creationflags=_NO_WINDOW,
            )
        except OSError as e:
            self._proc = None
            raise RuntimeError(
                "failed to start dots: %s. Is the package installed "
                "in this Python environment?" % e) from e
        self._reader = threading.Thread(
            target=self._drain, name="dots-log-reader", daemon=True)
        self._reader.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Ask the child to exit, then kill it if it has not by `timeout`.

        Two stages because a polite shutdown lets the server close its
        sessions cleanly, and an ungraceful one is what kills in-flight
        requests mid-write. A five-second window is generous: the server
        itself is a long-lived uvicorn, and stopping it on Ctrl+C in the
        CLI is sub-second.

        `stop` is the only place that flips `_intentional_stop`, and the
        watchdog checks that flag before deciding to restart.
        """
        self._intentional_stop = True
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            proc.terminate()
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        finally:
            self._proc = None

    def _drain(self) -> None:
        """Read every line the child emits and surface it through the GUI.

        Runs on its own thread, so a slow terminal cannot block the Tk main
        loop. The reader dies with the process: when `poll()` returns
        non-None, the loop ends and the exit callback fires once.
        """
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        for line in proc.stdout:
            self._on_line(line.rstrip())
        code = proc.wait()
        self._on_exit(code)

    def _start_watchdog(self) -> None:
        """Start (or no-op restart of) the watchdog thread.

        The watchdog blocks on `proc.wait()` - the OS-level exit of the
        server - and reacts to that, not to anything the server logs.
        Polling would race the log-reader for stdout, and a read on a
        closed pipe is a different signal from the process going away.
        """
        if self._watchdog and self._watchdog.is_alive():
            return
        self._watchdog = threading.Thread(
            target=self._watch_loop, name="dots-launcher-watchdog",
            daemon=True)
        self._watchdog.start()

    def _watch_loop(self) -> None:
        """Block on the current process; restart if it died on its own.

        The loop runs once per spawned process: the watchdog grabs
        `self._proc` at the top of the iteration, blocks on its exit,
        and on a non-stop exit restarts. The next iteration then
        watches the new process. Without the loop, a process that
        dies immediately would only be restarted once - the watchdog
        would have already returned, holding the now-stale reference
        to the original Popen.

        `max_restarts` bounds how many of those one-shot deaths the
        launcher will tolerate before letting the user see the error.
        """
        while True:
            proc = self._proc
            if proc is None:
                return
            proc.wait()
            if self._intentional_stop or not self._auto_restart:
                return
            if self._restart_count >= self._max_restarts:
                self._on_line(
                    f"[server crashed, auto-restart limit ({self._max_restarts}) reached]")
                return
            self._restart_count += 1
            cooldown = self._restart_cooldown * self._restart_count
            if self._on_restart is not None:
                self._on_restart(False)  # about to restart
            self._on_line(
                "[server crashed, auto-restarting in %.0fs (%d/%d)]"
                % (cooldown, self._restart_count, self._max_restarts))
            time.sleep(cooldown)
            # ⛔ AND THE STOP IS CHECKED AGAIN, AFTER THE WAIT. The check
            # above happens before the sleep, so a Stop that arrives while the
            # watchdog is waiting is not seen at all - and the server comes
            # back up a few seconds after the person asked for it to stop,
            # which is the one thing a Stop must never do.
            if self._intentional_stop:
                return
            try:
                self._spawn(self._last_cwd, self._last_port,
                            self._last_headed, self._last_extra_args)
                self._on_line(f"[server auto-restarted, PID={self._proc.pid}]")
                if self._on_restart is not None:
                    self._on_restart(True)  # back up
            except RuntimeError as e:
                self._on_line(f"[auto-restart failed: {e}]")
                return


# ---------------------------------------------------------------------------
# Tk window
# ---------------------------------------------------------------------------

#: Whether this process has already paid the cold-start cost. One warm-up per
#: process, however many windows it opens.
_WARMED = False


class App(tk.Tk):
    """The launcher window.

    The form is the source of truth while it is on screen; the file is
    written only on `Save`. That keeps a half-edited form from surprising
    the next run, and it lets the user cancel by closing the window.
    """

    def __init__(self) -> None:
        super().__init__()
        self.title("dots — launcher")
        # ⛔ WIDE ENOUGH FOR THE FIELDS, WHICH ARE THE POINT OF THE WINDOW.
        # 760px fitted the old four-row browser panel; with the profile row's
        # three buttons beside it, a folder entry showing more than the first
        # two letters of a path, and an authorizations table under that, the
        # same width squeezes the entry column down to nothing.
        self.geometry("960x960+100+40")
        self.minsize(820, 780)

        self._launchers: dict[str, Launcher] = {}
        self._warm_procs: list = []
        self._pending_start = None
        self._log_handle = self._open_the_log_file()
        self._config = EnvConfig.from_env(Path.cwd())
        # ⛔ EVERYTHING THE BACKGROUND THREADS SAY COMES THROUGH HERE, AND
        # NOTHING ELSE. The reader thread of a server and its watchdog both
        # call back into this window, and both of them are threads that are
        # not the one that made the Tcl interpreter. Calling Tk from another
        # thread is not supported - Tcl keeps its state in one place and does
        # not lock it - and the symptom is a process that dies without a
        # traceback, which is the least useful kind of crash there is.
        #
        # With one server there was one such thread and the chance was small.
        # With two profiles running at once there are four, and the launcher
        # went. So the threads now POST to a queue and the Tk thread reads
        # it: `queue.Queue` is the one piece of shared state here that is
        # built to be shared.
        self._events: queue.Queue = queue.Queue()

        # ⛔ THE WORKING DIRECTORY IS WHERE THE `.env` IS READ, AND IT IS
        # REMEMBERED. The window said "Working directory" and the server was
        # started there - but the API key was read from wherever the window
        # happened to be OPENED, so launching the launcher from another folder
        # showed an empty key and refused to start. The two answers now come
        # from one place, and the place is remembered between runs, so the
        # launcher finds the project it was last used with.
        self._cwd = self._remembered_directory() or Path.cwd()
        self._config = EnvConfig.from_env(self._cwd)

        self._build_ui()
        self._load_into_form(self._config)
        self.after(100, self._pump_events)
        self.after(2000, self._refresh_handover)
        self.after(500, self._check_pending_start)
        self._warm_up()
        # Force the OS to commit the geometry we asked for. Tk can hold
        # the window back in the queue until mainloop runs, and the
        # window that is "supposed to be visible" without a forced
        # draw is the one a user reports as "nothing happens".
        self.update_idletasks()
        self.deiconify()
        self.lift()
        self.focus_force()
        self.attributes("-topmost", True)
        self.after(1000, lambda: self.attributes("-topmost", False))
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.report_callback_exception = self._report_callback_exception

    def _open_the_log_file(self):
        """Open the file the launcher's log is kept in, or None.

        It is in the package's own home - next to the conversations and the
        sessions - because that is where everything else this program writes
        lives, and because a person looking for it will look there.
        """
        try:
            from invisible_playwright_mcp import storage

            home = storage.home()
            home.mkdir(parents=True, exist_ok=True)
            return open(home / "launcher.log", "a", encoding="utf-8")
        except Exception:
            return None

    def _pump_events(self) -> None:
        """Take what the background threads said and act on it, HERE.

        This runs on the Tk thread, so everything below touches widgets
        safely.

        ⛔ AND THE DRAIN IS BOUNDED. An unbounded one - "take everything that
        is queued" - is a pump that never returns while the servers are
        louder than the window, and a pump that never returns is a Tk loop
        that never runs again: no timer, no click, nothing. The launcher
        would be a picture of a launcher. So a tick takes a fixed number of
        events and leaves the rest for the next one, and the queue is only
        ever a tick behind.
        """
        taken = 0
        try:
            while taken < self.EVENTS_PER_TICK:
                try:
                    kind, name, payload = self._events.get_nowait()
                except queue.Empty:
                    break
                taken += 1
                try:
                    if kind == "line":
                        self._log_line(payload)
                    elif kind == "exit":
                        self._refresh_status(payload, name)
                    elif kind == "restart":
                        self._server_restart_state(payload)
                    elif kind == "device":
                        self._on_device_checked(payload)
                except tk.TclError:
                    # The window is going away; there is nothing left to do.
                    return
                except Exception:
                    self._report_callback_exception(*sys.exc_info())
        finally:
            try:
                self.after(100, self._pump_events)
            except tk.TclError:
                # The window is gone; there is nothing left to poll for.
                pass

    #: How many events one tick of the pump takes. A hundred is more than a
    #: person can read and few enough that the Tk loop always gets a turn.
    EVENTS_PER_TICK = 100

    def _warm_up(self) -> None:
        """Pay the cold-start cost in the background, in other processes.

        ⛔ THE FIRST RUN OF dots ON A MACHINE READS EVERY FILE OF THE MODEL
        LIBRARY AND THE MCP FRAMEWORK FOR THE FIRST TIME, and on a machine
        with an antivirus on it that took FIFTY-FIVE SECONDS - measured here.
        The server says nothing for that whole minute, because the package's
        first line is printed after the import.

        It also CHECKS THE ENGINE, which is nineteen seconds of hashing a
        250 MB file - and the package does that in the interface and again in
        every server it spawns. Doing it here, once, means the servers find
        it done.

        ⛔ THREE PROCESSES, NOT ONE, BECAUSE THEY DO NOT DEPEND ON EACH
        OTHER. Sequentially the model library, the MCP framework and the
        engine were sixty-one seconds; in parallel it is the slowest of the
        three, which is twenty-five. And they are separate processes so that
        the launcher itself keeps no hundred megabytes of somebody else's
        modules - the point is the FILES being read, not the objects being
        kept.

        ⛔ ONCE PER PROCESS, AND NEVER MORE THAN ONE SET AT A TIME. A launcher
        that opened five windows would have five of these reading the same
        files at the same time, which is the one thing that made the machine
        slow, and the one thing that made the tests time out.
        """
        global _WARMED
        if _WARMED or os.environ.get("DOTS_WARMUP") == "0":
            return
        _WARMED = True
        jobs = (
            "import openai",
            "import mcp.server.fastmcp, invisible_playwright_mcp.mcp.server",
            "import dots.engine as e; e.prepared_path()",
        )
        self._warm_procs = []
        for job in jobs:
            try:
                self._warm_procs.append(subprocess.Popen(
                    [sys.executable, "-c", job],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=_NO_WINDOW))
            except OSError:
                # A warm-up that cannot start is not a reason to refuse to
                # open the window; the start is simply slower.
                pass

    def _warming(self) -> bool:
        """Whether the warm-up is still reading files.

        ⛔ AND THE SERVERS WAIT FOR IT. The warm-up and the servers want the
        same files, and the disk gives them to one reader at a time: measured
        here, a server started while the warm-up was still running took
        ninety seconds instead of twenty-three. So a Start pressed too early
        waits for the warm-up rather than competing with it, and says so.
        """
        for proc in self._warm_procs:
            if proc.poll() is None:
                return True
        return False

    def _on_form_resized(self, _event=None) -> None:
        """Keep the canvas's window the width of the canvas.

        Without this the inner frame keeps the width it was built at, and a
        window that is widened leaves a strip of nothing down the side.
        """
        try:
            self._form_canvas.itemconfigure(self._form_window, width=self._form_canvas.winfo_width())
        except tk.TclError:
            pass

    def _on_canvas_resized(self, _event=None) -> None:
        """The canvas is the width of the form, and the scroll region follows
        the content.

        ⛔ BOTH, AND THE WIDTH FIRST. The form is a window INSIDE the canvas,
        and a window keeps the width it was given - which, on the first layout,
        is the width the canvas had when the form was built, and that is one
        pixel. So the width is set here, when the canvas knows how wide it is,
        and the scroll region is set after it, from the frame that is now the
        right width.
        """
        try:
            self._form_canvas.itemconfigure(
                self._form_window, width=self._form_canvas.winfo_width())
            self._form_canvas.configure(scrollregion=self._form_canvas.bbox("all"))
        except tk.TclError:
            pass

    def _on_mousewheel(self, event) -> None:
        """Scroll the form with the wheel.

        Buttons 4 and 5 are the wheel on X11, where the wheel is not a
        separate event; on Windows it is `<MouseWheel>` with a delta in
        units of 120.
        """
        try:
            if getattr(event, "num", None) == 4:
                delta = -1
            elif getattr(event, "num", None) == 5:
                delta = 1
            else:
                delta = -1 if getattr(event, "delta", 0) > 0 else 1
            self._form_canvas.yview_scroll(delta, "units")
        except tk.TclError:
            pass

    def _report_callback_exception(self, exc, value, tb) -> None:
        """A failed click must not take the window with it.

        ⛔ TKLINTER ANSWERS AN EXCEPTION IN A CALLBACK BY PRINTING IT AND
        CARRYING ON, which is the right behaviour for a library and useless
        here: a launcher started from a shortcut has no console, so the
        traceback goes nowhere and the user is left with a window that did
        not do what they clicked.

        ⛔ AND NOTHING IN HERE MAY RAISE. This function is called FROM the
        exception handler, so an exception of its own - and the likeliest one
        is a TclError from touching a widget that has already gone - climbs
        all the way out of the callback, out of the event loop, and takes the
        process with it. That is how a launcher closes by itself: not the
        error it was asked to report, but the error while reporting it.

        So every line here is guarded, and the last one writes to a file
        rather than to a widget, because a file does not need a window.
        """
        text = "".join(traceback.format_exception(exc, value, tb))
        _diag_log(text)
        try:
            self._log_line("*** %s: %s" % (value.__class__.__name__, value))
        except Exception:
            pass
        try:
            self._status_var.set("error: %s" % value)
        except Exception:
            pass

    # -- UI construction ---------------------------------------------------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        # ⛔ THE FORM SCROLLS, AND THE BUTTONS DO NOT. The window grew past
        # the screen: a row here, a row there, and the button bar - the one
        # thing that has to be reachable - ended up below the bottom edge,
        # where a click finds nothing. Growing the window is not the answer
        # either: the content grows every time somebody adds a row, and the
        # screen is the size it is.
        #
        # So everything above the buttons lives on a canvas that scrolls, and
        # the buttons are packed on the window itself, below it, where they
        # always are.
        self._form_canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        self._form_scroll = ttk.Scrollbar(self, orient="vertical",
                                           command=self._form_canvas.yview)
        self._form_canvas.configure(yscrollcommand=self._form_scroll.set)
        # ⛔ THE SCROLLBAR IS PACKED FIRST, AND THE ORDER IS THE WHOLE OF IT.
        # `pack` hands out the space in the order it is called, and the canvas
        # is `fill="both"` - so a canvas packed first takes the full width and
        # the scrollbar is left with a strip of nothing to sit in. The
        # scrollbar takes its strip first, and the canvas takes what is left.
        self._form_scroll.pack(side="right", fill="y")
        self._form_canvas.pack(side="top", fill="both", expand=True)
        self._form = ttk.Frame(self._form_canvas)
        self._form_window = self._form_canvas.create_window(
            (0, 0), window=self._form, anchor="nw")
        self._form.bind("<Configure>", self._on_form_resized)
        self._form_canvas.bind("<Configure>", self._on_canvas_resized)
        for widget in (self._form_canvas, self._form):
            widget.bind("<MouseWheel>", self._on_mousewheel)
            widget.bind("<Button-4>", self._on_mousewheel)
            widget.bind("<Button-5>", self._on_mousewheel)

        top = ttk.Frame(self._form, padding=8)
        top.pack(fill="x")

        ttk.Label(top, text="Working directory:").grid(row=0, column=0,
                                                        sticky="w", **pad)
        self._cwd_var = tk.StringVar(value=str(self._cwd))
        ttk.Entry(top, textvariable=self._cwd_var).grid(
            row=0, column=1, sticky="ew", **pad)
        ttk.Button(top, text="Browse…", command=self._pick_cwd).grid(
            row=0, column=2, **pad)
        top.columnconfigure(1, weight=1)

        form = ttk.LabelFrame(self._form, text="Provider & model", padding=8)
        form.pack(fill="x", padx=8, pady=(0, 8))
        form.columnconfigure(1, weight=1)

        ttk.Label(form, text="Provider:").grid(row=0, column=0, sticky="w",
                                                **pad)
        self._provider_var = tk.StringVar()
        provider_combo = ttk.Combobox(
            form, textvariable=self._provider_var, state="readonly",
            values=[name for name, _ in PROVIDERS])
        provider_combo.grid(row=0, column=1, sticky="ew", **pad)
        provider_combo.bind("<<ComboboxSelected>>", self._on_provider_change)

        ttk.Label(form, text="API key:").grid(row=1, column=0, sticky="w",
                                               **pad)
        self._api_key_var = tk.StringVar()
        ttk.Entry(form, textvariable=self._api_key_var, show="*").grid(
            row=1, column=1, sticky="ew", **pad)

        ttk.Label(form, text="Base URL:").grid(row=2, column=0, sticky="w",
                                                **pad)
        self._base_url_var = tk.StringVar()
        ttk.Entry(form, textvariable=self._base_url_var).grid(
            row=2, column=1, sticky="ew", **pad)

        ttk.Label(form, text="Model:").grid(row=3, column=0, sticky="w",
                                             **pad)
        self._model_var = tk.StringVar()
        ttk.Entry(form, textvariable=self._model_var).grid(
            row=3, column=1, sticky="ew", **pad)

        browser = ttk.LabelFrame(self._form, text="Browser", padding=8)
        browser.pack(fill="x", padx=8, pady=(0, 8))
        # ⛔ ONE COLUMN TAKES THE SLACK, AND EVERY HINT LIVES IN IT. Grid sizes
        # a column to the WIDEST thing in it, and a hint is the widest thing in
        # a label column: the two grey explanations under the folder rows were
        # in column 0, so column 0 asked for 430px, and the fields - which are
        # in column 1 and do take the slack - were left four pixels wide. A
        # field that cannot be read is a field nobody can type a proxy URL
        # into. Every widget below therefore either sits in column 1 or starts
        # there and spans the rest; column 0 holds labels only.
        browser.columnconfigure(1, weight=1)

        # -- Profile ---------------------------------------------------------
        # First row, because a profile decides what the rows under it mean: the
        # proxy and seed below belong to whoever is picked here, and picking a
        # different one loads that one's. A user reading this window top to
        # bottom reads "who", then "through what exit", then "as what
        # fingerprint", which is the order the browser answers them in.
        ttk.Label(browser, text="Profile:").grid(row=0, column=0, sticky="w",
                                                 **pad)
        self._profile_var = tk.StringVar(value=NO_PROFILE)
        self._profile_combo = ttk.Combobox(browser, textvariable=self._profile_var,
                                          state="readonly", width=18)
        self._profile_combo.grid(row=0, column=1, sticky="ew", **pad)
        self._profile_combo.bind("<<ComboboxSelected>>",
                                 self._on_profile_pick)
        ttk.Button(browser, text="New…", width=7,
                   command=self._on_profile_new).grid(row=0, column=2, **pad)
        ttk.Button(browser, text="Save to profile", width=15,
                   command=self._on_profile_save).grid(row=0, column=3, **pad)
        ttk.Button(browser, text="Delete", width=7,
                   command=self._on_profile_delete).grid(row=0, column=4, **pad)

        ttk.Label(browser, text="Port:").grid(row=1, column=0, sticky="w",
                                               **pad)
        self._port_var = tk.IntVar(value=8765)
        ttk.Spinbox(browser, from_=1, to=65535, textvariable=self._port_var,
                    width=8).grid(row=1, column=1, sticky="w", **pad)

        ttk.Label(browser, text="Proxy:").grid(row=2, column=0, sticky="w",
                                                **pad)
        self._proxy_var = tk.StringVar()
        ttk.Entry(browser, textvariable=self._proxy_var).grid(
            row=2, column=1, sticky="ew", **pad)

        ttk.Label(browser, text="Seed:").grid(row=3, column=0, sticky="w",
                                                **pad)
        self._seed_var = tk.StringVar()
        ttk.Entry(browser, textvariable=self._seed_var, width=14).grid(
            row=3, column=1, sticky="w", **pad)

        # The device the seed draws, with the profile's own pins on it. It sits
        # under the seed because it is the other half of the same answer: the
        # seed says which person, the pins say which screen and which ratio
        # that person has.
        ttk.Label(browser, text="Device:").grid(row=4, column=0, sticky="w",
                                                 **pad)
        self._device_var = tk.StringVar()
        ttk.Entry(browser, textvariable=self._device_var).grid(
            row=4, column=1, sticky="ew", **pad)
        self._device_check_btn = ttk.Button(browser, text="Check", width=8,
                                            command=self._on_check_device)
        self._device_check_btn.grid(row=4, column=2, sticky="w", **pad)
        self._device_hint_var = tk.StringVar()
        ttk.Label(browser, textvariable=self._device_hint_var,
                  foreground="grey").grid(row=5, column=1, columnspan=4,
                                          sticky="w", **pad)

        ttk.Label(browser, text="Browser data:").grid(
            row=6, column=0, sticky="w", **pad)
        self._profile_dir_var = tk.StringVar()
        row4 = ttk.Frame(browser)
        row4.grid(row=6, column=1, sticky="ew", **pad)
        row4.columnconfigure(0, weight=1)
        ttk.Entry(row4, textvariable=self._profile_dir_var).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(row4, text="Browse…",
                   command=self._pick_profile_dir).grid(row=0, column=1,
                                                        padx=(4, 0))
        ttk.Label(browser, text="the browser's own - cookies, logins, the "
                        "fingerprint. Do not put your files in it.",
                  foreground="grey").grid(row=7, column=1, columnspan=4,
                                          sticky="w", **pad)

        ttk.Label(browser, text="Working folder:").grid(
            row=8, column=0, sticky="w", **pad)
        self._workdir_var = tk.StringVar()
        row5 = ttk.Frame(browser)
        row5.grid(row=8, column=1, sticky="ew", **pad)
        row5.columnconfigure(0, weight=1)
        ttk.Entry(row5, textvariable=self._workdir_var).grid(
            row=0, column=0, sticky="ew")
        ttk.Button(row5, text="Browse…", command=self._pick_workdir).grid(
            row=0, column=1, padx=(4, 0))
        ttk.Label(browser, text="yours - where the agent puts the files it "
                        "produces for this profile.",
                  foreground="grey").grid(row=9, column=1, columnspan=4,
                                          sticky="w", **pad)

        self._headed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(browser, text="Run browser in a visible window "
                        "(--headed)", variable=self._headed_var).grid(
            row=10, column=0, columnspan=5, sticky="w", **pad)

        # -- Authorizations ---------------------------------------------------
        # The logins the agent may type for THIS profile, and the only place
        # they are ever typed by hand. A password column is deliberately
        # absent: `auths.describe_rows` is written for a person to read, and a
        # table full of the real ones would end up in a screenshot of this
        # window. Selecting a row fills the fields, and the password field is
        # empty until it is needed - it is a form, not a viewer.
        vault_frame = ttk.LabelFrame(self._form,
                                     text="Authorizations for this profile",
                                     padding=8)
        vault_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        vault_frame.columnconfigure(0, weight=1)

        self._auths_hint = ttk.Label(
            vault_frame, foreground="grey",
            text="Pick a profile above to see the logins the agent may use "
                 "for it.")
        self._auths_hint.grid(row=0, column=0, columnspan=5, sticky="w", **pad)

        self._auths_tree = ttk.Treeview(vault_frame, columns=("site", "login"),
                                        show="headings", height=6,
                                        selectmode="browse")
        self._auths_tree.heading("site", text="Site")
        self._auths_tree.heading("login", text="Login")
        self._auths_tree.column("site", width=320, anchor="w")
        self._auths_tree.column("login", width=220, anchor="w")
        self._auths_tree.grid(row=1, column=0, columnspan=5, sticky="nsew",
                              **pad)
        self._auths_tree.bind("<<TreeviewSelect>>", self._on_auth_pick)

        self._auth_site_var = tk.StringVar()
        self._auth_login_var = tk.StringVar()
        self._auth_password_var = tk.StringVar()
        self._auth_notes_var = tk.StringVar()

        form_row = ttk.Frame(vault_frame)
        form_row.grid(row=2, column=0, columnspan=5, sticky="ew", **pad)
        form_row.columnconfigure(1, weight=1)
        ttk.Label(form_row, text="Site:").grid(row=0, column=0, sticky="w")
        ttk.Entry(form_row, textvariable=self._auth_site_var,
                  width=34).grid(row=0, column=1, sticky="ew", padx=(4, 12))
        ttk.Label(form_row, text="Login:").grid(row=0, column=2, sticky="w")
        ttk.Entry(form_row, textvariable=self._auth_login_var,
                  width=24).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        form_row2 = ttk.Frame(vault_frame)
        form_row2.grid(row=3, column=0, columnspan=5, sticky="ew", **pad)
        form_row2.columnconfigure(1, weight=1)
        ttk.Label(form_row2, text="Password:").grid(row=0, column=0, sticky="w")
        ttk.Entry(form_row2, textvariable=self._auth_password_var,
                  show="*", width=34).grid(row=0, column=1, sticky="ew",
                                           padx=(4, 12))
        ttk.Label(form_row2, text="Note:").grid(row=0, column=2, sticky="w")
        ttk.Entry(form_row2, textvariable=self._auth_notes_var,
                  width=24).grid(row=0, column=3, sticky="ew", padx=(4, 0))

        buttons = ttk.Frame(vault_frame)
        buttons.grid(row=4, column=0, columnspan=5, sticky="w", **pad)
        ttk.Button(buttons, text="Save / replace",
                   command=self._on_auth_save).pack(side="left")
        ttk.Button(buttons, text="Remove",
                   command=self._on_auth_remove).pack(side="left", padx=(8, 0))
        ttk.Button(buttons, text="Clear",
                   command=self._on_auth_clear).pack(side="left", padx=(8, 0))

        # -- Buttons and status -------------------------------------------

        bar = ttk.Frame(self, padding=8)
        bar.pack(fill="x")
        self._save_btn = ttk.Button(bar, text="Save .env",
                                    command=self._on_save)
        self._save_btn.pack(side="left")
        self._start_btn = ttk.Button(bar, text="Start server",
                                     command=self._on_start)
        self._start_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Start all profiles",
                   command=self._on_start_all).pack(side="left", padx=(6, 0))
        self._stop_btn = ttk.Button(bar, text="Stop server",
                                    command=self._on_stop, state="disabled")
        self._stop_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Stop all",
                   command=self._on_stop_all).pack(side="left", padx=(6, 0))
        self._open_btn = ttk.Button(bar, text="Open in browser",
                                    command=self._on_open_browser,
                                    state="disabled")
        self._open_btn.pack(side="left", padx=(8, 0))
        ttk.Button(bar, text="Clear log",
                   command=self._on_clear_log).pack(side="right")

        self._status_var = tk.StringVar(value="stopped")
        ttk.Label(bar, textvariable=self._status_var,
                  foreground="grey").pack(side="right", padx=(0, 8))
        if self._log_handle is not None:
            # The file is the log; the pane is a view of it. Saying where it
            # is means a person who has just seen the window vanish knows
            # where the words went - and the NAME is enough, because the full
            # path is in the tooltip and a path in a button bar is a path that
            # pushes the buttons off the end.
            where = self._log_handle.name
            label = ttk.Label(bar, text="log: %s" % Path(where).name,
                              foreground="grey")
            label.pack(side="right", padx=(0, 8))
            label.bind("<Enter>", lambda _e, w=where: self._show_the_path(w))
            label.bind("<Leave>", lambda _e: self._hide_the_path())

        # The banner and the log, below the bar.
        self._build_the_rest()

    def _show_the_path(self, where: str) -> None:
        """Say where the log is, on the status line, while the pointer is on it."""
        try:
            self._status_var.set("log: %s" % where)
        except tk.TclError:
            pass

    def _hide_the_path(self) -> None:
        try:
            self._refresh_status()
        except tk.TclError:
            pass

    def _build_the_rest(self) -> None:
        """The banner and the log, which live below the button bar.

        ⛔ THEY ARE BUILT IN THEIR OWN METHOD ON PURPOSE. They were once the
        tail of `_build_ui`, and a method that ends in the middle of somebody
        else's method is a method that silently stops: the banner and the log
        were never made, and nothing said so, because nothing was raised.
        """
        # -- The handback banner ---------------------------------------------
        # Sits above the log because that is the one moment the launcher has
        # nothing else to do: the agent has stopped and is waiting for the
        # person, and the question it stopped on is on file. Polled rather
        # than pushed, because the write happens in another process - the
        # browser server - and there is no channel from there to here.
        self._handover_var = tk.StringVar(value="")
        self._handover = ttk.Label(self._form, textvariable=self._handover_var,
                                   foreground="#b34700", padding=(8, 6),
                                   wraplength=880, justify="left")
        self._handover.pack(fill="x", padx=8, pady=(0, 6))

        log_frame = ttk.LabelFrame(self._form, text="Log", padding=4)
        log_frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)

        self._log = tk.Text(log_frame, height=16, wrap="word",
                            state="disabled", font=("Consolas", 9))
        self._log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, orient="vertical",
                               command=self._log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self._log.configure(yscrollcommand=scroll.set)

        # Every text field gets a right-click menu, and it is done LAST, over
        # the widget tree rather than over a list of names - so a field added
        # later is covered without anybody remembering to add it.
        self._install_context_menus()

    # -- the context menu ----------------------------------------------------

    def _install_context_menus(self) -> None:
        """Put a cut/copy/paste menu on every text field in the window.

        ⛔ TK HAS NO CONTEXT MENU OF ITS OWN. A right-click in an Entry does
        nothing at all - there is no menu to open - and that is the whole
        reason "paste does not work here" is the usual report about a tkinter
        window. The keyboard shortcut does work; the menu a person reaches for
        when they see a text field simply is not there.

        So it is built here. Cut, Copy, Paste and Select all, the four things
        a text field has, and nothing else: a menu with a dozen entries is a
        menu nobody reads.

        ⛔ AND THE WALK STARTS AT THE FORM, NOT AT THE WINDOW. The form
        lives on a canvas, and a canvas's children are its ITEMS, not
        widgets - so a walk from the window finds the canvas and stops, and
        every field in the window is left without a menu.
        """
        for widget in self._text_fields(self._form):
            self._menu_for(widget)

    def _text_fields(self, widget=None):
        """Every Entry in the tree under `widget` - the form, by default."""
        out = []
        self._text_fields_into(self._form if widget is None else widget, out)
        return out

    def _text_fields_into(self, widget, out):
        for child in widget.winfo_children():
            if child.winfo_class() in ("TEntry", "Entry"):
                out.append(child)
            self._text_fields_into(child, out)

    def _menu_for(self, widget) -> None:
        """One field's menu, and the click that opens it.

        ⛔ PASTE IS GREYED OUT WHEN THE CLIPBOARD HOLDS NOTHING THAT CAN BE
        PASTED. A menu that offers an action which silently does nothing is
        worse than a menu that does not offer it: the person clicks Paste,
        nothing appears, and the field is blamed for it.
        """
        menu = tk.Menu(widget, tearoff=0)
        menu.add_command(label="Cut",
                         command=lambda: widget.event_generate("<<Cut>>"))
        menu.add_command(label="Copy",
                         command=lambda: widget.event_generate("<<Copy>>"))
        menu.add_command(label="Paste",
                         command=lambda: widget.event_generate("<<Paste>>"))
        menu.add_separator()
        menu.add_command(label="Select all",
                         command=lambda: widget.event_generate("<<SelectAll>>"))

        def popup(event):
            try:
                widget.clipboard_get()
                can_paste = True
            except tk.TclError:
                can_paste = False
            menu.entryconfigure("Paste",
                                state="normal" if can_paste else "disabled")
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                # Without this the menu keeps the mouse: the click that was
                # meant for the field behind it goes to the menu instead.
                menu.grab_release()

        # Button 3 is the right button on Windows and on X11; on macOS it is
        # Button 2, because the CONTROL button is the one that opens menus.
        widget.bind("<Button-3>" if sys.platform != "darwin" else "<Button-2>",
                    popup)

    # -- Form ↔ config ----------------------------------------------------

    def _load_into_form(self, config: EnvConfig) -> None:
        """Copy a config onto the form, with the provider chosen from the URL."""
        self._provider_var.set(config.provider_name())
        self._api_key_var.set(config.api_key)
        self._base_url_var.set(config.base_url)
        self._model_var.set(config.model)
        self._proxy_var.set(config.proxy)
        self._seed_var.set(config.seed)
        self._device_var.set("")
        self._profile_dir_var.set(config.profile_dir)
        self._port_var.set(config.port)
        self._headed_var.set(config.headed)
        self._refresh_profiles(config.profile_name)
        if config.profile_name:
            profile = registry.find(config.profile_name)
            self._workdir_var.set(str(profile.work()) if profile else "")
        else:
            self._workdir_var.set("")
        self._refresh_device_hint()
        # The table belongs to the profile too, and a window that opens with
        # "pick a profile above" while one is picked is a window telling the
        # user the wrong thing about itself.
        self._refresh_auths()

    def _config_from_form(self) -> EnvConfig:
        """The config the form is currently showing, with the typed values."""
        return EnvConfig(
            api_key=self._api_key_var.get().strip(),
            base_url=self._base_url_var.get().strip(),
            model=self._model_var.get().strip(),
            proxy=self._proxy_var.get().strip(),
            seed=self._seed_var.get().strip(),
            profile_dir=self._profile_dir_var.get().strip(),
            profile_name=self._chosen_profile(),
            port=int(self._port_var.get()),
            headed=bool(self._headed_var.get()),
        )

    # -- profiles -------------------------------------------------------------

    def _chosen_profile(self) -> str:
        """The profile this window is pointed at, or an empty string.

        The dropdown's value is a name or the `NO_PROFILE` label; the label is
        not a name and must never become one, so it is translated here rather
        than being written into `.env` and read back as a profile called
        "(no profile)".
        """
        chosen = self._profile_var.get().strip()
        return "" if chosen in (NO_PROFILE, "") else chosen

    def _refresh_profiles(self, select: Optional[str] = None) -> None:
        """Repopulate the dropdown from the registry, keeping the selection.

        Called after every add, delete and Save, and once at startup. A name
        in the selection that the registry does not have is dropped to
        `NO_PROFILE` rather than left in the box: a dropdown offering a profile
        that is not there is how somebody ends up wondering why Start refuses.

        ⛔ AND THE TABLE FOLLOWS THE LIST, ALWAYS. The list and the table are
        two answers to one question - "whose logins is this?" - and they were
        refreshed in different places, so a name that the registry had lost
        left the table showing one profile's logins under another profile's
        name. One call now answers both, and nothing else may refresh either
        on its own.
        """
        held = [p.name for p in registry.list_profiles()]
        self._profile_combo.configure(values=[NO_PROFILE, *held])
        wanted = select if select is not None else self._chosen_profile()
        self._profile_var.set(wanted if wanted in held else NO_PROFILE)
        self._refresh_auths()

    def _on_profile_pick(self, _event: object = None) -> None:
        """Load the chosen profile's settings onto the form.

        The fields under the dropdown are that profile's, so picking one shows
        its exit, its seed and its directories - and picking another shows
        that one's instead of leaving the first one's on screen where the next
        Save would write them over the wrong profile. Changes the user makes
        afterwards are not saved until `Save to profile`: the form is the
        source of truth while it is open, the same rule `.env` follows.
        """
        name = self._chosen_profile()
        if not name:
            self._workdir_var.set("")
            self._device_var.set("")
            self._refresh_device_hint()
            self._refresh_auths()
            return
        try:
            profile = registry.get(name)
        except registry.ProfileError as exc:
            messagebox.showerror("Could not open the profile", str(exc))
            # The list is emptied here, and the table with it: a name the
            # registry has lost must not leave one profile's logins on screen
            # under another profile's name.
            self._refresh_profiles(NO_PROFILE)
            self._refresh_device_hint()
            return
        self._proxy_var.set(profile.proxy)
        # The seed the browser will be opened with, which is the one the
        # browser directory remembers if it remembers one: the directory is
        # where the cookies are, and the pinned package refuses a profile
        # that asks to be somebody else. Showing the registry's own value
        # would put a number in the box that the browser will not be.
        seed = profile.effective_seed()
        self._seed_var.set("" if seed is None else str(seed))
        self._device_var.set(geometry.format_pin(profile.pin))
        self._profile_dir_var.set(str(profile.path()))
        self._workdir_var.set(str(profile.work()))
        # ⛔ THE PORT COMES WITH THE PROFILE. Two profiles running at once need
        # two ports, and the port is the one setting that decides whether the
        # second one can even start: it would fail to bind and say so, and the
        # first would be the one that works. So picking a profile picks its
        # port, and the field shows the profile's rather than the last one's.
        if profile.port:
            self._port_var.set(profile.port)
        self._refresh_device_hint()
        self._refresh_auths()

    def _on_profile_new(self) -> None:
        """Make a profile and put the form on it.

        The name is asked for rather than generated: a profile is something a
        person recognises in a dropdown six months later, and `work-2` is not.
        A refusal to take the name is shown in place, not in a dialog that
        could belong to any of the three buttons in that row.
        """
        asked = simpledialog.askstring("New profile", "Name for this profile:",
                                       parent=self)
        if asked is None:
            return
        try:
            registry.create(asked)
        except registry.ProfileError as exc:
            messagebox.showerror("Could not create the profile", str(exc))
            return
        self._refresh_profiles(registry.check_name(asked))
        self._on_profile_pick()
        self._log_line("created profile %s" % registry.check_name(asked))

    def _on_profile_save(self) -> None:
        """Write the browser fields on the form into the chosen profile.

        A profile that has no exit yet, and a form with the exit field left
        empty, clear it - because "the exit I do not use" is the one value an
        empty box is unambiguous about. Everything else is left alone: the
        working folder and the logins are not on this form, and a Save here
        must not be a reason to lose them.
        """
        name = self._chosen_profile()
        if not name:
            messagebox.showinfo("No profile chosen",
                                "Pick a profile from the list first, or use "
                                "New… to make one.")
            return
        seed = self._seed_var.get().strip()
        if seed:
            try:
                seed_value: Optional[int] = int(seed)
            except ValueError:
                messagebox.showerror("Cannot save the profile",
                                     "Seed must be a whole number, or empty "
                                     "to let the profile keep its own.")
                return
        else:
            seed_value = None
        try:
            pin = geometry.parse_pin([self._device_var.get()])
        except geometry.GeometryError as exc:
            messagebox.showerror("Cannot save the profile", str(exc))
            return
        try:
            profile = registry.update(
                name,
                proxy=self._proxy_var.get().strip(),
                seed=seed_value,
                pin=pin,
                directory=self._profile_dir_var.get().strip(),
                workdir=self._workdir_var.get().strip(),
                port=int(self._port_var.get()),
            )
        except (registry.ProfileError, OSError) as exc:
            messagebox.showerror("Cannot save the profile", str(exc))
            return
        # Written back so the box shows what was stored: a directory typed as
        # a relative path is an absolute path once resolved, and the next Save
        # should not resolve it differently.
        self._profile_dir_var.set(str(profile.path()))
        self._workdir_var.set(str(profile.work()))
        self._refresh_device_hint()
        self._log_line("saved profile %s: %s" % (name, profile.path()))

    def _refresh_device_hint(self) -> None:
        """What the profile on the form claims, in one line under the field.

        Two things are said here that the field itself cannot: what the pins
        and the seed add up to, and the fact that the seed the browser will
        be opened with is not the one the registry holds.
        """
        name = self._chosen_profile()
        if not name:
            self._device_hint_var.set(
                "the screen and ratio this browser claims, for example "
                "screen.width=1920, screen.dpr=1.25. Empty is whatever the "
                "seed draws.")
            return
        profile = registry.find(name)
        if profile is None:
            self._device_hint_var.set("this profile is not in the registry.")
            return
        self._device_hint_var.set(profile.device_line())

    def _on_check_device(self) -> None:
        """Ask the running server what the browser says it is.

        ⛔ THE BROWSER IS IN ANOTHER PROCESS, so the question goes there over
        HTTP and the answer comes back the same way. A thread does the
        asking, because a blocking read in the Tk thread is a window that
        stops answering, and the answer is posted back to this thread the
        way every other thing from a thread arrives here.
        """
        launcher = self._launcher_for(self._chosen_profile())
        try:
            port = launcher._last_port or int(self._port_var.get())
        except (TypeError, ValueError):
            self._log_line("device: the port is not a number.")
            return
        url = "http://127.0.0.1:%d/live/device" % port
        self._device_check_btn.configure(state="disabled")
        threading.Thread(target=self._ask_the_server, args=(url,),
                         daemon=True).start()

    def _ask_the_server(self, url: str) -> None:
        """One question to the server, answered on a thread of its own."""
        import json
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=20) as answer:
                body = json.loads(answer.read().decode("utf-8"))
            said = body.get("report") or body.get("error") or "the server said nothing"
        except Exception as exc:
            said = ("could not ask the server at %s: %s - is it running?"
                    % (url, exc))
        self._post("device", "", said)

    def _on_device_checked(self, said: str) -> None:
        """What the server answered, in the log and under the field."""
        self._device_check_btn.configure(state="normal")
        self._log_line("device: %s" % said)
        self._device_hint_var.set(said)

    def _on_profile_delete(self) -> None:
        """Forget a profile, asking first, and never deleting files by accident.

        The question names the two things it could do and makes the destructive
        one a separate button: `remove` without `--wipe` leaves a browser
        directory holding real logins exactly where it was.
        """
        name = self._chosen_profile()
        if not name:
            return
        answer = messagebox.askyesno(
            "Remove profile",
            "Remove profile %r from the list?\n\n"
            "Its browser directory and its saved logins stay on disk. "
            "Nothing is deleted unless you remove the files yourself."
            % name, parent=self)
        if not answer:
            return
        try:
            registry.delete(name)
        except registry.ProfileError as exc:
            messagebox.showerror("Could not remove the profile", str(exc))
            return
        self._refresh_profiles(NO_PROFILE)
        self._refresh_auths()
        self._log_line("removed profile %s" % name)

    # -- the handback ---------------------------------------------------------

    #: How often the banner looks at the file. Two seconds is quick enough that
    #: the question is on screen while the person is still reading the chat,
    #: and slow enough that a launcher open all day costs nothing.
    HANDOVER_POLL_MS = 2000

    def _refresh_handover(self) -> None:
        """Show the question the agent is waiting on, while it is outstanding.

        Polled rather than pushed, and rescheduled from here: the write happens
        in the browser server process and there is no channel from there to
        here, so this file is the only place the two meet.

        Once, not every poll - a line that reappeared every two seconds would
        fill the log with the same sentence and teach the user to ignore it.
        """
        try:
            name = self._chosen_profile()
            record = handover.current(name) if name else None
            text = ""
            if record:
                text = ("The agent is waiting for you. %s" % record["question"])
                if record.get("need"):
                    text += " It needs %s." % record["need"]
                if record.get("page"):
                    text += " On %s." % record["page"]
                text += (" Answer in the chat, or type it into the browser "
                         "window if it is open.")
            if self._handover_var.get() != text:
                self._handover_var.set(text)
                if text:
                    self._log_line("*** " + text)
            self.after(self.HANDOVER_POLL_MS, self._refresh_handover)
        except tk.TclError:
            # The window is gone, which is the only reason this can fail: it
            # ends the poll rather than raising into a closed interpreter.
            pass

    def _on_answered(self) -> None:
        """The person has replied in the chat, so the question is answered.

        Called from nothing yet - the chat lives in the web UI and this window
        does not see it - and kept because the one place it belongs is a
        callback wired to the server's output, which `dots-gui` does not read.
        The poll finds the clear by itself; this is what a caller with better
        knowledge of the moment would call.
        """
        name = self._chosen_profile()
        if name:
            handover.clear(name)
        self._handover_var.set("")
        self._log_line("[the agent is no longer waiting]")

    # -- authorizations -------------------------------------------------------

    def _refresh_auths(self) -> None:
        """Refill the table from the chosen profile's file.

        Only sites and logins, never a password: this table is the thing
        somebody screenshots when they ask for help, and `vault.describe_rows`
        is written for exactly that.
        """
        self._auths_tree.delete(*self._auths_tree.get_children())
        name = self._chosen_profile()
        self._auths_hint.configure(
            text="Logins for profile %r. The agent may type these into a form "
                 "when a site asks it to sign in." % name if name
            else "Pick a profile above to see the logins the agent may use "
                 "for it.")
        if not name:
            return
        try:
            held = vault.load(name)
        except OSError as exc:
            self._log_line("could not read the logins of %s: %s" % (name, exc))
            return
        for record in held:
            self._auths_tree.insert("", "end", values=(record.site,
                                                       record.login or ""))

    def _on_auth_pick(self, _event: object = None) -> None:
        """Fill the form from the selected row.

        The password field is left empty on purpose. It is not in the table,
        so the window does not become a place a password sits in plain text
        for as long as it is open - and `Save / replace` with an empty
        password box would silently blank the stored one, which is why the
        Remove button is a separate thing and why the hint is in the label.
        """
        chosen = self._auths_tree.selection()
        if not chosen:
            return
        values = self._auths_tree.item(chosen[0], "values")
        self._auth_site_var.set(values[0] if values else "")
        self._auth_login_var.set(values[1] if len(values or ()) > 1 else "")
        self._auth_password_var.set("")
        self._auth_notes_var.set("")

    def _on_auth_save(self) -> None:
        """Save or replace one login for the chosen profile.

        An empty password box on a login that already exists keeps the stored
        password. That is the other half of why the field is not filled in when
        a row is selected: `Save / replace` is a button people press after
        typing a login, and a press that silently blanked the password would be
        the worst possible reading of it.
        """
        name = self._chosen_profile()
        if not name:
            messagebox.showinfo("No profile chosen",
                                "Logins belong to a profile. Pick one from "
                                "the list, or use New… to make one.")
            return
        site = self._auth_site_var.get().strip()
        if not site:
            messagebox.showerror("Cannot save", "A login needs a site: a host "
                                "like example.com, or any URL on it.")
            return
        held = vault.find(name, site)
        password = self._auth_password_var.get()
        if not password:
            if held is None:
                messagebox.showerror(
                    "Cannot save",
                    "There is no saved login for that site yet, so a "
                    "password is needed. Leaving the field empty here would "
                    "save nothing.")
                return
            password = held.password
        try:
            record = vault.add(name, site, self._auth_login_var.get().strip(),
                               password, notes=self._auth_notes_var.get().strip())
        except (vault.AuthError, registry.ProfileError, OSError) as exc:
            messagebox.showerror("Cannot save the login", str(exc))
            return
        self._refresh_auths()
        self._auth_password_var.set("")
        self._log_line("saved the login for %s in profile %s"
                       % (record.site, name))

    def _on_auth_remove(self) -> None:
        """Forget one login, after asking."""
        name = self._chosen_profile()
        site = self._auth_site_var.get().strip()
        if not name or not site:
            return
        if not messagebox.askyesno("Forget login",
                                   "Forget the saved login for %s in profile "
                                   "%r?" % (site, name), parent=self):
            return
        try:
            gone = vault.remove(name, site)
        except (vault.AuthError, OSError) as exc:
            messagebox.showerror("Cannot remove the login", str(exc))
            return
        self._refresh_auths()
        self._on_auth_clear()
        self._log_line("forgot the login for %s in profile %s"
                       % (gone.site, name))

    def _on_auth_clear(self) -> None:
        """Empty the login form and drop the table selection."""
        self._auth_site_var.set("")
        self._auth_login_var.set("")
        self._auth_password_var.set("")
        self._auth_notes_var.set("")
        self._auths_tree.selection_remove(self._auths_tree.selection())

    # -- Callbacks --------------------------------------------------------

    def _on_provider_change(self, _event: object = None) -> None:
        """Refill the base-URL field when the dropdown moves.

        Switching away from `Custom` overwrites a free-form URL the user
        typed, which is the right behaviour: they picked a known provider,
        they meant that provider's URL. Switching TO `Custom` leaves the
        field alone, because that is what the user came to `Custom` for.
        """
        provider = self._provider_var.get()
        for name, url in PROVIDERS:
            if name == provider:
                if url:
                    self._base_url_var.set(url)
                if not self._model_var.get().strip():
                    suggestion = self._config.model_suggestion(provider)
                    if suggestion:
                        self._model_var.set(suggestion)
                break

    def _remembered_directory(self) -> Optional[Path]:
        """The directory this launcher was last used in, if it is still there.

        ⛔ THE LAUNCHER IS OPENED FROM EVERYWHERE, AND THE PROJECT IS IN ONE
        PLACE. A launcher opened from the desktop has no project in front of
        it, and without this it would show an empty API key and refuse to
        start - which reads as "the program is broken" and is not.
        """
        try:
            from invisible_playwright_mcp import storage

            blob = storage.read_json(storage.home() / "launcher.json")
            if not isinstance(blob, dict):
                return None
            said = str(blob.get("cwd") or "").strip()
            if not said:
                return None
            path = Path(said)
            return path if path.is_dir() else None
        except Exception:
            return None

    def _remember_directory(self, directory: Path) -> None:
        """Write down where this launcher was last used."""
        try:
            from invisible_playwright_mcp import storage

            home = storage.home()
            home.mkdir(parents=True, exist_ok=True)
            storage.write_atomically(home / "launcher.json",
                                    json.dumps({"cwd": str(directory)},
                                               indent=2).encode("utf-8"))
        except Exception:
            # A launcher that cannot remember is a launcher that asks again.
            pass

    def _pick_cwd(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self._cwd_var.get() or str(Path.cwd()),
            title="Pick the directory for .env and the server")
        if chosen:
            self._cwd_var.set(chosen)
            self._cwd = Path(chosen)
            self._config = EnvConfig.from_env(self._cwd)
            self._load_into_form(self._config)
            self._remember_directory(self._cwd)

    def _pick_profile_dir(self) -> None:
        chosen = self._ask_directory(
            self._profile_dir_var.get(),
            "Pick the folder the browser keeps its data in")
        if chosen:
            self._profile_dir_var.set(chosen)

    def _pick_workdir(self) -> None:
        chosen = self._ask_directory(
            self._workdir_var.get(),
            "Pick the folder the agent works in for this profile")
        if chosen:
            self._workdir_var.set(chosen)

    def _start_in(self, typed: str) -> str:
        """A directory the folder dialog can actually open at.

        A path in the field that has since been moved or deleted is not a
        reason to refuse the dialog: Tk answers a missing `-initialdir` with an
        error the user can do nothing about, and the folder they are about to
        choose is the point of opening it. Home is the fallback.
        """
        candidate = (typed or "").strip()
        if candidate:
            try:
                path = Path(candidate).expanduser()
            except (OSError, ValueError):
                return str(Path.home())
            if path.is_dir():
                return str(path)
        return str(Path.home())

    def _ask_directory(self, typed: str, title: str) -> str:
        """The folder chooser, opened IN FRONT OF THIS WINDOW.

        ⛔ THE WINDOW IS UN-PINNED FIRST AND RE-PINNED AFTER. `__init__`
        raises the launcher `-topmost` for its first second so it comes up over
        whatever was in front of it, and the native folder dialog inherits that
        pin: it opens BEHIND the window it was opened from, which reads as a
        launcher that stopped responding to clicks. Pin down, ask, put it
        back - and put back what it was, rather than a value chosen here,
        because the pin is the window's business and not this dialog's.
        """
        try:
            pinned = bool(self.attributes("-topmost"))
        except tk.TclError:
            pinned = False
        if pinned:
            self.attributes("-topmost", False)
        try:
            return filedialog.askdirectory(initialdir=self._start_in(typed),
                                           title=title)
        finally:
            if pinned:
                self.attributes("-topmost", True)

    def _on_save(self) -> None:
        """Write the form to `.env` in the working directory.

        The browser fields are not written twice: a profile owns its own exit,
        seed and directories, and copying them into `.env` as well would leave
        two copies to disagree - with the copy in `.env` the one that wins on
        the next run, and the profile quietly not being what the user saved.
        `Save to profile` is the button that puts them in the profile.
        """
        try:
            config = self._config_from_form()
            directory = Path(self._cwd_var.get())
            directory.mkdir(parents=True, exist_ok=True)
            write_env(directory, config)
        except (OSError, ValueError) as e:
            messagebox.showerror("Could not save .env", str(e))
            return
        # And the launcher remembers where it was used, so that opening it
        # from anywhere else finds the same project and the same key.
        self._cwd = directory
        self._remember_directory(directory)
        self._log_line("saved .env to %s" % (directory / ENV_FILENAME))

    def _check_pending_start(self) -> None:
        """Start what was asked for, once the warm-up has let go of the disk.

        A Start pressed while the warm-up is still reading is not dropped and
        not run at once: it waits, and the log says so. The window stays
        alive throughout - the wait is a poll on this thread, not a sleep in
        it.
        """
        try:
            if self._pending_start is not None and not self._warming():
                pending, self._pending_start = self._pending_start, None
                pending()
            self.after(500, self._check_pending_start)
        except tk.TclError:
            # The window is gone; there is nothing left to start.
            pass

    def _defer_until_warm(self, action, what: str) -> None:
        """Run `action` when the warm-up is done, and say that it will.

        ⛔ AND THE WAIT IS BOUNDED. A warm-up that never finishes - a child
        that hangs on a file - must not leave the person with a button that
        does nothing for ever. After a minute the start goes ahead anyway:
        slow is better than stuck, and the log says which it was.
        """
        self._pending_start = action
        self._log_line("warming up the browser libraries, %s starts in a "
                        "moment..." % what)

    def _on_start(self) -> None:
        """Save `.env` first, then spawn the server in the working directory.

        Saves first so the server always reads what the form currently shows:
        the alternative - "start with whatever's on disk" - would have the
        server disagreeing with the form when the user forgot to click Save,
        and the next Save would re-spin a server already running.

        A preflight check catches the obvious failure modes before the
        subprocess is even started: an empty key, a malformed URL, a
        port already in use. Each one points the user at the field that
        needs fixing, instead of letting the server crash a second later
        with a stack trace in the log.

        The server that starts is the CHOSEN profile's, on that profile's
        port - which is what makes two of them runnable at once.
        """
        name = self._chosen_profile()
        launcher = self._launcher_for(name)
        if launcher.running:
            return
        self._on_save()
        config = self._config_from_form()
        problem = self._preflight(config)
        if problem is not None:
            messagebox.showerror("Cannot start dots", problem)
            return
        extra_args: list[str] = []
        if config.proxy:
            extra_args.extend(["--proxy", config.proxy])
        if config.seed:
            extra_args.extend(["--seed", config.seed])
        if config.profile_dir:
            extra_args.extend(["--profile-dir", config.profile_dir])
        if config.profile_name:
            # ⛔ LAST, AND THEREFORE NOT DECIDING. `--profile-dir` is above it
            # on purpose: a profile name resolves to a directory in
            # `dots.cli`, and a directory typed into the form by hand is the
            # more specific of the two. Order here and order there are the
            # same order for the same reason.
            extra_args.extend(["--profile", config.profile_name])

        cwd = Path(self._cwd_var.get())
        # ⛔ A LINE, BEFORE THE PROCESS IS EVEN SPAWNED. The server says
        # nothing for as long as it takes to import the model library and the
        # browser engine - a minute on a machine that has never run dots - and
        # an empty log pane in that minute is a launcher that looks dead.
        self._log_line("starting %s on port %d..."
                       % (name or NO_PROFILE, config.port))
        if self._warming():
            self._defer_until_warm(lambda: self._on_start(), "the server")
            return
        try:
            launcher.start(cwd=cwd, port=config.port,
                           headed=config.headed, extra_args=extra_args)
        except RuntimeError as e:
            messagebox.showerror("Could not start dots", str(e))
            return
        self._refresh_status()

    def _launcher_for(self, name: str) -> Launcher:
        """The launcher for a profile, made the first time it is asked for.

        One per profile, because one per PROCESS: two profiles running at
        once are two servers, two ports and two browsers, and a launcher
        holds one child.

        The callbacks POST to the queue and touch nothing: they run on the
        reader thread and on the watchdog thread, and neither of them is the
        thread that made the interpreter Tcl draws its pictures in. The log
        lines are prefixed with the profile's name, because two servers
        writing into one log is a log nobody can read.
        """
        launcher = self._launchers.get(name)
        if launcher is not None:
            return launcher
        label = name or NO_PROFILE

        def on_line(line: str, _label=label) -> None:
            self._post("line", _label, "[%s] %s" % (_label, line))

        def on_exit(code: int, _label=label) -> None:
            self._post("exit", _label, code)

        def on_restart(up: bool, _label=label) -> None:
            self._post("restart", _label, up)

        launcher = Launcher(on_line=on_line, on_exit=on_exit,
                            on_restart=on_restart,
                            server_exe=_frozen_server_exe())
        self._launchers[name] = launcher
        return launcher

    def _on_start_all(self) -> None:
        """Start every profile, each on its own port.

        ⛔ A PROFILE WITH NO PORT IS GIVEN ONE, AND THE GIFT IS KEPT. Two
        servers cannot share a port, so "start everything" on a registry whose
        profiles have no ports would start exactly one of them and fail on
        the rest with a bind error. The port is assigned, written to the
        profile and said out loud, so the second run finds it already there
        and the person can see which port is whose.
        """
        held = registry.list_profiles()
        if not held:
            messagebox.showinfo("No profiles", "There are no profiles yet. "
                                "Use New… to make one.")
            return
        self._on_save()
        if self._warming():
            self._defer_until_warm(lambda: self._on_start_all(),
                                   "every profile")
            return
        taken: set[int] = set()
        started = 0
        for index, profile in enumerate(held):
            port = profile.port or (8765 + index)
            if port in taken:
                self._log_line("%s wants port %d, which another profile "
                               "already has - not started" % (profile.name, port))
                continue
            taken.add(port)
            if not profile.port:
                try:
                    registry.update(profile.name, port=port)
                except registry.ProfileError as exc:
                    self._log_line("could not give %s a port: %s"
                                   % (profile.name, exc))
                    continue
                self._log_line("gave %s port %d" % (profile.name, port))
            launcher = self._launcher_for(profile.name)
            if launcher.running:
                continue
            self._log_line("starting %s on port %d..."
                           % (profile.name, port))
            try:
                launcher.start(cwd=Path(self._cwd_var.get()), port=port,
                               headed=bool(self._headed_var.get()),
                               extra_args=["--profile", profile.name])
            except RuntimeError as exc:
                self._log_line("could not start %s: %s" % (profile.name, exc))
                continue
            started += 1
        self._refresh_status()
        if not started:
            self._log_line("nothing to start: every profile is already up")

    def _on_stop_all(self) -> None:
        """Stop every server this window started."""
        for launcher in list(self._launchers.values()):
            launcher.stop()
        self._refresh_status()

    def _preflight(self, config: "EnvConfig") -> Optional[str]:
        """A pre-spawn check that returns an error message or None.

        The things the server would crash on, named in the user's terms:
        an empty key, a port that something else is holding, a URL that
        does not parse. The check is cheap and runs in a fraction of a
        second; the alternative - spawning the server, having it crash,
        parsing the traceback - is what the user was paying for in
        attention before this method existed.
        """
        if not config.api_key:
            return ("API key is empty. Fill it in the form and click "
                    "Save .env before starting.")
        if config.base_url:
            from urllib.parse import urlparse
            try:
                parsed = urlparse(config.base_url)
                if parsed.scheme not in ("http", "https"):
                    return (f"Base URL must start with http:// or https:// - "
                            f"got '{config.base_url}'.")
                if not parsed.netloc:
                    return f"Base URL has no host: '{config.base_url}'."
            except Exception as e:
                return f"Base URL is not a valid URL: {e}"
        if not (1 <= config.port <= 65535):
            return f"Port is out of range: {config.port}."
        if config.profile_name and not registry.exists(config.profile_name):
            return ("No profile called %r. `dots profile list` shows the ones "
                    "that exist, or pick another from the Profile list."
                    % config.profile_name)
        # Already-bound port: a TCP probe is cheap and tells the user the
        # truth in one line, instead of a server that exits on the bind.
        try:
            import socket
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.bind(("127.0.0.1", config.port))
        except OSError as e:
            return (f"Port {config.port} is already in use. Pick a different "
                    f"port or stop the process holding it ({e}).")
        return None

    def _on_stop(self) -> None:
        self._launcher_for(self._chosen_profile()).stop()
        self._refresh_status()

    def _on_open_browser(self) -> None:
        launcher = self._launcher_for(self._chosen_profile())
        port = launcher._last_port or int(self._port_var.get())
        webbrowser.open_new_tab("http://127.0.0.1:%d" % port)

    def _on_clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")

    def _post(self, kind: str, name: str, payload) -> None:
        """The ONE way a background thread reaches this window.

        ⛔ IT IS A QUEUE, AND THAT IS THE WHOLE OF IT. Calling Tk from another
        thread calls into Tcl from a thread Tcl does not allow, and the result
        is a process that dies with no traceback - see `_pump_events` for the
        whole of it. `queue.Queue` is the one piece of shared state here that
        is built to be shared.
        """
        self._events.put((kind, name, payload))

    def _on_log(self, line: str) -> None:
        """A line from a server, from a thread that is not this one."""
        self._post("line", "", line)

    #: How many lines the log pane keeps.
    #:
    #: ⛔ A LAUNCHER'S LOG IS A TAIL, NOT AN ARCHIVE. The pane grew without
    #: limit, and a Tk `Text` widget with tens of thousands of lines takes
    #: SECONDS to scroll to its end - so `see("end")` on every arriving line
    #: made the window slower and slower until it stopped answering at all.
    #: Measured on this machine: a window that had been logging for an
    #: afternoon was spending its whole life in `Text.see`.
    #:
    #: So the pane keeps the last few thousand lines and drops the rest. The
    #: lines that matter are the ones being written now; the ones from an hour
    #: ago are in the file the user can open.
    LOG_LINES = 2000

    def _log_line(self, line: str) -> None:
        """One line in the log pane, and in the file the pane is a view of."""
        self._write_to_the_log_file(line)
        widget = self._log
        # ⛔ FOLLOW THE TAIL ONLY WHILE THE READER IS AT IT. A person reading
        # the history should not be yanked to the bottom by a line arriving.
        following = widget.yview()[1] >= 0.999
        widget.configure(state="normal")
        widget.insert("end", line + "\n")
        if int(widget.index("end-1c").split(".")[0]) > self.LOG_LINES:
            widget.delete("1.0", "%d.0" % (self.LOG_LINES // 2))
        widget.configure(state="disabled")
        if following:
            widget.see("end")

    def _write_to_the_log_file(self, line: str) -> None:
        """Write the line to the file as well as to the pane.

        ⛔ THE PANE IS NOT A LOG. It is a view of one - and it is the ONLY
        place the servers' output appears, so a launcher that closes takes
        every word of it with it, and the person is left with a window that
        vanished and nothing to say why. The file is in the package's own
        home, next to the conversations and the sessions, and it is written
        whether or not anyone asked for a diagnostic log.
        """
        try:
            if self._log_handle is None:
                return
            self._log_handle.write(line + "\n")
            self._log_handle.flush()
        except Exception:
            # A log that cannot be written is a log nobody sees; it must not
            # be the reason the window goes.
            pass

    def _on_exit(self, code: int, name: str = "") -> None:
        self._post("exit", name, code)

    def _server_restart_state(self, up: bool) -> None:
        """Bring the button state in line with the watchdog's progress.

        `up=False` is called when the watchdog decides to restart; the
        server is not running right now but it will be soon, so the
        user should not be invited to click Start. `up=True` is called
        after a successful respawn, with the new server already up.
        """
        if up:
            self._refresh_status()
        else:
            self._status_var.set("restarting…")

    def _on_server_restart(self, up: bool, name: str = "") -> None:
        self._post("restart", name, up)

    def _refresh_status(self, code: int = 0, name: str = "") -> None:
        """What is running, and which buttons make sense now.

        More than one server can be running, so the state is the whole set
        rather than the one this window happens to be pointed at - and the
        line says which is which, because a port with no profile beside it
        is a port nobody can use.
        """
        running = [(key, launcher) for key, launcher in self._launchers.items()
                   if launcher.running]
        chosen = self._chosen_profile()
        mine = self._launchers.get(chosen)
        if running:
            if mine is not None and mine.running:
                said = "running on http://127.0.0.1:%d" % (mine._last_port or 0)
            else:
                said = "running"
            others = [key or NO_PROFILE for key, launcher in running
                      if key != chosen]
            if others:
                said += " (+%d: %s)" % (len(others), ", ".join(sorted(others)))
            self._status_var.set(said)
        elif code:
            self._status_var.set("exited with code %d" % code)
        else:
            self._status_var.set("stopped")
        self._start_btn.configure(state="disabled" if running else "normal")
        self._stop_btn.configure(state="normal" if running else "disabled")
        self._open_btn.configure(state="normal" if running else "disabled")
        self._save_btn.configure(state="disabled" if running else "normal")
        if code:
            self._log_line("[%s exited with code %d]" % (name or NO_PROFILE, code))

    def _on_close(self) -> None:
        """Stop the server before the window goes away.

        A child outliving its parent is the messy shape: the server is a
        real process group with its own console-less window on Windows, and
        a window that vanishes leaving a zombie is the one a user has to dig
        out of Task Manager. Every server this window started is stopped -
        there can be more than one now, and the others would be the same
        zombie with a different port.
        """
        for launcher in list(self._launchers.values()):
            if launcher.running:
                launcher.stop()
        if self._log_handle is not None:
            try:
                self._log_handle.flush()
                self._log_handle.close()
            except Exception:
                pass
            self._log_handle = None
        self.destroy()


def _hook_for_the_unexpected(exc, value, tb) -> None:
    """Write down anything that got past everything else.

    ⛔ THE LAUNCHER HAD NO LAST WORD. An exception that escaped the event
    loop ended the process with a traceback printed to a console that a
    launcher started from a shortcut does not have - so the window vanished
    and the person was left with nothing. This is that last word: the
    traceback goes to the file, and the process still ends, but the file says
    why.
    """
    _diag_log("".join(traceback.format_exception(exc, value, tb)))
    sys.__excepthook__(exc, value, tb)


def main() -> None:
    """Open the window, and survive a Tk that will not initialise.

    ⛔ ON THIS MACHINE THE TK INTERPRETER SOMETIMES DOES NOT COME UP. It
    fails with "This probably means that tk wasn't installed properly", and
    sometimes it hangs instead of failing. Both were measured here, and
    neither is a fault of this code: the traceback ends inside
    `tkinter.Tk.__init__`, before a line of `dots` has run.

    The launcher is a window with a name on it, and a window that vanishes
    without a word is the worst thing it can do - the person cannot tell it
    from a crash and cannot do anything about it. So:

      * the failure is RETRIED, because it is transient: a second attempt
        three seconds later works, and that is not what a broken install
        does;
      * and if it keeps failing, what is left on the screen is a SENTENCE
        about the Tk library and not a traceback, because a traceback in a
        window with no console is a sentence nobody reads.

    ⛔ AND IT CATCHES EVERYTHING, NOT ONLY THE TK ERRORS. The launcher is
    the last thing between a person and a browser that takes an hour to
    start; an exception nobody expected - an OSError, a MemoryError, a bug
    of ours - must leave the same sentence behind and the same log, not a
    traceback in a void.
    """
    last = None
    sys.excepthook = _hook_for_the_unexpected
    for attempt in range(3):
        try:
            App().mainloop()
            return
        except tk.TclError as exc:
            last = exc
            _diag_log("Tk did not initialise: %s" % exc)
            if attempt < 2:
                time.sleep(3)
        except Exception as exc:
            last = exc
            _diag_log("".join(traceback.format_exception(
                type(exc), exc, exc.__traceback__)))
            if attempt < 2:
                time.sleep(3)
    message = ("The window could not be opened: %s\n\n"
               "The traceback is in %s."
               % (last, _DIAG_PATH or "(set DOTS_GUI_LOG to a file path "
                  "to get one)"))
    print(message, file=sys.stderr)
    _diag_log(message)
    raise SystemExit(1)


# ⛔ PYINSTALLER'S BOOTLOADER RUNS THIS SCRIPT AS __main__, AND THE ENTRY
# POINT `dots-gui = "dots.gui:main"` IS NOT WHAT THE BOOTLOADER LOOKS FOR.
# Without this guard the module-level code runs, `main()` is never called,
# and the process exits silently before anything is drawn. The Tk window
# exists in memory but never enters `mainloop`, which is the one call that
# actually shows the OS the window. Two lines, the difference between "works"
# and "I see a process for 200 ms and then nothing".
if __name__ == "__main__":
    main()