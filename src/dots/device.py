"""The sessions this process opens: the device they are told to be, and the check.

The pinned package opens a browser through a factory - `Work.__init__` takes
one, and the server builds its `Work` with the default - and that factory is
where a session is MADE. This module replaces it with one that does two
things the default does not:

  * it carries the device the profile claims to be. A profile can pin the
    geometry it claims (`screen.width`, `screen.dpr`, `screen.chrome_w` and
    the rest), and the pin has to reach the engine, which draws the profile
    from a seed and nothing else. The package's planner has no word for a
    pin, so the pin travels the way the profile's other half does - by the
    browser directory it belongs to.

  * it takes the host's own text scale out of the ratio. The engine writes
    the device pixel ratio as a string and multiplies it by the scale of the
    machine underneath, so a profile that declares 1.0 is read by a page as
    1.1538461538461537 here. The pref that ends that is a NUMBER, and this
    is where the number is written - see `dots.geometry`.

And it measures what came out. The engine is told a geometry, a page is asked
what it sees, and the two are compared. The comparison is a REPORT and not a
refusal: a newer engine may model the geometry differently, and a browser
that will not open because a formula moved is worse than a browser that opens
and says what it is. The report goes to stderr, where the interface's log
shows it, and it is on the session, where a tool can read it.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Mapping, Optional

from invisible_playwright_mcp.mcp.session import StealthSession

from . import geometry
from . import profiles as registry


def same_directory(one: Any, other: Any) -> bool:
    """Whether two paths name the same directory.

    Compared by the name the operating system would answer to, because the
    two arrive from different places: one is a profile's own directory, built
    from the registry and resolved; the other is a path the package resolved
    on its way here. On Windows the same directory is written three ways -
    with a different case, with a short name, with a trailing separator - and
    a string comparison would call them three different directories.
    """
    try:
        left = os.path.normcase(str(Path(one).resolve()))
        right = os.path.normcase(str(Path(other).resolve()))
    except (OSError, TypeError, ValueError):
        return False
    return left == right


def pin_for_directory(directory: Optional[str]) -> dict:
    """The pins of the profile whose browser directory this is.

    By the directory and not by the name, because the directory is what
    reaches this process: the planner writes it into the launch settings and
    into the file that says who `main` was, and a session reopened with no
    arguments at all comes back with the same one. A profile that points at a
    directory of somebody else's has no pin here, and neither has a
    directory that belongs to no profile - both are answered with an empty
    pin, which is the profile the seed draws and no more.
    """
    if not directory:
        return {}
    try:
        held = registry.list_profiles()
    except Exception:
        return {}
    for profile in held:
        if same_directory(profile.path(), directory):
            return dict(profile.pin or {})
    return {}


def launch_settings(kwargs: Mapping[str, Any]) -> dict:
    """The launch kwargs with the device and the neutral scale added.

    The pins of the profile the launch is for, and the pref that stops the
    host's text scale from being multiplied into the ratio. Everything else
    is left exactly as it was: this is the package's own dictionary, and a key
    it put there is one it knows what to do with.
    """
    out = dict(kwargs)
    pin = pin_for_directory(out.get("profile_dir"))
    if pin:
        out["pin"] = pin
    # ⛔ THE NEUTRAL SCALE IS ADDED LAST, ON PURPOSE. The engine applies the
    # caller's prefs over its own, so a value written here is the one that
    # stands - and this is the one that has to stand, because the alternative
    # is a ratio the host decided.
    out["extra_prefs"] = {**(out.get("extra_prefs") or {}),
                           **geometry.neutral_prefs()}
    return out


class CheckedSession(StealthSession):
    """A session that knows what device it is, and says whether it is it."""

    #: What the measurement found. Empty until the session has started, and
    #: empty for good when the browser could not be measured at all.
    report: str = ""

    #: What the browser was TOLD to be, in the names a page uses. Empty until
    #: the session has started.
    wanted: dict = {}

    async def start(self) -> None:
        await super().start()
        self.report = await self._check()

    async def _check(self) -> str:
        """What the browser is, measured, against what it was told to be.

        A browser that cannot be measured is not a browser that failed: the
        page might not open, the engine might be busy, and neither of those
        says the browser is the wrong device. So a measurement that does not
        come back is an empty report, and the browser stands.
        """
        seed = self._kwargs.get("seed")
        pin = self._kwargs.get("pin") or {}
        if seed is None:
            # The engine drew a seed of its own and wrote it into the browser
            # directory, so there is nothing here to compare the browser with:
            # the declaration would be about somebody else's device.
            return ""
        try:
            self.wanted = geometry.expected(geometry.declared(seed, pin))
        except Exception:
            return ""
        try:
            live = await geometry.measure(self)
        except Exception:
            return ""
        problems = geometry.differences(self.wanted, live)
        if problems:
            say("device: " + "; ".join(problems))
        return geometry.report(self.wanted, live)


def factory(**kwargs: Any) -> CheckedSession:
    """A session, with the device the profile claims to be."""
    return CheckedSession(**launch_settings(kwargs))


def install(work) -> None:
    """Make the sessions this process opens carry the device and be measured.

    ⛔ THIS REPLACES A FACTORY, NOT A BROWSER. `Work.__init__` takes a
    `factory` and the server builds its `Work` with the default one, so the
    only way to reach it afterwards is the attribute it is kept in. What is
    replaced is what a session is MADE OF, and every path that opens one
    goes through it - the package's own `browser_open` and ours alike, which
    is the point: a device that is applied on one path and not the other is
    a device that is applied when it is convenient.
    """
    work._factory = factory


def say(line: str) -> None:
    """One line on stderr, where the interface's log shows it.

    stderr because stdout is the protocol channel the browser server speaks
    on, and a note printed there corrupts the stream.
    """
    try:
        print(line, file=sys.stderr, flush=True)
    except Exception:
        pass
