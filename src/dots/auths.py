"""Logins the agent may type, kept per profile.

A Firefox profile remembers the cookies of a site somebody signed into by
hand. It does not remember a password typed into a form that was never
submitted, and it cannot answer "what is the password for the account I keep
for this shop" - because the browser never held the password at all. That is
the gap this file is for: a small, deliberate list of the logins a person has
decided an agent is allowed to use, stored beside the profile they belong to.

    <home>/profiles/<name>/auths.json

⛔ WHAT IS AND IS NOT ENCRYPTED. The passwords here are written as they are
typed. There is no DPAPI, no keyring, no master password, and pretending
otherwise would be the worst kind of lie in a file whose whole job is to be
trusted about secrets. What is done is the part that is actually free: the file
is `600` on POSIX, it is a separate file from the registry so listing profiles
never reads it, no listing anywhere in this product prints a password, and the
agent is told to type one rather than to quote one. The API key in `.env` sits
in a plain file under the same house, and pretending these are safer than that
would be the claim to avoid.

⛔ AND THE AGENT IS NOT GIVEN THE DOOR BY ACCIDENT. Reading a password is a
tool call like any other, and the model chooses to make it; nothing here
answers a password to a question that was never asked. What the tools do NOT
do is hand the whole file over: `list_auths` returns sites and logins and says
which sites have a password, `get_auth` returns one site on purpose.

The site key is a host, never a path: `https://shop.example.com/cart?x=1`,
`shop.example.com/` and `SHOP.Example.com` are all `shop.example.com`. A key
that carried the path would make two pages of one shop two different accounts,
which is exactly the confusion a person saving a login is trying to avoid.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlparse

from invisible_playwright_mcp import storage

from . import profiles

#: The file one profile's logins are in, inside that profile's directory.
FILE_NAME = "auths.json"


class AuthError(Exception):
    """A login could not be read, written or removed. Says why, in one line."""


@dataclass
class Auth:
    """One login, for one host.

    The two selectors are optional and they are the reason this is not just a
    password file. A site that never asks for the same field twice is the
    exception; saving `input[name=login]` next to the login turns "sign in"
    from a task the model has to solve every time into one call.
    """

    site: str
    login: str = ""
    password: str = ""
    login_selector: str = ""
    password_selector: str = ""
    notes: str = ""
    created: str = field(default_factory=profiles.now)
    updated: str = ""

    def to_json(self) -> dict[str, Any]:
        """The record as the file holds it."""
        out: dict[str, Any] = {}
        for f in fields(self):
            value = getattr(self, f.name)
            if f.name == "updated" and not value:
                value = self.created
            out[f.name] = value
        return out

    @classmethod
    def from_json(cls, blob: Mapping[str, Any]) -> "Auth":
        """A record out of the file, ignoring keys this build does not know."""
        known = {f.name for f in fields(cls)}
        clean = {k: str(v) for k, v in dict(blob).items() if k in known}
        clean["site"] = site_key(clean.get("site") or "")
        if not clean["site"]:
            raise AuthError("a login with no site is not a login")
        return cls(**clean)

    def describe(self) -> str:
        """One line for a person: the site, the login, never the password."""
        said = self.login or "(no login saved)"
        extra = self.notes.strip()
        return "%s - %s%s" % (self.site, said, (" - %s" % extra) if extra else "")


def site_key(value: str) -> str:
    """The host a site means, whatever shape it was written in.

    Accepts a full URL, a bare host, or a host with a path; lowercases it,
    drops a leading `www.`, and keeps the port because `localhost:8080` and
    `localhost` are two different applications on most machines.

    An empty string is refused by the caller rather than here: `Auth.from_json`
    turns one into an `AuthError` and `add` asks about it before writing, and a
    function that raises on an argument nobody else has to handle is a function
    that gets wrapped in a `try` anyway.
    """
    raw = (value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        raw = "//" + raw
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip().lower()
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port and not ((parsed.scheme == "http" and port == 80)
                     or (parsed.scheme == "https" and port == 443)):
        host = "%s:%d" % (host, port)
    return host


def path_for(name: str) -> Path:
    """Where one profile's logins are kept."""
    return profiles.home_directory(name) / FILE_NAME


def load(name: str) -> list[Auth]:
    """Every login this profile holds, by site.

    A file that will not parse is no logins, not an error: the same rule
    `storage.read_json` applies everywhere else in this product, and a
    corrupted file that stops the agent from starting is a worse outcome than
    starting without what the file held.
    """
    blob = storage.read_json(path_for(name))
    records = (blob or {}).get("auths") if isinstance(blob, Mapping) else None
    out: list[Auth] = []
    for record in records or []:
        if not isinstance(record, Mapping):
            continue
        try:
            out.append(Auth.from_json(record))
        except AuthError:
            continue
    out.sort(key=lambda a: a.site)
    return out


def save(name: str, auths: list[Auth]) -> Path:
    """Write one profile's logins, replacing the file in one move.

    The write is atomic and its temporary name carries the process id, so an
    agent saving a login in one process does not collide with the launcher
    reading the same file in another.
    """
    blob = {
        "profile": name,
        "auths": [a.to_json() for a in sorted(auths, key=lambda a: a.site)],
    }
    return profiles.write_json(path_for(name), blob)


def listing(name: str) -> list[Auth]:
    """Every login of a profile, by site. Spelled out for a caller that reads
    better with a verb - `auths.listing` beside `auths.load` says which of
    the two is meant to be shown to a person."""
    return load(name)


def find(name: str, site: str) -> Optional[Auth]:
    """The login for a host, or None.

    A hit on the exact host first; failing that, on the parent domain, because
    a login saved for `example.com` is the one somebody means when the browser
    is on `id.example.com`. The parent hop is one level only: `a.b.example.com`
    falls back to `example.com` and never further, because a login held for
    the whole of `com` is not a thing anybody saved.
    """
    key = site_key(site)
    if not key:
        return None
    held = {a.site: a for a in load(name)}
    if key in held:
        return held[key]
    parts = key.split(".")
    if len(parts) > 2:
        parent = ".".join(parts[1:])
        return held.get(parent)
    return None


def get(name: str, site: str) -> Auth:
    """The login for a host, or say what is there instead."""
    found = find(name, site)
    if found is None:
        held = [a.site for a in load(name)]
        raise AuthError(
            "no login saved for %s in profile %r. %s Use `dots auth list %s` "
            "to see the ones there are, or `dots auth add %s <login> <password>`."
            % (site_key(site) or site, name,
               ("Saved for: %s." % ", ".join(held)) if held
               else "That profile has no logins yet.",
               name, name))
    return found


def add(name: str, site: str, login: str, password: str, *,
        login_selector: str = "", password_selector: str = "",
        notes: str = "") -> Auth:
    """Save a login, or replace the one already there for that host.

    A second save for the same host replaces rather than adding: two passwords
    for one site is a state `find` cannot answer, and the newest is what
    somebody typing it meant.
    """
    key = site_key(site)
    if not key:
        raise AuthError("that is not a site: %r. A host is enough - "
                        "example.com, or a full URL." % (site or ""))
    if not (login or password):
        raise AuthError("a login needs a login or a password")
    if not profiles.exists(name):
        raise profiles.UnknownProfile("no profile called %r." % name)
    held = load(name)
    kept = [a for a in held if a.site != key]
    stamp = profiles.now()
    created = stamp
    for old in held:
        if old.site == key:
            created = old.created or stamp
    fresh = Auth(site=key, login=login, password=password,
                 login_selector=login_selector,
                 password_selector=password_selector, notes=notes,
                 created=created, updated=stamp)
    kept.append(fresh)
    save(name, kept)
    return fresh


def remove(name: str, site: str) -> Auth:
    """Forget one login, or say there was nothing to forget."""
    key = site_key(site)
    held = load(name)
    kept = [a for a in held if a.site != key]
    if len(kept) == len(held):
        raise AuthError("no login saved for %s in profile %r."
                        % (key or site, name))
    save(name, kept)
    return next(a for a in held if a.site == key)


def mask(secret: str) -> str:
    """A password as something that can be recognised but not read.

    Four dots for anything at all, so the shape of the answer does not leak the
    length. Used by `dots auth list` and by the agent's `auth_list`, and by
    nothing that has to compare - a real comparison reads the record.
    """
    return "...." if secret else ""


def describe_rows(name: str) -> list[str]:
    """Lines for `dots auth list`: one per site, with no password in them."""
    held = load(name)
    if not held:
        return []
    return ["%-40s %-24s %s" % (a.site, a.login or "-", mask(a.password))
            for a in held]