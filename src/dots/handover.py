"""A handback: the agent stops and asks, and the record of what it asked.

Some pages need a person and cannot be talked out of it. A code from an
authenticator app is the obvious one: six digits that exist on a phone in
somebody's pocket. So do a payment confirmation, a bank SMS, a CAPTCHA the
engine will not pass, a password the person has not saved here, and every
site that asks for one of those behind a login.

The agent's answer to that is NOT to try harder and NOT to report the task
impossible. It is to stop, say what is needed, and wait - and the chat is
already the channel for it: a turn ends with a question, the person types the
answer, and the next turn carries on from where it stopped. Nothing has to be
built for the reply.

What is built here is the RECORD of the question, because that is the half
that was missing:

    <HOME>/profiles/<name>/handover.json

The launcher reads it and says so on screen, `dots handover` prints it from a
terminal, and the browser server's `dots_ask_operator` writes it. Without it a
person reading the chat sees the question and the log shows nothing, which is
one window too few for the one moment when the run is deliberately doing
nothing.

⛔ AND IT IS ONE, NOT A QUEUE. A handback is one question about one page. A
second one replaces the first rather than joining it, because an agent that
has asked twice is an agent that did not wait the first time, and a list of
questions is a list nobody reads.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Mapping, Optional

from invisible_playwright_mcp import storage

from . import profiles

#: The file one profile's outstanding question is in, inside that profile.
FILE_NAME = "handover.json"


def path_for(name: str) -> Path:
    """Where a profile's outstanding question is kept."""
    return profiles.home_directory(name) / FILE_NAME


def current(name: str) -> Optional[dict[str, Any]]:
    """The question that has not been answered, or None.

    A file that will not parse is no question rather than an error: the same
    rule the package applies to a session file, and the same reason - a saved
    thing that cannot be read is exactly as usable as one that was never
    saved, and it must not stop a browser from opening.
    """
    blob = storage.read_json(path_for(name))
    if not isinstance(blob, Mapping) or not str(blob.get("question") or "").strip():
        return None
    return dict(blob)


def ask(name: str, question: str, *, need: str = "", page: str = "") -> dict[str, Any]:
    """Write down what the agent is waiting for, and answer with the record.

    `need` is the thing itself - "the six digits from your authenticator app"
    - kept beside the question because the difference between a question and
    an instruction is the difference between the person finding their phone
    and the person reading "what do you need?". `page` is where the browser
    is, so the question can be answered with the window already in front.

    The time goes in here rather than being read back later: a question from
    yesterday is not a question the person is still standing in front of, and
    the launcher says when it was asked.
    """
    if not profiles.exists(name):
        raise profiles.UnknownProfile("no profile called %r." % name)
    said = (question or "").strip()
    if not said:
        raise ValueError("a handback needs a question")
    record = {
        "profile": name,
        "asked": profiles.now(),
        "question": said,
        "need": (need or "").strip(),
        "page": (page or "").strip(),
    }
    where = path_for(name)
    profiles.write_json(where, record)
    return record


def clear(name: str) -> bool:
    """Forget the outstanding question. Answers whether there was one."""
    record = current(name)
    storage.erase(path_for(name))
    return record is not None


def describe(record: Mapping[str, Any]) -> str:
    """The record as a few lines for a person reading a terminal."""
    out = ["%s asked %s: %s" % (record.get("profile", "?"),
                                 record.get("asked", "?"),
                                 record.get("question", ""))]
    if record.get("need"):
        out.append("  needs: %s" % record["need"])
    if record.get("page"):
        out.append("  on:    %s" % record["page"])
    return "\n".join(out)
