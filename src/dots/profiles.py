"""Named profiles: a directory, an exit, a seed, a working folder, a vault.

A profile is what a person means by "my work browser" or "the account for the
shop" - one bundle of everything that decides which browser comes up and what
it is allowed to reach. The pinned package has all of these as separate
command-line options that a caller has to remember together; nothing above it
held the bundle, so the only way to keep a second identity was to write down a
directory, a proxy URL and a number and hope they stayed together.

The registry is the bundle. It lives in ONE file, `<home>/profiles/index.json`,
and each profile owns a directory under `<home>/profiles/<name>/`:

    browser/   the persistent Firefox profile: cookies, logins, the seed
    work/      the working folder the agent writes into for this identity
    auths.json the logins the agent is allowed to type (see `dots.auths`)

⛔ IT IS BUILT ON `invisible_playwright_mcp.storage` RATHER THAN ON A PATH OF
ITS OWN. That module already answers "where does this installation keep its
data" - honouring `INVISIBLE_MCP_HOME`, which is what the tests use to isolate
themselves and what a person sets to move the whole thing to another disk - and
already owns the two rules a file of somebody's data needs: a write that cannot
be interrupted halfway, and a file that will not parse being exactly as usable
as one that was never saved. A second answer to "where is my data" would be a
second directory to find when somebody's data goes missing, which is the worst
moment to discover there are two.

⛔ AND A PROFILE IS NOT THE PINNED PACKAGE'S `profile_dir`. That is one field
of this one: the directory a profile keeps its cookies in. What a profile adds
is the name it answers to, the exit it usually goes out through, the seed that
keeps it the same person on another machine, the working folder that goes with
it, and the credentials the agent may type for it. `--profile-dir` still works
and still wins - see `cli.py` for the order - so nothing that worked before this
stopped working.

Nothing here launches anything, reads the model key, or imports the browser. It
is a file format and the functions that read and write it, which is what makes
it testable without a browser and why every other module here can depend on it.
"""
from __future__ import annotations

import json
import os
import shutil
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from invisible_playwright_mcp import storage
from invisible_playwright_mcp.mcp.identity import IDENTITY_FILE
from invisible_playwright_mcp.quiet import swallow

from . import geometry

#: The directory this keeps its things in, under the package's own home.
ROOT_NAME = "profiles"

#: The one file that names every profile and says what each one is.
INDEX_FILE = "index.json"

#: The variable that names the profile a run starts as. In `.env` as
#: `DOTS_PROFILE`, on the command line as `--profile work`.
ACTIVE_VARIABLE = "DOTS_PROFILE"

#: What a name may contain. Deliberately narrower than `storage.safe_name`,
#: which replaces anything odd with an underscore because a conversation should
#: not become unreachable through no fault of the person who named it. Two
#: profiles whose names differ only in a replaced character would be the same
#: profile, so this one refuses rather than repairs - see `check_name`.
_NAME_OK = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._")

_NAME_MAX = 60

#: What is in a profile that the pinned package reads back as WHO a browser is.
#: `mcp/work.py` calls the three of them `WHO_A_BROWSER_IS` and writes them into
#: the session file on every open; a stored profile carries the same three, so a
#: profile opened from the registry is byte-for-byte the browser that opening
#: the same directory by hand would have produced.
IDENTITY_FIELDS = ("seed", "proxy", "profile_dir")


class ProfileError(Exception):
    """A profile could not be read, made or removed. Says why, in one line."""


class UnknownProfile(ProfileError):
    """A name that is not in the registry."""


def restrict(where: Path) -> None:
    """Take away the group and everybody's read, where the platform allows it.

    ⛔ A BEST EFFORT, AND SAYS SO. On POSIX this is `chmod 600` and it is the
    whole protection: the browser directory next to it holds a real Firefox
    profile with real logins in it, in whatever mode Firefox itself wrote, and
    this one file is not the weakest link in the tree if it is the strongest.
    On Windows there is no `chmod` to speak of and nothing here pretends
    otherwise - the ACL on a directory under `%APPDATA%` is what governs, and
    changing it for one file would be a decision about somebody's data taken
    without them.

    So: on POSIX, closed to the world. On Windows, the same posture as the
    `.env` this product already tells people to keep out of a repository, and
    no worse than the API key sitting in it.
    """
    if os.name != "posix":
        return
    try:
        os.chmod(where, 0o600)
    except OSError:
        pass


def write_json(where: Path, blob: dict) -> Path:
    """Write a JSON file, and replace it in one move.

    ⛔ THE TEMPORARY NAME HAS THE PROCESS IN IT, AND THAT IS THE WHOLE POINT.
    The package's own `storage.write_atomically` writes to a FIXED name -
    `index.json.writing` - and moves it onto the target. That is right for one
    process and wrong for two: both write the same temporary file, the first
    moves it away, and the second's `os.replace` finds nothing there and
    raises. Two profiles running at once were both writing the registry, and
    the loser of that race raised inside whatever asked it to write.

    A name of its own per process removes the collision without changing what
    the write is: the file the reader sees is replaced in one move, so a
    reader never sees half a document.
    """
    where.parent.mkdir(parents=True, exist_ok=True)
    beside = where.with_suffix(where.suffix + (".%d.writing" % os.getpid()))
    try:
        beside.write_bytes(json.dumps(blob, indent=2,
                                      sort_keys=True).encode("utf-8"))
        os.replace(beside, where)
    except OSError:
        with swallow("a temporary file that will not go is not worth refusing "
                     "the write for"):
            beside.unlink()
        raise
    restrict(where)
    return where


def checked_pin(pin: Any) -> dict:
    """A pin as the engine will take it, or refuse.

    The engine owns the vocabulary of pins and refuses a key it does not know
    with a message naming the keys it does; that refusal is what this passes
    on, because a second list of keys here would be a second answer to the
    same question and it would be wrong the day the engine grows one.
    """
    try:
        return geometry.check_pin(pin)
    except geometry.GeometryError as exc:
        raise ProfileError(str(exc)) from None


def check_name(name: str) -> str:
    """The name, stripped, or refuse.

    Empty is refused rather than defaulted: a profile called nothing is a
    profile two people will both make and neither will find. The allowed set
    is the one above; `..` is refused explicitly because it survives a
    character filter and would put a profile's directory outside its own.
    """
    clean = (name or "").strip()
    if not clean:
        raise ProfileError("a profile needs a name")
    if len(clean) > _NAME_MAX:
        raise ProfileError("a profile name is at most %d characters, %r is %d"
                           % (_NAME_MAX, clean, len(clean)))
    if clean in (".", ".."):
        raise ProfileError("%r is not a name a profile can have" % clean)
    bad = [c for c in clean if c not in _NAME_OK]
    if bad:
        raise ProfileError(
            "a profile name may hold letters, digits, dot, dash and underscore "
            "only - %r has %s. Pick another name: changing this one to fit "
            "would make two different names the same profile."
            % (clean, ", ".join(repr(c) for c in bad)))
    return clean


def now() -> str:
    """The timestamp every record carries, in the format the package's own
    files use (`%Y-%m-%dT%H:%M:%S`), so a profile and a session written by
    this build sort against each other by string."""
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def root() -> Path:
    """Where every profile of this installation lives.

    `storage.home()` is the answer the pinned package gives, so `INVISIBLE_MCP_HOME`
    moves the profiles along with the sessions - which is the point of it.
    """
    return storage.home() / ROOT_NAME


def user_env_path() -> Path:
    """Where the settings that apply to EVERY directory live.

    ⛔ THE KEY WAS ONLY EVER READ FROM THE DIRECTORY YOU HAPPENED TO BE IN.
    A `.env` is a property of a project, and a key is a property of a person,
    and a person does not have a different API key per folder they open a
    terminal in. So the key can be written once, here, and every run finds it -
    and a project that has its own `.env` still wins, because the project's
    file is read first and only fills in what is missing.

    It is in the package's own home - next to the conversations, the sessions
    and the profiles - because that is where this program already keeps
    everything that belongs to the person rather than to a project.
    """
    try:
        return storage.home() / ".env"
    except Exception:
        return Path.home() / ".dots.env"


def home_directory(name: str) -> Path:
    """One profile's own directory: `<root>/<name>`.

    The name is already checked by the time this is reached from the
    registry; `check_name` is called again here because `delete` is reachable
    with a name straight off a command line and this is what keeps a
    directory from being built outside `root()`.
    """
    return root() / check_name(name)


def browser_dir(name: str) -> Path:
    """Where the browser's own state goes: cookies, logins, the seed file."""
    return home_directory(name) / "browser"


def stored_seed(directory: Any) -> Optional[int]:
    """The seed the browser directory remembers, or None.

    Read out of the file the pinned package writes there, by the name it
    publishes. The directory is the identity that matters - the cookies and
    the logins are in it - so the seed it remembers is the one a browser
    opened on that directory has to be, whatever the registry says.
    """
    try:
        blob = storage.read_json(Path(directory) / IDENTITY_FILE)
    except (OSError, TypeError, ValueError):
        return None
    seed = (blob or {}).get("seed") if isinstance(blob, Mapping) else None
    try:
        return int(seed) if seed is not None else None
    except (TypeError, ValueError):
        return None


def work_dir(name: str) -> Path:
    """The working folder that belongs to this profile.

    Separate from the browser directory on purpose. The browser directory is
    the engine's: it writes a seed, a cache and a lock into it, and a program
    that decided to clean it up would delete somebody's logins. This one is
    the agent's to write into, and it starts empty.
    """
    return home_directory(name) / "work"


def index_path() -> Path:
    """The one file every profile is written to."""
    return root() / INDEX_FILE


@dataclass
class Profile:
    """One identity: where its browser lives, how it goes out, where it works.

    Every field is optional except the name, because a profile is allowed to
    decide only some of them: a person with one browser and no proxy needs a
    name and nothing else, and making them type `--proxy ""` to say so would
    be a rule nobody would keep.

    `dir` is the escape hatch. Empty means "the directory under the registry",
    and anything in it is used instead - which is how a profile adopts a
    directory made by hand, or one that an older `--profile-dir` run already
    had logins in.

    `pin` is the device this profile claims to be, as pins on the profile the
    seed draws: `screen.width`, `screen.dpr`, `screen.chrome_w` and the rest.
    Empty means "whatever the seed draws", which is the right answer for most
    profiles. It is the wrong answer for one whose seed was drawn for a
    machine this one is not - and the seed cannot be changed to fix that,
    because a new seed is a new person. See `dots.geometry`.
    """

    name: str
    label: str = ""
    proxy: str = ""
    seed: Optional[int] = None
    headed: Optional[bool] = None
    binary: str = ""
    workdir: str = ""
    directory: str = ""
    port: int = 0
    notes: str = ""
    #: The device this profile claims to be, as pins on the sampled profile.
    #: Empty means "whatever the seed draws", which is the right answer for
    #: most profiles and the wrong one for a profile whose seed was drawn on
    #: a machine whose screen this one is not.
    pin: dict = field(default_factory=dict)
    created: str = field(default_factory=now)
    last_used: str = ""

    # -- the paths -----------------------------------------------------------

    def path(self) -> Path:
        """Where the browser's own state goes: cookies, logins, the seed."""
        if self.directory.strip():
            return self._absolute(self.directory)
        return browser_dir(self.name)

    def work(self) -> Path:
        """The working folder, absolute, created on demand by `prepare`."""
        if self.workdir.strip():
            return self._absolute(self.workdir)
        return work_dir(self.name)

    def _absolute(self, raw: str) -> Path:
        """A stored path, made absolute against THIS PROFILE'S OWN HOME.

        ⛔ NEVER AGAINST THE CURRENT DIRECTORY. A relative path resolved
        against the working directory means one thing in the launcher and
        another in a terminal, and the difference is a whole browser profile -
        cookies, logins, a fingerprint - left in a directory nobody knows
        about. It happened here: a profile whose folder was written as
        `Second_proxy` put a 150 MB Firefox profile in the repository root,
        because that is where the command was run from.

        So a relative path is taken against the profile's own directory,
        which is the same place whatever the current directory is, and the
        same place next time.
        """
        candidate = Path(raw).expanduser()
        if not candidate.is_absolute():
            candidate = home_directory(self.name) / candidate
        return candidate.resolve()

    def effective_seed(self) -> Optional[int]:
        """The seed the browser will actually be opened with.

        The browser directory remembers who it was, and that memory wins: the
        cookies and the logins live in that directory, and a browser that came
        back as somebody else would bring them from another machine. The
        pinned package refuses such a profile outright - "this profile is
        seed N and you asked for M" - so a registry that disagrees with the
        directory is a profile that will not open, and the registry is the
        one that is wrong.

        So the seed that is passed, and the device that is shown, are the one
        the directory remembers. With no directory and no memory of one, the
        registry's own seed is what there is.
        """
        remembered = stored_seed(self.path())
        if remembered is not None:
            return remembered
        return self.seed

    def device(self) -> dict:
        """The device this profile claims to be.

        The geometry the seed draws, with this profile's pins on it, which is
        what the browser is told. Empty for a profile with no seed: the engine
        draws one when the browser opens, and the seed it drew is written into
        the browser directory, where it is read back from.
        """
        seed = self.effective_seed()
        if seed is None:
            return {}
        return geometry.declared(seed, self.pin)

    def device_line(self) -> str:
        """One line for a person: the device this profile claims to be.

        The geometry the seed draws, with this profile's pins on it, and the
        pins named - because a pin that is not visible is a pin nobody can
        take off. The seed the browser will be opened with is the one the
        browser directory remembers, so the line describes the browser that
        will open and not the record that disagrees with it.
        """
        pins = geometry.format_pin(self.pin)
        seed = self.effective_seed()
        if seed is None:
            said = "drawn when the browser opens"
        else:
            try:
                device = self.device()
                said = ("%dx%d, ratio %s, frame %dx%d, taskbar %d"
                        % (device["screen_width"], device["screen_height"],
                           device["dpr"], device["chrome_w"], device["chrome_h"],
                           device["taskbar_px"]))
            except Exception:
                said = "not readable"
        if pins:
            said += ", pinned %s" % pins
        if self.seed is not None and seed is not None and seed != self.seed:
            said += (" - the browser directory remembers seed %d, the "
                     "registry says %d" % (seed, self.seed))
        return said

    def port_or(self, default: int) -> int:
        """The port this profile runs its interface on.

        Zero means "not set", and zero is not a port anybody can listen on -
        so a profile that never chose one takes the default rather than
        refusing to start.

        The port is here because two profiles running at once need two of
        them, and a number somebody has to remember is a number they will
        get wrong: the second instance would fail to bind and say so, and
        the first would be the one that works.
        """
        return int(self.port) if self.port else default

    # -- what the browser is told -------------------------------------------

    def launch_settings(self) -> dict[str, Any]:
        """The launch kwargs this profile implies, and only those it does.

        The three keys are `mcp/work.WHO_A_BROWSER_IS` - `IDENTITY_FIELDS`
        here, same names - which is what the package writes into the session
        file on every open, so a profile opened from the registry produces
        byte-for-byte the browser that opening the same directory by hand
        would have produced.

        An absent key is not the same as an empty one: `profile_dir=None` is
        the caller saying nothing, and `proxy=""` is the caller saying "go out
        from this machine". Sending `""` for a profile that has no proxy would
        turn an absence into a contradiction on every open, so nothing unset is
        sent at all.
        """
        out: dict[str, Any] = {}
        values = {"seed": self.effective_seed(),
                  "proxy": self.proxy.strip() or None}
        for key in IDENTITY_FIELDS:
            if key == "profile_dir":
                # Always. A profile with no directory of its own would still
                # have to keep its cookies somewhere, and `browser_dir` is
                # where: a profile that reopened as a different person every
                # visit would be the one thing it must never do.
                out[key] = str(self.path())
            elif values.get(key) is not None:
                out[key] = int(values[key]) if key == "seed" else values[key]
        return out

    def describe(self) -> str:
        """One line for a person: the name, what it is called, where it goes."""
        parts = [self.name]
        if self.label.strip():
            parts.append("(%s)" % self.label.strip())
        say = []
        say.append(self.path().name if self.directory.strip() else "own directory")
        if self.proxy.strip():
            say.append("via %s" % proxy_host(self.proxy))
        seed = self.effective_seed()
        if seed is not None:
            say.append("seed %d" % seed)
        say.append("work %s" % self.work().name)
        return "%s - %s" % (" ".join(parts), ", ".join(say))

    # -- the file ------------------------------------------------------------

    def to_json(self) -> dict[str, Any]:
        """The record as the index file holds it. Unknown keys are dropped."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "created" and not value:
                value = now()
            out[f.name] = value
        return out

    @classmethod
    def from_json(cls, name: str, blob: Mapping[str, Any]) -> "Profile":
        """A record out of the file, ignoring what it does not understand.

        A file written by a later build carries keys this one has never heard
        of; they are dropped on the next write rather than refused here, so an
        older `dots` still runs against a newer registry instead of treating
        somebody's profiles as corrupt.
        """
        known = {f.name for f in fields(cls)}
        clean = {k: v for k, v in dict(blob).items() if k in known}
        seed = clean.get("seed")
        if seed is not None:
            try:
                clean["seed"] = int(seed)
            except (TypeError, ValueError):
                clean["seed"] = None
        headed = clean.get("headed")
        if headed is not None:
            clean["headed"] = bool(headed)
        for text in ("name", "label", "proxy", "binary", "workdir",
                     "directory", "notes", "created", "last_used"):
            clean[text] = str(clean.get(text) or "")
        port = clean.get("port")
        try:
            clean["port"] = int(port) if port else 0
        except (TypeError, ValueError):
            clean["port"] = 0
        # ⛔ A PIN THAT DOES NOT PARSE IS DROPPED, NOT REFUSED. The file is
        # somebody's work and this build is not the only one that will ever
        # read it: a pin written by a newer engine, or a half-written one,
        # must not stop the rest of the profile from being usable. The pin is
        # the only field that can be wrong in a way the engine would refuse
        # later, so it is the only one checked here - and the check is the
        # engine's, so the vocabulary is not written down here.
        pin = clean.get("pin")
        if pin:
            try:
                clean["pin"] = geometry.check_pin(pin)
            except Exception:
                # The pin is the only field the engine would refuse later, so
                # it is the only one checked here - and a pin this build
                # cannot read is no pin at all rather than a reason to refuse
                # somebody's profile.
                clean["pin"] = {}
        else:
            clean["pin"] = {}
        return cls(**clean)


def proxy_host(proxy: str) -> str:
    """The exit a proxy URL names, with the credentials taken off.

    `http://user:secret@host:8080` is `host:8080`. Nothing here prints a
    password - not in a listing, not in a log line, not in an error - and the
    one that has to be shown to be useful is the host, which is what a person
    reading a table of profiles is asking for anyway.
    """
    raw = (proxy or "").strip()
    if not raw:
        return ""
    if "://" in raw:
        raw = raw.split("://", 1)[1]
    if "@" in raw:
        raw = raw.rsplit("@", 1)[1]
    return raw or "proxy"


# ---------------------------------------------------------------------------
# The index
# ---------------------------------------------------------------------------

def load_all() -> dict[str, Profile]:
    """Every profile on this machine, by name.

    An absent file is an empty registry and a file that will not parse is the
    same thing - `storage.read_json` decides that, and it is the right answer:
    a registry that cannot be read should not stop the product from starting,
    and it is not this module's job to decide what to do with the file.
    """
    blob = storage.read_json(index_path())
    out: dict[str, Profile] = {}
    for name, record in (blob or {}).items():
        if not isinstance(record, Mapping):
            continue
        try:
            out[check_name(str(name))] = Profile.from_json(str(name), record)
        except ProfileError:
            # A name this build would refuse still belongs to somebody: it is
            # left out of the answer rather than deleted, so downgrading does
            # not destroy what a newer build wrote.
            continue
    return out


def save_all(profiles: Mapping[str, Profile]) -> Path:
    """Write the whole registry, replacing the file in one move.

    Every write is the whole file, not a patch: the index is small, and a
    half-written index is a registry whose middle is missing. The write is
    atomic - the file the reader sees is replaced in one move, never a
    half-written one - and its temporary name carries the process id, so two
    profiles running at once do not write over each other's temporary file.
    """
    blob = {name: profile.to_json()
            for name, profile in sorted(profiles.items())}
    return write_json(index_path(), blob)


def get(name: str) -> Profile:
    """The profile by name, or raise."""
    try:
        key = check_name(name)
    except ProfileError as exc:
        raise UnknownProfile(str(exc)) from None
    found = load_all().get(key)
    if found is None:
        raise UnknownProfile("no profile called %r. `dots profile list` shows "
                             "the ones that exist." % key)
    return found


def find(name: str) -> Optional[Profile]:
    """The profile by name, or None. For a caller with a real fallback."""
    try:
        return get(name)
    except UnknownProfile:
        return None


def list_profiles() -> list[Profile]:
    """Every profile, by name. Sorted, so a listing does not shuffle."""
    return [profile for _, profile in sorted(load_all().items())]


def exists(name: str) -> bool:
    """Whether a profile of this name is in the registry."""
    return find(name) is not None


def create(name: str, *, overwrite: bool = False, **fields_in: Any) -> Profile:
    """Make a profile, and its directories.

    `overwrite` is a separate word rather than something inferred from whether
    a profile of that name is there: replacing a bundle that holds a browser's
    logins because a name was typed again is not a thing a caller should be
    able to do by accident.

    The fields are the dataclass's, and an unknown one is refused. A typo in a
    field name is a value that would be silently dropped otherwise, and this is
    the one place where dropping it would be permanent.
    """
    key = check_name(name)
    known = {f.name for f in fields(Profile)} - {"name", "created", "last_used"}
    unknown = sorted(set(fields_in) - known)
    if unknown:
        raise ProfileError("a profile has no field%s %s. It has: %s."
                           % ("" if len(unknown) == 1 else "s",
                              ", ".join(repr(u) for u in unknown),
                              ", ".join(sorted(known))))
    if "pin" in fields_in:
        fields_in["pin"] = checked_pin(fields_in["pin"])
    profiles = load_all()
    if key in profiles and not overwrite:
        raise ProfileError("there is already a profile called %r. `dots profile "
                           "show %s` reads it, `dots profile set %s ...` changes "
                           "it, or pass --overwrite to replace it."
                           % (key, key, key))
    if key in profiles and overwrite:
        # Kept: `created` is the day the profile was first made, and replacing
        # a profile is a change to it, not a new one.
        fields_in.setdefault("created", profiles[key].created)
    profile = Profile(name=key, **fields_in)
    profiles[key] = profile
    prepare(profile)
    save_all(profiles)
    return profile


def update(name: str, **fields_in: Any) -> Profile:
    """Change the fields given, leave the rest, and say which ones moved."""
    key = check_name(name)
    profiles = load_all()
    profile = profiles.get(key)
    if profile is None:
        raise UnknownProfile("no profile called %r. `dots profile list` shows "
                             "the ones that exist." % key)
    known = {f.name for f in fields(Profile)} - {"name", "created"}
    unknown = sorted(set(fields_in) - known)
    if unknown:
        raise ProfileError("a profile has no field%s %s."
                           % ("" if len(unknown) == 1 else "s",
                              ", ".join(repr(u) for u in unknown)))
    if "pin" in fields_in:
        fields_in["pin"] = checked_pin(fields_in["pin"])
    for key_f, value in fields_in.items():
        setattr(profile, key_f, value)
    profiles[key] = profile
    prepare(profile)
    save_all(profiles)
    return profile


def delete(name: str, *, wipe: bool = False) -> Profile:
    """Forget a profile, and by default leave its files exactly where they are.

    `wipe` is what deletes the browser directory, the working folder and the
    logins in them, and it is a separate word because that is a fire. Without
    it the registry entry goes and the directory stays, so a name that is
    reused later comes back to the same cookies - and so a mistaken `delete`
    costs a list entry rather than a person's sessions on a dozen sites.
    """
    key = check_name(name)
    profiles = load_all()
    profile = profiles.pop(key, None)
    if profile is None:
        raise UnknownProfile("no profile called %r." % key)
    if wipe:
        shutil.rmtree(home_directory(key), ignore_errors=True)
    save_all(profiles)
    return profile


def prepare(profile: Profile) -> Profile:
    """Make the directories this profile needs, and leave the rest alone.

    Idempotent, and cheap: it is called on every create and every set, and it
    touches nothing that already exists. The browser directory is made here
    rather than at launch because an unwritable one should be refused while
    somebody is looking at the form, not twenty minutes into a task.
    """
    profile.path().mkdir(parents=True, exist_ok=True)
    profile.work().mkdir(parents=True, exist_ok=True)
    return profile


def touch(name: str) -> None:
    """Record that a profile was used, writing nothing if it is not there.

    Called on every launch, so it swallows everything: a stamp that could
    fail must not be the reason a browser did not open.
    """
    try:
        profiles = load_all()
        profile = profiles.get(check_name(name))
        if profile is None:
            return
        profile.last_used = now()
        save_all(profiles)
    except Exception:
        pass


def active_name(env: Optional[Mapping[str, str]] = None) -> str:
    """The profile this run starts as, or an empty string for none.

    Read once, here, from the environment - the same "never a tool argument,
    never a schema, never something a model can read or invent" rule the
    pinned package applies to `INVISIBLE_MCP_SESSION_ID`. The value reaches
    the spawned server through `child_env`, which passes the whole environment
    through, so it arrives there without a single new variable being invented
    for it.
    """
    where = os.environ if env is None else env
    return (where.get(ACTIVE_VARIABLE) or "").strip()


def active(env: Optional[Mapping[str, str]] = None) -> Optional[Profile]:
    """The profile this run starts as, or None when there is not one.

    A name in the environment that is not in the registry is None and not an
    error: `.env` is written by hand, a line left behind after a `delete` is
    not a reason to refuse to start.
    """
    return find(active_name(env))


def resolve(name: str, env: Optional[Mapping[str, str]] = None) -> Optional[Profile]:
    """The profile a caller means: the one named, else the run's, else None."""
    if name.strip():
        return get(name)
    return active(env)


def apply_env(profile: Profile, env: Optional[dict] = None) -> dict[str, str]:
    """Put a profile's launch settings into an environment mapping, where
    nothing has been asked for yet.

    The pinned package reads `STEALTHFOX_SEED`, `STEALTHFOX_PROXY` and
    `STEALTHFOX_PROFILE_DIR` from the environment of the browser server
    process (`mcp/plan.py`, `mcp/identity.py`), and `runner.child_env` is what
    fills them from the interface's options. Writing the same three names here
    means a profile reaches the browser the way a `--proxy` on the command
    line does, with no second mechanism to keep in step.

    ⛔ AND IT NEVER OVERRIDES A NAME THAT IS ALREADY THERE. That mapping is
    built by `child_env` from the options the interface was started with, and
    an option is the most explicit thing there is: `dots --profile work
    --profile-dir D:/scratch` must open `D:/scratch`, and a profile filling
    the name back in here would quietly undo the flag that was typed after it.

    A profile that has no proxy adds no `STEALTHFOX_PROXY`: an empty one is a
    decision to go out direct, and a profile that never mentioned a proxy has
    not made that decision.
    """
    where = os.environ if env is None else env
    for name, value in profile.launch_settings().items():
        key = "STEALTHFOX_%s" % name.upper()
        if key not in where:
            where[key] = str(value)
    where.setdefault(ACTIVE_VARIABLE, profile.name)
    return where


def as_flags(profile: Profile) -> list[str]:
    """A profile as the command-line options that mean it.

    Used by `cli.py` and by the GUI to hand a profile to the interface's own
    `ui` command. The flags are the ones `invisible_playwright_mcp.cli`
    already declares, so a profile and a hand-typed command line end up in the
    same place - which is what makes the resolution in `cli.py` orderable at
    all.
    """
    out: list[str] = []
    if profile.proxy.strip():
        out += ["--proxy", profile.proxy.strip()]
    seed = profile.effective_seed()
    if seed is not None:
        out += ["--seed", str(int(seed))]
    if profile.headed:
        out.append("--headed")
    if profile.binary.strip():
        out += ["--binary", profile.binary.strip()]
    out += ["--profile-dir", str(profile.path())]
    return out


def describe_rows(profiles: Iterable[Profile], *,
                  env: Optional[Mapping[str, str]] = None) -> list[str]:
    """Lines for `dots profile list`: what each profile is, and no secrets.

    The name, the label and a `*` on the one this run starts as. A proxy is
    shown as a host by `describe`, never as a URL with credentials in it, and
    no password is in this product's output anywhere - see `dots.auths` for
    where one does live.
    """
    rows = []
    here = active_name(env)
    for profile in profiles:
        rows.append("%s %s" % ("*" if profile.name == here else " ", profile.name)
                    + (" - %s" % profile.label if profile.label.strip() else ""))
    return rows