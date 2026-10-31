"""The browser engine, checked once per machine instead of once per process.

⛔ THE PACKAGE PREPARES THE ENGINE IN EVERY PROCESS, AND ON THIS MACHINE THAT
IS NOT A CHEAP THING. The core re-hashes the whole 250 MB engine on every
cache hit - measured here: NINETEEN SECONDS - and it does that once in the
interface and once again in every browser server the interface spawns. So:

    one profile   two preparations   ~39 seconds before the port opens
    two profiles  four preparations   ~80 seconds before the port opens

Measured, with two profiles: both servers sat in that step for eighty
seconds and neither of them opened its port.

The package states the rule it is living by, in its own words: "the interface
runs it to completion before spawning anything, so there is exactly one caller
per process and the race cannot start". The rule holds while there is one
process. Two profiles are two processes, and the second caller's extraction
deletes the first one's temporary tree - the core names it `.tmp-<tag>-<pid>`
and removes it at the start of every download.

So the engine is checked HERE, once, and the path is handed to every server
with `--binary`, which is the package's own supported way of saying "this
binary is already here, do not fetch it". A server given a binary returns from
`Engine.run()` without touching it.

⛔ AND THE CHECK IS NOT SKIPPED, IT IS DONE ONCE. The stamp below says which
engine was checked, for which build of the core, and what the file looked like
when it was checked. A core that updates, an engine that was replaced, a file
that changed size or date - every one of them makes the stamp wrong, and the
engine is checked again. What is gone is the SECOND check of the same file in
the same minute, which is the one that was costing the minute.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Optional

from invisible_playwright_mcp import storage


def stamp_path() -> Path:
    """Where the record of the last check is kept."""
    return storage.home() / "engine.stamp.json"


def _version() -> str:
    """The core's build, which changes when the engine changes."""
    try:
        import invisible_playwright

        return str(invisible_playwright.BINARY_VERSION)
    except Exception:
        return ""


def _checked_before() -> Optional[str]:
    """The engine this machine has already checked, or None.

    The stamp is compared against the file as it is NOW: the same path, the
    same size, the same minute it was last written. An engine that was
    replaced under the same name has a different size or date, and the stamp
    stops being true.
    """
    blob = storage.read_json(stamp_path())
    if not isinstance(blob, dict):
        return None
    path = str(blob.get("path") or "")
    if not path or str(blob.get("version") or "") != _version():
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    if int(blob.get("size") or -1) != stat.st_size:
        return None
    if int(blob.get("mtime") or -1) != int(stat.st_mtime):
        return None
    return path


def _remember(path: str) -> None:
    """Write down that this engine is checked."""
    try:
        stat = os.stat(path)
    except OSError:
        return
    blob = {
        "path": path,
        "version": _version(),
        "size": stat.st_size,
        "mtime": int(stat.st_mtime),
        "at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    try:
        storage.write_atomically(stamp_path(),
                                 json.dumps(blob, indent=2).encode("utf-8"))
    except OSError:
        # A stamp that cannot be written costs the next start nineteen
        # seconds and nothing else.
        pass


def checked_path() -> Optional[str]:
    """The engine this machine has already checked, or None.

    ⛔ IT CHECKS NOTHING. It only answers "is there a stamp, and does it
    still describe the file on disk". The work - the nineteen seconds of
    hashing - is `prepared_path`'s, and it is somebody's job but not this
    function's.

    That separation matters for the reason the interface asks the question at
    all: a machine that has never run dots must not be made to DOWNLOAD a
    250 MB engine because a command line was typed. So the interface asks
    "is it already checked?" and, if the answer is no, it passes nothing and
    the package prepares the engine in its own way, saying what it is doing.
    """
    return _checked_before()


def prepared_path() -> Optional[str]:
    """The engine's path, checked once per machine. None when there is none.

    A failure here is not this module's to report: the package prepares the
    engine itself and says so in words a person can act on, and a `dots` that
    has no engine is a `dots` that says why.
    """
    known = _checked_before()
    if known:
        return known

    from invisible_playwright_mcp.engine import Engine

    class _Said:
        """The download, said out loud - in words, not in bytes."""

        def status(self, phase: str) -> None:
            print("engine  %s..." % phase, flush=True)

        def progress(self, done: int, total: int) -> None:
            if total:
                print("engine  %d%% of %d MB"
                      % (done * 100 // total, total >> 20), flush=True)

    engine = Engine(listener=_Said())
    path = engine.run()
    if not path:
        return None
    _remember(path)
    return path
