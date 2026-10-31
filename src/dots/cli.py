"""`dots` is `invisible-playwright-mcp ui`, with a registry in front of it.

It goes through the package's command GROUP rather than calling the `ui`
command directly, because the group is where the `.env` beside the command is
read and where an old session directory is carried over. Calling `ui` on its
own would skip both, and a key kept in `.env` would simply not be found.

The package the pin names is built around OpenRouter: its `llm` module
hardcodes the endpoint and the attribution headers, and its `resolve_key`
looks for `OPENROUTER_API_KEY` and nothing else. None of that has a hook,
and a fork kept the pin; what is left is to read provider config before the
group does and rewrite the two module attributes that decide which endpoint
the client talks to.

Three names the file (or the shell) recognises:

    OPENAI_API_KEY   the credential the OpenAI SDK convention names first
    OPENAI_BASE_URL  the endpoint, with no trailing slash
    OPENAI_MODEL     the model id, mirrored to INVISIBLE_MCP_MODEL

`OPENAI_API_KEY` is mirrored into `OPENROUTER_API_KEY` so the package's
`resolve_key` still answers the only question it knows how to ask. Without
the mirror a key typed in `.env` under the SDK's conventional name would
never be found, and the interface would refuse to start on a key it could
read off the screen.

Nothing is overridden when the three names are absent, and OpenRouter
remains the default: an install that only knew OpenRouter before knows
nothing different now.

⛔ TWO THINGS ARE ADDED HERE, AND NEITHER IS A NEW COMMAND LINE OF ITS OWN.

A `--profile NAME` that means "the bundle this profile holds": its browser
directory, its exit, its seed and its working folder. It is resolved here, into
the flags the package already declares, so the package's own precedence is what
decides the winner and a hand-typed `--profile-dir` still beats it.

And `profile` / `auth`, which are `dots`' own and run without ever reaching
the package: there is a registry to read and logins to keep, and neither of
those is a thing an MCP server does on behalf of a model.

⛔ AND THE SPAWNED BROWSER SERVER IS THIS BUILD'S, NOT THE PACKAGE'S.
`Link._params` builds the command for the MCP server the interface talks to,
and everything the model can call is what that process answers `list_tools`
with. Patching that one method - in this process, at runtime, to run
`dots.mcp_boot` instead - is what puts the profile and login tools in front of
the agent without a line of the pinned package changing. See `mcp_boot.py` for
why it then goes through the package's own group rather than around it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Optional

from dotenv import dotenv_values

from invisible_playwright_mcp.quiet import swallow as _quiet

from . import api
from . import auths as vault
from . import engine
from . import geometry
from . import handover
from . import profiles as registry

SUBCOMMAND = "ui"

#: The file read at startup, in the directory the command is run from. The
#: package reads it too, and a second pass on names already written is a no-op
#: because nothing here overrides a variable that is already set.
ENV_FILE = ".env"

#: The commands of this build's own, answered here rather than handed to the
#: package's group - which would answer `No such command 'profile'` for each.
#: Read from `argv[0]` only: `--profile` is a flag of the interface, not one of
#: these, and the two spellings are not interchangeable.
OWN_COMMANDS = ("profile", "auth", "handover")

#: The server variant. Not in `OWN_COMMANDS` because it is not answered here:
#: it is the same product reached over HTTP instead of by a page, so it goes
#: on to the package's own server with the interface's doors closed and the
#: API's opened beside them. See `dots.api`.
SERVE_COMMAND = "serve"


def user_env_path() -> Path:
    """Where the settings that apply to EVERY directory live.

    See `dots.profiles.user_env_path` - the file is the same one, and the
    reason it exists is there.
    """
    return registry.user_env_path()


def _load_env_file(directory: Path) -> None:
    """Apply `.env` from `directory` to `os.environ`, only where unset.

    Mirrors `invisible_playwright_mcp.cli.load_env_file`: same precedence
    (`--flag` > the environment > `.env` > the default), same parser, same
    refusal to override what is already there. The package's own loader runs
    later and announces what it applied; this one is silent on purpose, so the
    same name is not reported twice.
    """
    path = directory / ENV_FILE
    if not path.is_file():
        return

    for name, value in dotenv_values(path).items():
        if value is None or name in os.environ:
            continue
        os.environ[name] = value


def _load_every_env_file(directory: Path) -> None:
    """Read the project's `.env`, and then the one that applies everywhere.

    ⛔ IN THAT ORDER, AND THE ORDER IS THE PRECEDENCE. The project's file is
    read first, so it owns every name it mentions; the file that applies
    everywhere is read second and can only fill in what the project did not
    say. A project that sets its own key therefore wins over the personal
    one, which is the only order that makes sense - and it is the opposite of
    the order the two files are found in.
    """
    _load_env_file(directory)
    user = user_env_path()
    if user.is_file() and user != directory / ENV_FILE:
        _load_env_file(user.parent)


#: The names the launcher writes into `.env`, and the names the package
#: reads. The launcher wrote the first set for years and the package never
#: read a single one of them: the two sets are the same settings under
#: different names, and the launcher passed its own as command-line flags to
#: cover for it.
#:
#: ⛔ WHICH MEANT A `.env` THAT LOOKED LIKE IT WORKED AND DID NOT. A person
#: who set a profile directory in the `.env` - which is what the launcher
#: writes, and what the launcher itself reads - and then ran `dots` from a
#: terminal got a browser with NO profile: an EPHEMERAL one, which keeps no
#: cookies, no logins and no fingerprint between runs. The setting was in the
#: file, the file was read, and the setting was thrown away.
#:
#: So the names are translated here, once, at startup, and the `.env` the
#: launcher writes means the same thing to both.
STEALTHFOX_NAMES = {
    "INVISIBLE_MCP_PROXY": "STEALTHFOX_PROXY",
    "INVISIBLE_MCP_SEED": "STEALTHFOX_SEED",
    "INVISIBLE_MCP_PROFILE_DIR": "STEALTHFOX_PROFILE_DIR",
}


def _translate_env_names(env: Optional[dict] = None) -> Optional[str]:
    """Put the settings the launcher wrote where the package reads them.

    Returns the port the `.env` named, if it named one - the port is a
    command-line option and not an environment name, so it comes back to the
    caller to be put on the command line.

    Nothing is overwritten: a name that is already set is left alone, so the
    shell beats the file, exactly as it does everywhere else.
    """
    where = os.environ if env is None else env
    for wrote, reads in STEALTHFOX_NAMES.items():
        value = where.get(wrote)
        if value and not where.get(reads):
            where[reads] = value
    headed = where.get("INVISIBLE_MCP_HEADED")
    if headed and not where.get("STEALTHFOX_HEADLESS"):
        # ⛔ THE OTHER WAY ROUND. The launcher's name says "headed"; the
        # package's says "headless". One of them has to be inverted, and
        # getting it wrong runs a browser with a window when the person
        # asked for one without - or the other way, which is worse, because
        # a headless browser leaves no trace of itself.
        off = headed.strip().lower() in ("0", "false", "no", "off")
        where["STEALTHFOX_HEADLESS"] = "1" if off else "0"
    port = where.get("INVISIBLE_MCP_PORT")
    return port.strip() if port and port.strip() else None


def _apply_provider_overrides() -> None:
    """Read the provider config and rewrite what the package hardcoded.

    Three independent overrides, in the order they fall:

      * `OPENAI_MODEL` is mirrored to `INVISIBLE_MCP_MODEL`, the name the
        package's `resolve_model` already reads. A model typed under the
        SDK's conventional name reaches the same lookup.
      * `OPENAI_API_KEY` is mirrored to `OPENROUTER_API_KEY`, the name the
        package's `resolve_key` reads and the only one whose absence makes
        the interface refuse to start.
      * `OPENAI_BASE_URL` replaces `BASE_URL` in both `llm` and `cli`, and
        nulls the OpenRouter attribution headers. `cli` imported `BASE_URL`
        by name at module load, so the string there is its own reference;
        patching `llm.BASE_URL` alone would leave the boot line printing
        the old endpoint.

    A trailing slash is stripped because the SDK joins end and path itself,
    and a doubled slash is a 404 that explains itself poorly.
    """
    import invisible_playwright_mcp.cli as ipm_cli
    import invisible_playwright_mcp.llm as llm

    model = os.environ.get("OPENAI_MODEL")
    if model and "INVISIBLE_MCP_MODEL" not in os.environ:
        os.environ["INVISIBLE_MCP_MODEL"] = model

    api_key = os.environ.get("OPENAI_API_KEY")
    if api_key and "OPENROUTER_API_KEY" not in os.environ:
        os.environ["OPENROUTER_API_KEY"] = api_key

    base_url = os.environ.get("OPENAI_BASE_URL")
    if base_url:
        url = base_url.rstrip("/")
        # `cli` imported `BASE_URL` by name at module load, so the string
        # there is its own reference; the boot line prints it. `APP_URL`
        # and `APP_TITLE` live only on `llm` (the package's `cli` never
        # imports them), so patching them here is enough to silence the
        # attribution headers in the actual request.
        llm.BASE_URL = ipm_cli.BASE_URL = url
        llm.APP_URL = ""
        llm.APP_TITLE = ""


# ---------------------------------------------------------------------------
# The browser server this build spawns
# ---------------------------------------------------------------------------

#: What the package's conversation names were BEFORE this build touched them.
#: Kept so that installing the scope twice is not the same as installing it
#: twice: `default-work-work` is a name nobody would ever guess.
_ORIGINAL_IDS: dict = {}


def _install_profile_scope() -> None:
    """Give this profile its own conversation, its own transcript and its own
    browser.

    ⛔ THE PACKAGE NAMES EVERY CONVERSATION "default", AND EVERY PROCESS
    AGREES ON THAT NAME. `chat.DEFAULT_CHAT_ID` is the string "default" for
    every `dots` on every machine, and the two files a conversation owns are
    named after it: the transcript in `chats/default.json` and the remembered
    browser in `sessions/default.json`.

    So two profiles running at once were ONE conversation with ONE browser
    between them, and the second one to start read the first one's saved
    identity - the seed, the exit and the profile directory - and opened THAT
    browser. `dots --profile play` would come up as the work profile, and
    both would write the same transcript over each other.

    The fix is the name: with a profile in use, the conversation is
    `<profile>-default` and everything named after it is that profile's own.
    The child browser server is told which file is its own by the
    environment the interface already passes it, so it follows without a
    second mechanism.

    ⛔ AND NEW CONVERSATIONS GET THE SAME PREFIX, because a name drawn from
    the clock is the same name in two processes started in the same
    millisecond, and two conversations sharing one file is the same defect
    with a smaller number.

    ⛔ AND IT IS INSTALLED AFTER THE PROFILE IS RESOLVED, because it is the
    RESOLVED profile this has to scope. See `main` for the bug that came
    from getting that wrong.
    """
    name = registry.active_name()
    if not name or registry.find(name) is None:
        # No profile, or one that is not there: nothing to scope, and the
        # package's own name for the conversation stands.
        return

    from invisible_playwright_mcp import chat as ipm_chat
    from invisible_playwright_mcp import routes as ipm_routes
    from invisible_playwright_mcp import sessions as ipm_sessions

    if not _ORIGINAL_IDS:
        _ORIGINAL_IDS["chat"] = ipm_chat.DEFAULT_CHAT_ID
        _ORIGINAL_IDS["routes"] = ipm_routes.DEFAULT_CHAT_ID
        _ORIGINAL_IDS["sessions"] = ipm_sessions.DEFAULT_CHAT_ID
        _ORIGINAL_IDS["free_id"] = ipm_sessions.Sessions._free_id

    scoped = "%s-%s" % (_ORIGINAL_IDS["chat"], name)
    # Three modules, three names, one fact: the package's own `cli.ui` imports
    # `DEFAULT_CHAT_ID` from `chat` INSIDE the function, so it picks the
    # patched one up, while `routes` and `sessions` imported it BY NAME at
    # import time and each hold their own binding.
    ipm_chat.DEFAULT_CHAT_ID = scoped
    ipm_routes.DEFAULT_CHAT_ID = scoped
    ipm_sessions.DEFAULT_CHAT_ID = scoped

    drawn = _ORIGINAL_IDS["free_id"]

    def _free_id(self):
        return "%s-%s" % (name, drawn(self))

    ipm_sessions.Sessions._free_id = _free_id


def _install_live_scrolling() -> None:
    """Let the wheel over a live picture scroll the page it pictures.

    The live pane is a video of the browser window, cropped to fit, and
    nothing in the package forwards a gesture from it to the page - so a
    person watching the agent cannot scroll what they are watching. This adds
    that one forwarding: the wheel over a screen goes to the browser as a
    real wheel event, and the next frame shows the new position.

    Two attributes of the package's routes module, both replaced at runtime
    and neither touched on disk: the PAGE it serves and the `build_app` the
    interface calls. See `dots.live` for why those two are the seams.
    """
    from invisible_playwright_mcp import routes

    from . import live

    live.install(routes)


def _install_browser_server() -> None:
    """Point the interface's MCP connection at `dots.mcp_boot`.

    `Link._params` is the one place in the package that knows what command
    starts the browser server, and it is a method rather than a constant
    precisely so that one command cannot be changed in two places. Replacing
    it is therefore the whole of this: the options still travel through
    `runner.child_env` under the names they had, the model key is still
    stripped on the way out, and what comes back over the pipe answers with
    this build's tools.

    A `Link` that already exists - the interface opens the default conversation
    eagerly - was built before this ran, but a `Link` builds its command at
    `open()` time and not before, so the patch lands on every connection this
    process goes on to make. Idempotent, because `dots.cli.main` is called once
    per process and a test may call it more than once.
    """
    from mcp import StdioServerParameters

    from invisible_playwright_mcp import link
    from invisible_playwright_mcp.runner import child_env

    if getattr(link.Link._params, "_dots", False):
        return

    def _params(self) -> StdioServerParameters:
        """How the child is started: this build's server, the package's env.

        Same environment as before - the key removed, the browser options under
        the names the engine reads - plus `DOTS_PROFILE`, which rides along in
        the mapping this call already passes through and names the profile the
        child's own tools read.

        A profile named in the environment fills in what the options did not
        ask for, and nothing more: `dots --profile work --profile-dir D:/x`
        must open `D:/x`, so `apply_env` only writes a name that is absent.
        """
        env = child_env(self._opts, os.environ, key=self._key)
        named = registry.active_name()
        if named:
            profile = registry.find(named)
            if profile is not None:
                registry.apply_env(profile, env)
        return StdioServerParameters(
            command=sys.executable,
            args=["-m", "dots.mcp_boot"],
            env=env,
        )

    _params._dots = True
    link.Link._params = _params


# ---------------------------------------------------------------------------
# `--profile NAME`
# ---------------------------------------------------------------------------

def split_profile_flag(args: list[str]) -> tuple[list[str], str]:
    """The argv without `--profile`, and the name it named.

    Takes `-p NAME` and `--profile NAME` alike, and both the space-separated
    and the `=` spellings. The name is taken out of the argv rather than
    passed along because the package's `ui` command has no such option: leaving
    it there would end at "no such option: --profile" and the profile would be
    the one thing the person typed that did nothing.
    """
    out: list[str] = []
    name = ""
    rest = list(args)
    while rest:
        item = rest.pop(0)
        if item in ("--profile", "-p") and rest:
            name = rest.pop(0)
            continue
        if item.startswith("--profile=") or item.startswith("-p="):
            name = item.split("=", 1)[1]
            continue
        out.append(item)
    return out, name.strip()


def resolve_profile_args(args: list[str], *, env: Optional[dict] = None) -> list[str]:
    """The argv the package's group is called with, a profile applied.

    The order, once and here:

      1. `--profile-dir` already on the command line. A flag somebody typed
         is the most explicit thing there is, and it is the package's own.
      2. `DOTS_PROFILE` in the environment, which `.env` may have set - the
         same place the package reads its other defaults from, so a profile
         chosen once in a file is chosen for every run in that directory.
      3. nothing. The package draws a seed, goes out from this machine, and
         keeps nothing, exactly as it did before any of this.

    The profile's own fields then become the flags the group declares, so
    nothing downstream needs to know a profile exists: `runner.child_env`
    picks `--proxy`, `--seed` and `--profile-dir` up on their way to the
    browser as the three names the engine reads.

    ⛔ AND THE NAME IS PUBLISHED TO THE ENVIRONMENT, because the browser
    server is a different process and its tools need to know whose logins
    they may read. `runner.child_env` passes an environment through rather
    than building one from a fixed list, so `DOTS_PROFILE` arrives there
    without a new variable being invented for it - and the parent's own
    `.env` is read by the child's own loader anyway, so the value is the
    same one on both sides of the pipe.

    ⛔ AND A NAME TYPED ON THE COMMAND LINE IS REFUSED, WHILE A NAME LEFT IN
    `.env` IS NOT. `dots --profile tpo` should say so and stop. A
    `DOTS_PROFILE` naming a profile somebody deleted an hour ago is a line
    that outlived its subject, and refusing to start the product over it
    would be the worse answer by a long way: the warning goes to stderr and
    the run carries on with the package's defaults, exactly as an install
    from before any of this does.
    """
    rest, asked = split_profile_flag(args)
    if any(a == "--profile-dir" or a.startswith("--profile-dir=") for a in rest):
        return rest
    name = asked or registry.active_name(env)
    if not name:
        return rest
    profile = registry.find(name)
    if profile is None:
        if asked:
            raise registry.UnknownProfile(
                "no profile called %r. `dots profile list` shows the ones "
                "that exist; `dots profile add %s` makes this one."
                % (name, name))
        _warn("DOTS_PROFILE names %r and there is no such profile; starting "
              "without one. `dots profile list` shows the ones that exist."
              % name)
        return rest
    os.environ[registry.ACTIVE_VARIABLE] = profile.name
    flags = registry.as_flags(profile)
    # ⛔ THE PROFILE'S PORT, AND ONLY IF NOBODY NAMED ONE. Two profiles
    # running at once need two ports, and a number in the registry is the
    # only place one of them can live. A `--port` on the command line is the
    # more explicit of the two and wins, exactly as `--profile-dir` does.
    if profile.port and not any(a == "--port" or a.startswith("--port=")
                                for a in rest):
        flags = ["--port", str(profile.port)] + flags
    return flags + rest


# ---------------------------------------------------------------------------
# `dots profile` and `dots auth`
# ---------------------------------------------------------------------------

_PROFILE_USAGE = """usage: dots profile <command> [options]

  list                       every profile; * marks the one this run uses
  show NAME                  one profile, in full
  path NAME                  its browser directory, work folder and logins file
  add NAME [options]         make one and its directories
  set NAME [options]         change the fields given, leave the rest
  remove NAME [--wipe]       forget it; --wipe deletes its files as well

  --label TEXT               a name for it, for you
  --proxy URL                http://user:pass@host:port, or socks5://host:port
  --seed N                   the fingerprint identity, a whole number
  --pin GROUP.FIELD=VALUE    the device it claims to be; may be written more
                             than once. Empty takes the pins off again
  --dir PATH                 use this browser directory instead of its own
  --workdir PATH             the folder the agent works in for this profile
  --port N                   the port this profile's interface runs on
  --headed / --headless      whether its browser shows a window
  --binary PATH              a particular engine binary
  --notes TEXT               anything else worth keeping

A profile is a name for a browser directory, an exit, a seed, a working folder
and a port. `dots --profile NAME` starts the interface as one; a --profile-dir
written by hand still wins over it, and so does a --port on the command line.

The pins are the device the browser claims to be - screen.width, screen.dpr,
screen.chrome_w and the rest of the engine's own vocabulary - and they are
what a seed cannot change: a new seed is a new person, and a profile that
must keep its seed can still say what screen it has. `dots profile show NAME`
prints the device the pins and the seed add up to.

Two profiles can run at once, because each has its own port, its own
conversation and its own browser. Give them ports with `--port`."""

_AUTH_USAGE = """usage: dots auth <command> [options]

  list [NAME]                sites with a saved login, and their logins
  get NAME SITE              one login and its password
  add NAME SITE LOGIN PASS   save, or replace the one already there
  remove NAME SITE           forget one
  path NAME                  where the file is

A site is a host: example.com, https://example.com/login and
WWW.Example.com are all `example.com`, and a login saved for example.com is
found on id.example.com too. NAME defaults to the profile this run uses.

These are the only passwords the agent can read, and only for the profile its
browser was opened as. `list` never prints one."""


def _split_flag(rest: list[str], name: str) -> tuple[Optional[str], list[str]]:
    """The value of `--name`, and the rest of the argv.

    Takes both spellings and refuses a flag with nothing after it rather than
    swallowing the next flag as its value - which would turn `--seed --proxy x`
    into a profile with a seed of `--proxy`.
    """
    out: list[str] = []
    value: Optional[str] = None
    rest = list(rest)
    while rest:
        item = rest.pop(0)
        if item == name:
            if not rest:
                raise ValueError("%s needs a value" % name)
            value = rest.pop(0)
            continue
        if item.startswith(name + "="):
            value = item.split("=", 1)[1]
            continue
        out.append(item)
    return value, out


def _split_flags(rest: list[str], name: str) -> tuple[list[str], list[str]]:
    """Every value of `--name`, and the rest of the argv.

    A flag that may be written more than once - one pin per line, say - is
    not a flag with a value: it is several, and the last one written is not
    the only one that counts. The spelling with an `=` is taken as well, so
    `--pin=screen.width=1920` is the same flag.
    """
    out: list[str] = []
    values: list[str] = []
    rest = list(rest)
    while rest:
        item = rest.pop(0)
        if item == name:
            if not rest:
                raise ValueError("%s needs a value" % name)
            values.append(rest.pop(0))
            continue
        if item.startswith(name + "="):
            values.append(item.split("=", 1)[1])
            continue
        out.append(item)
    return values, out


def _profile_fields(rest: list[str], *, where: str) -> dict:
    """The profile fields named on a command line, as a dict for the registry.

    Every option is optional and only the ones actually written are in the
    answer, because `create` and `update` both read a missing key as "leave it
    alone" - the rule that lets one function serve both.
    """
    values: dict = {}
    for flag, field in (("--label", "label"), ("--proxy", "proxy"),
                        ("--dir", "directory"), ("--workdir", "workdir"),
                        ("--notes", "notes"), ("--binary", "binary")):
        value, rest = _split_flag(rest, flag)
        if value is not None:
            values[field] = value
    seed, rest = _split_flag(rest, "--seed")
    if seed is not None:
        try:
            values["seed"] = int(seed)
        except ValueError:
            raise ValueError("--seed takes a whole number, not %r" % seed)
    port, rest = _split_flag(rest, "--port")
    if port is not None:
        try:
            values["port"] = int(port)
        except ValueError:
            raise ValueError("--port takes a whole number, not %r" % port)
        if not 1 <= values["port"] <= 65535:
            raise ValueError("--port is out of range: %d" % values["port"])
    for flag, field in (("--headed", True), ("--headless", False)):
        if flag in rest:
            values["headed"] = field
            rest = [a for a in rest if a != flag]
    pins, rest = _split_flags(rest, "--pin")
    if pins:
        # An empty value is how a pin is taken OFF, which is a different
        # answer from "not mentioned" - the same rule the other flags here
        # follow, where an absent key means "leave it alone".
        if any(not p.strip() for p in pins):
            values["pin"] = {}
        else:
            try:
                values["pin"] = geometry.parse_pin(pins)
            except geometry.GeometryError as exc:
                raise ValueError(str(exc))
    if rest:
        raise ValueError("%s does not take %s. Run `dots profile` for the "
                         "list." % (where, ", ".join(repr(a) for a in rest)))
    return values


def _warn(line: str) -> None:
    """One line on stderr, and never a reason to stop.

    stderr because stdout is the protocol channel the browser server speaks
    on, and a note printed there corrupts the stream - the same rule the
    pinned package states at the top of its own `env.py`.
    """
    try:
        print(line, file=sys.stderr)
    except UnicodeEncodeError:
        with _quiet("a console that cannot hold a warning is not a reason "
                    "to fail the command"):
            sys.stderr.reconfigure(encoding=sys.stderr.encoding or "utf-8",
                                   errors="replace")
        print(line, file=sys.stderr)


def _echo(lines) -> None:
    """Print these lines, and survive a console that cannot hold the text.

    A label in Russian on a Windows console still set to a legacy code page
    raises `UnicodeEncodeError` out of `print`, and a command that listed
    every profile and then died on the eighth is worse than one that shows a
    `?` where the label was. The stream is reconfigured once and the line
    retried: what the console can hold is printed, and what it cannot is
    replaced rather than taking the command down with it.
    """
    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            with _quiet("a console that cannot hold a label is not a reason "
                        "to fail the command"):
                sys.stdout.reconfigure(encoding=sys.stdout.encoding or "utf-8",
                                       errors="replace")
            print(line)


def _device_line(profile) -> str:
    """One line: the device this profile claims to be.

    The geometry is what the seed draws, with the profile's pins on it, so
    this is the same thing the browser is told. A profile with no seed has no
    device of its own yet - the engine draws one when the browser opens, and
    the seed file in the browser directory is where that seed is written.
    """
    return profile.device_line()


def _profile_command(args: list[str]) -> int:
    """`dots profile ...`. Returns the exit code."""
    if not args or args[0] in ("-h", "--help", "help"):
        _echo(_PROFILE_USAGE.splitlines())
        return 0
    verb, rest = args[0], args[1:]
    try:
        if verb == "list":
            held = registry.list_profiles()
            if not held:
                _echo(["No profiles yet. `dots profile add NAME` makes one."])
                return 0
            here = registry.active_name()
            _echo(["%s %s" % ("*" if p.name == here else " ", p.describe())
                   for p in held])
            _echo(["", "* the profile this run starts as (DOTS_PROFILE)"])
            return 0
        if verb == "show":
            profile = registry.get(_need(rest, "name"))
            sites = [a.site for a in vault.load(profile.name)]
            seed = profile.effective_seed()
            _echo([
                profile.name,
                "  label           %s" % (profile.label or "-"),
                "  browser dir     %s" % profile.path(),
                "  working folder  %s" % profile.work(),
                "  exit            %s" % (registry.proxy_host(profile.proxy)
                                          or "this machine"),
                "  seed            %s" % (seed if seed is not None
                                          else "in the browser directory"),
                "  device          %s" % _device_line(profile),
                "  headed          %s" % ("yes" if profile.headed else "default"),
                "  created         %s" % profile.created,
                "  last used       %s" % (profile.last_used or "never"),
                "  logins          %s" % (", ".join(sites) or "none"),
            ])
            if profile.seed is not None and seed != profile.seed:
                # Two records of one thing, and they disagree. The directory
                # is the one that counts - the cookies are in it - and the
                # registry is what is out of step.
                _echo(["  ! the browser directory remembers seed %d, the "
                       "registry says %d. The directory wins; to start over, "
                       "delete .stealth-identity.json inside it."
                       % (seed, profile.seed)])
            if profile.notes.strip():
                _echo(["  notes           %s" % profile.notes.strip()])
            return 0
        if verb == "add":
            name = _need(rest, "name")
            profile = registry.create(
                name, **_profile_fields(rest[1:], where="add"))
            _echo(["Added. %s" % profile.describe()])
            return 0
        if verb == "set":
            name = _need(rest, "name")
            profile = registry.update(
                name, **_profile_fields(rest[1:], where="set"))
            _echo(["Saved. %s" % profile.describe()])
            return 0
        if verb == "remove":
            name = _need(rest, "name")
            wipe = "--wipe" in rest
            rest = [a for a in rest if a != "--wipe"]
            _need(rest, "name")
            gone = registry.delete(name, wipe=wipe)
            if wipe:
                _echo(["Removed %s and everything under %s."
                       % (gone.name, registry.home_directory(name))])
            else:
                _echo(["Removed %s. Its files are still at %s - `dots profile "
                       "remove %s --wipe` deletes those too."
                       % (gone.name, gone.path(), name)])
            return 0
        if verb == "path":
            name = _need(rest, "name")
            profile = registry.get(name)
            _echo([str(profile.path()), str(profile.work()),
                   str(vault.path_for(name))])
            return 0
    except (registry.ProfileError, vault.AuthError, ValueError) as exc:
        _echo(["dots profile: %s" % exc])
        return 1
    _echo(["dots profile: %r is not a command. Run `dots profile` for the "
           "list." % verb])
    return 1


_HANDOVER_USAGE = """usage: dots handover [NAME] [--clear]

  NAME      the profile to look at; defaults to the one this run uses
  --clear   forget the outstanding question

The agent writes a question here when a page needs something only a person can
provide - a code from an authenticator app, a payment confirmation, a
CAPTCHA - and then stops and asks. The answer goes back the same way it came:
you type it into the chat, and the run carries on from where it stopped.

This command only shows and clears it; it is here so the question is
readable from a terminal and not only from the launcher window."""


def _handover_command(args: list[str]) -> int:
    """`dots handover ...`. Returns the exit code.

    Read-only on purpose. A handback is a question a person has to answer, and
    a command that could write one would let a script put words in the
    operator's mouth.
    """
    if args and args[0] in ("-h", "--help", "help"):
        _echo(_HANDOVER_USAGE.splitlines())
        return 0
    clear = "--clear" in args
    rest = [a for a in args if a != "--clear"]
    # ⛔ NOT ONE ARGUMENT MORE THAN `--clear`. A command that could take a
    # question would be a command that could put words in the operator's
    # mouth - and a typo like `--quesion "hello"` landing in the file as
    # though somebody had asked it is the worst version of that.
    if len(rest) > 1 or (rest and rest[0].startswith("-")):
        _echo(["dots handover: takes at most a profile name and --clear. Run "
               "`dots handover` for the list."])
        return 1
    try:
        name = _need(rest, "a profile name") if rest else registry.active_name()
        if not name:
            _echo(["No profile is in use. Give one: `dots handover NAME`."])
            return 1
        if clear:
            gone = handover.clear(name)
            _echo(["Nothing is outstanding for %r." % name] if not gone
                  else ["Cleared the question for %r." % name])
            return 0
        record = handover.current(name)
        _echo([handover.describe(record)] if record
              else ["Profile %r is not waiting on anything." % name])
        return 0
    except (registry.ProfileError, ValueError) as exc:
        _echo(["dots handover: %s" % exc])
        return 1


def _auth_command(args: list[str]) -> int:
    """`dots auth ...`. Returns the exit code."""
    if not args or args[0] in ("-h", "--help", "help"):
        _echo(_AUTH_USAGE.splitlines())
        return 0
    verb, rest = args[0], args[1:]
    here = registry.active_name()
    try:
        if verb == "list":
            name = rest[0] if rest else here
            if not name:
                _echo(["No profile is in use (set DOTS_PROFILE), and no name "
                       "was given. `dots auth list NAME`."])
                return 1
            held = vault.listing(name)
            if not held:
                _echo(["Profile %r has no saved logins." % name])
                return 0
            _echo(["%s  %s" % ("site", "login")])
            _echo(vault.describe_rows(name))
            return 0
        if verb == "get":
            name, site = _two(rest, "a profile and a site")
            record = vault.get(name, site)
            _echo(["%s %s %s" % (record.site, record.login, record.password)])
            return 0
        if verb == "add":
            name = _need(rest, "profile")
            site = _need(rest[1:], "site")
            login = _need(rest[2:], "login")
            password = _need(rest[3:], "password")
            record = vault.add(name, site, login, password)
            _echo(["Saved %s for %s in profile %r."
                   % (record.login or "(no login)", record.site, name)])
            return 0
        if verb == "remove":
            name, site = _two(rest, "a profile and a site")
            record = vault.remove(name, site)
            _echo(["Forgot the login for %s in profile %r." % (record.site, name)])
            return 0
        if verb == "path":
            _echo([str(vault.path_for(_need(rest, "profile")))])
            return 0
    except (registry.ProfileError, vault.AuthError, ValueError) as exc:
        _echo(["dots auth: %s" % exc])
        return 1
    _echo(["dots auth: %r is not a command. Run `dots auth` for the list."
           % verb])
    return 1


_SERVE_USAGE = """usage: dots serve [options]

  --host HOST         the address to listen on (default 127.0.0.1)
  --port PORT         the port to listen on (default 8765)
  --api-key KEY       the key the API asks for; also DOTS_API_KEY
  --profile NAME      the profile this server works as
  --proxy URL         the exit
  --seed N            the fingerprint identity
  --headed            show the browser window
  --binary PATH       a particular engine binary

The same browser, reached over HTTP instead of by a page. The interface's own
doors are closed and the API's are opened in their place:

  GET  /api/v1/health             is it alive
  GET  /api/v1/status             the model, the profile, the device
  POST /api/v1/run                a task, and the answer to it
  POST /api/v1/tasks              a task, and an id to come back for
  GET  /api/v1/tasks/{id}         what became of that task
  GET  /api/v1/tasks/{id}/events  the same, as it happens
  GET  /api/v1/sessions           the conversations this server holds
  POST /api/v1/sessions           a new one, under a name of your own
  POST /api/v1/reset              forget the transcript, keep the browser
  GET  /api/v1/handover           the question the agent is waiting on
  POST /api/v1/handover           the answer to it
  GET  /api/v1/browser            where the browsers are
  GET  /api/v1/frame              a picture of the browser window
  POST /api/v1/stop               stop the run

A key is required the moment the server is not on this machine: a browser with
a person's cookies behind it is not something to serve open. On loopback the
key is optional, and the key is asked for in the `X-Dots-Key` header, in
`Authorization: Bearer ...`, or as `?key=` in the query string.

The answer to a task comes back with the run, the events it produced and
whatever it is waiting on. A task that is still going when the wait is over
is answered with the id to come back for."""


def _need(args: list[str], what: str) -> str:
    """The first of these arguments, or say which one is missing.

    A missing argument is refused in a sentence naming itself rather than
    raising `IndexError`, because `dots auth add` with three of four arguments
    is a typo and the typo should say so.
    """
    if not args or not str(args[0]).strip():
        raise ValueError("%s is missing" % what)
    return str(args[0]).strip()


def _two(args: list[str], what: str) -> tuple[str, str]:
    return _need(args, what.split(" and ")[0]), _need(args[1:], what.split(" and ")[1])


# ---------------------------------------------------------------------------
# The entry point
# ---------------------------------------------------------------------------

def _engine_flag(rest: list[str]) -> list[str]:
    """Hand the servers an engine that is already on disk and already checked.

    ⛔ THE ENGINE IS CHECKED ONCE PER MACHINE, NOT FOUR TIMES BY THE
    PACKAGE. The core re-hashes the whole 250 MB engine on every cache hit -
    nineteen seconds on this machine - and the package does that in the
    interface AND in every server it spawns. Two profiles is four checks, and
    measured: eighty seconds before either port opened.

    A server that is given a binary skips the check altogether, which is what
    `--binary` is for. The path is handed down with it.

    ⛔ AND THIS ASK FOR NO DOWNLOAD. The engine is only named when it has
    ALREADY been checked on this machine - the stamp says so, and the file
    has not changed since. A machine that has never run dots is left to the
    package, which prepares the engine in its own way and says what it is
    doing. A command line must never be the reason 250 MB arrive.

    A `--binary` the person wrote themselves is left alone: they named an
    engine and the answer is to use it.
    """
    if any(a == "--binary" or a.startswith("--binary=") for a in rest):
        return rest
    path = engine.checked_path()
    if not path:
        return rest
    return ["--binary", path] + rest


def _serve_key(given: str) -> str:
    """The key the API asks for: the flag's, then the environment's.

    ⛔ AND IT LEAVES THE ENVIRONMENT ON THE WAY OUT. The browser server this
    process spawns inherits the environment, and the key is a password to a
    browser that carries somebody's cookies - so it is read here, once, by the
    process that needs it, and is not left lying where a child would find it.
    The same posture the pinned package takes with the model key.
    """
    key = (given or "").strip() or api.key_from_env()
    os.environ.pop(api.KEY_VARIABLE, None)
    return key.strip()


def _install_api(key: str) -> None:
    """Open the API's doors, and close the interface's.

    The same seam `dots.live` uses: the package builds its app through
    `routes.build_app`, and replacing that one function is what puts this
    build's routes in front of it. The API's `install` takes the interface's
    routes away rather than serving them beside its own - they ask for no
    key, and a key next to an open door is not a key.
    """
    from invisible_playwright_mcp import routes

    api.install(routes, key=key)


def main(argv: list[str] | None = None) -> None:
    args = list(sys.argv[1:] if argv is None else argv)

    _load_every_env_file(Path.cwd())

    # ⛔ BEFORE THE PACKAGE IS TOUCHED. `profile` and `auth` read a registry of
    # ours and answer from it; a missing model key is a fact about the
    # interface, not about a listing, and refusing to print a profile because
    # no key is set would be the wrong answer to the question.
    if args and args[0] in OWN_COMMANDS:
        code = {"profile": _profile_command,
                "auth": _auth_command,
                "handover": _handover_command}[args[0]](args[1:])
        if code:
            raise SystemExit(code)
        return

    # ⛔ THE SERVER VARIANT IS A VERB OF ITS OWN, AND IT IS TAKEN OFF THE ARGV
    # BEFORE THE PACKAGE SEES IT. The package's group would answer `No such
    # command 'serve'`, and the verb is ours - the flags under it are the
    # package's own, which is why they go straight on to it.
    serving = bool(args) and args[0] == SERVE_COMMAND
    api_key = ""
    if serving:
        args = args[1:]
        try:
            api_key, args = _split_flag(args, "--api-key")
        except ValueError as exc:
            raise SystemExit("dots serve: %s" % exc)
        if any(a in ("-h", "--help") for a in args):
            _echo(_SERVE_USAGE.splitlines())
            raise SystemExit(0)
        api_key = _serve_key(api_key)
        # ⛔ AND A SERVER THAT IS NOT ON THIS MACHINE NEEDS A KEY, REFUSED
        # BEFORE ANYTHING IS STARTED. The check is here rather than in the
        # API's own gate because a gate that says no to every request still
        # leaves a browser server running on somebody's machine; refusing to
        # start says the thing once and stops.
        host = api.host_of(args)
        if not api.is_local(host) and not api_key:
            raise SystemExit(
                "dots serve: %s is not this machine, so the API needs a key. "
                "Pass --api-key KEY, or put %s in the .env. A browser with a "
                "person's cookies behind it is not something to serve open."
                % (host, api.KEY_VARIABLE))

    # ⛔ A LINE, BEFORE THE HEAVY IMPORTS. The package's own first line is
    # printed after the model library has been imported, and on a machine
    # where the antivirus has never seen those files that import takes a
    # MINUTE. The launcher was showing an empty log for that whole minute,
    # which is a launcher that looks dead: the person closes it, and the
    # server was on its way up.
    #
    # This one line is the difference between "it is starting" and "it has
    # crashed", and it costs nothing.
    #
    # And it is NOT a line of the help: `--help` prints a usage and exits, and
    # a "starting" line above it would be a sentence about a server that is
    # not starting.
    if not any(a in ("-h", "--help") for a in args):
        print("dots    starting: reading the configuration and loading the "
              "browser engine. This takes a moment on a machine that has never "
              "run dots before.", flush=True)

    _apply_provider_overrides()
    port_from_env = _translate_env_names()

    # ⛔ `--help` DOES NOT PAY FOR THE WHOLE PRODUCT. Patching the browser
    # server means importing the package's `link` module, and that module
    # imports the MCP SDK - eighteen seconds on this machine, cold - for a
    # command that is going to print a usage and exit. The help is asked for
    # by people who are lost, and making them wait twenty seconds for it is
    # its own kind of answer.
    asking_for_help = any(a in ("-h", "--help") for a in args)

    # ⛔ THE PROFILE IS RESOLVED FIRST, AND THE SCOPE FOLLOWS IT. The order
    # here is the whole of a bug that cost an afternoon: the scope was
    # installed from the ENVIRONMENT and the browser settings from the
    # COMMAND LINE, so in a directory whose `.env` says `DOTS_PROFILE=First`,
    # `dots --profile play` came up with play's browser and FIRST's
    # conversation - and two instances of different profiles were one
    # conversation with one saved browser between them. The second one read
    # the first one's identity, opened the browser directory the first one
    # was holding, and the interface lost its event stream.
    #
    # `resolve_profile_args` publishes the name it settled on into the
    # environment, and everything after this line reads it from there - so
    # there is ONE answer to "whose profile is this" and it is the one the
    # browser is actually being opened as.
    try:
        rest = resolve_profile_args(args)
    except registry.ProfileError as exc:
        raise SystemExit("dots: %s" % exc)

    rest = _engine_flag(rest)
    # ⛔ AND THE PORT THE `.env` NAMED, IF NOTHING ELSE DID. The order is the
    # order of how specific each answer is: a `--port` on the command line is
    # what the person typed, a profile's port is what that identity runs on,
    # and the `.env` is the last word.
    if port_from_env and not any(a == "--port" or a.startswith("--port=")
                                 for a in rest):
        rest = ["--port", port_from_env] + rest
    if not asking_for_help:
        # ⛔ ALL THREE INSTALLATIONS TOUCH THE PACKAGE'S LINK MODULE, AND THAT
        # MODULE IMPORTS THE MCP SDK - eighteen seconds, cold, on this
        # machine. A person who typed `--help` is lost, and making them wait
        # twenty seconds for a usage is its own kind of answer.
        _install_profile_scope()
        _install_browser_server()
        _install_live_scrolling()
        if serving:
            # ⛔ AND THE API'S DOORS, WHICH TAKE THE INTERFACE'S AWAY. The
            # order is the other way round on purpose: the API's `install`
            # wraps whatever `build_app` is by the time it runs, and it takes
            # the routes away afterwards - so the interface's doors are closed
            # whichever order the two went in.
            _install_api(api_key)

    if serving:
        # ⛔ AND THE ADDRESS, PRINTED BEFORE THE SERVER OPENS THE PORT. The
        # package prints its own line - `open http://...` - and that one is
        # about the interface, which this server is not serving. This is the
        # line that says where the API is.
        print("api      http://%s:%d%s" % (api.host_of(rest), api.port_of(rest),
                                           api.PREFIX), flush=True)

    from invisible_playwright_mcp.cli import main as group

    group(args=[SUBCOMMAND, *rest], prog_name="dots")

