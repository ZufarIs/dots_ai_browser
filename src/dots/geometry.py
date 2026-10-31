"""The device a profile claims to be: the geometry it pins, and the check on it.

A seed draws a whole person: a screen, a GPU, a set of cores, a set of codecs.
That person is coherent by construction - the engine's sampler conditions the
screen on the GPU class and the cores on the same class - but it is coherent
with ITSELF, not with the browser that has to carry it. Two things then decide
what a site actually sees, and neither of them is the seed:

  * the geometry the engine is told (`zoom.stealth.screen.*`), and
  * the geometry the machine underneath happens to have.

⛔ AND THE HOST'S TEXT SCALE IS THE THIRD THING, AND IT IS THE ONE THAT LEAKS.
The engine writes the device pixel ratio into `layout.css.devPixelsPerPx` as a
STRING, and the engine multiplies it by the scale of the machine's text - so
what a page reads as `devicePixelRatio` is the profile's number times the host's
number. Measured here, with a profile that declares 1.0:

    devicePixelRatio on the page   1.1538461538461537
    the profile declares           1.0

That is the worst kind of seam: two numbers a script can divide, and the
quotient is not a device anybody owns. The engine's own text scale is set the
same way - as the string "1.0" - and a string is not a number to the pref it
is written to, so it is ignored and the host's scale stands.

So this module does two things, and they are the two halves of one repair:

  * `neutral_prefs()` - the pref that takes the host's text scale out of the
    ratio. It is a NUMBER (a hundred, for a hundred per cent), because a string
    is ignored, and the measurement is on the other side of that: with the
    number the page reads exactly the ratio the profile declares, and without
    it the page reads the host's.

  * `check()` - the measurement itself. The engine is told a geometry; this
    reads back what a page sees and says whether the two are the same thing.
    A profile that says 1920x1080 and a browser that answers 1678 wide is a
    browser nobody believes, and the point of measuring is that the belief is
    checked rather than assumed.

The vocabulary of pins belongs to the engine, not here: `invisible_core`
refuses a key it does not know and says which keys it does, and that answer is
what this module passes on. A list of keys written down here would be a second
vocabulary, and it would be wrong the day the engine grows one.
"""
from __future__ import annotations

from typing import Any, Iterable, Mapping, Optional

from invisible_core import get_default_stealth_prefs

from invisible_playwright_mcp.quiet import swallow


class GeometryError(Exception):
    """A device geometry that cannot be built, or a browser that is not it."""


#: The text scale the host is left at: a hundred per cent, written as the
#: NUMBER the pref is read as.
#:
#: ⛔ AND IT IS A NUMBER, NOT A STRING, AND THAT IS THE WHOLE POINT. The
#: engine's own pref is written as the string "1.0" and is therefore ignored:
#: the pref wants a number, a string is not one, and what the page reads as
#: `devicePixelRatio` is then the host's own text scale (measured here:
#: 1.1538461538461537) multiplied into the profile's ratio. Measured with the
#: number: 100 gives exactly 1.0, 120 gives 1.2, 125 gives 1.25, 150 gives
#: 1.5, 200 gives 2.0.
#:
#: The scale is not the engine's alone - the browser also snaps it to the
#: grid its fonts live on, so 115 comes out as 1.1538461538461537 rather than
#: 1.15. That is why `check()` compares the ratio with a tolerance and not
#: for equality.
NEUTRAL_TEXT_SCALE = 100

#: How far the measured ratio may sit from the declared one and still be the
#: same device. A hundredth, because the font grid moves it by less than that
#: (1.15 comes out as 1.1538) and because a real device does not come out of
#: a sampler at a rounder number than this.
DPR_TOLERANCE = 0.01

#: What a page is asked, so that what it answers is a fact and not an
#: assumption. The names are the ones a page uses for them.
PROBE = """() => ({
  innerWidth: window.innerWidth,
  innerHeight: window.innerHeight,
  outerWidth: window.outerWidth,
  outerHeight: window.outerHeight,
  devicePixelRatio: window.devicePixelRatio,
  screenWidth: screen.width,
  screenHeight: screen.height,
  availWidth: screen.availWidth,
  availHeight: screen.availHeight,
})"""


def neutral_prefs() -> dict[str, int]:
    """The prefs that take the host's own text scale out of the ratio.

    Returned as a dict because that is what the engine's caller overlay
    takes, and it is applied LAST - after the engine has written its own
    value - so it is this one that stands.
    """
    return {"ui.textScaleFactor": NEUTRAL_TEXT_SCALE}


def parse_pin(values: Iterable[str]) -> dict[str, Any]:
    """Turn `screen.width=1920` lines into the dict the engine takes.

    The engine's pins are flat and named `group.field`, and the whole list of
    them belongs to the engine; what is decided here is how a PERSON writes
    one down. A comma, a space or a newline separates them, so a line typed
    into a single box in the launcher works the same as a flag repeated on a
    command line.
    """
    out: dict[str, Any] = {}
    for raw in values:
        for piece in str(raw).replace(",", " ").replace("\n", " ").split():
            if not piece:
                continue
            if "=" not in piece:
                raise GeometryError(
                    "%r is not a pin. A pin is group.field=value, for example "
                    "screen.width=1920." % piece)
            key, _, value = piece.partition("=")
            key = key.strip()
            if not key:
                raise GeometryError("%r has no name in front of the =." % piece)
            out[key] = _number(value.strip(), piece)
    return out


def format_pin(pin: Mapping[str, Any]) -> str:
    """A pin dict as the line a person reads and types back."""
    if not pin:
        return ""
    return ", ".join("%s=%s" % (key, pin[key])
                     for key in sorted(pin))


def _number(text: str, whole: str) -> Any:
    """The value of a pin: a whole number where it can be, a fraction where it
    has to be.

    `1` stays `1` and `1.25` stays `1.25`, because the engine writes both
    into prefs and a float that is really an integer is written as "1.0" -
    which is a different pref value to the engine that reads it.
    """
    try:
        if "." in text or "e" in text.lower():
            return float(text)
        return int(text)
    except ValueError:
        raise GeometryError("%r is not a number: %r" % (whole, text)) from None


def check_pin(pin: Optional[Mapping[str, Any]]) -> dict[str, Any]:
    """The pin as the engine will take it, or refuse.

    The vocabulary is the engine's: it is asked, and its refusal is passed on
    with its own wording, because it is the one that knows which fields exist
    and what they are called. A key written down here would be a second
    vocabulary and a second answer.
    """
    if not pin:
        return {}
    if not isinstance(pin, Mapping):
        raise GeometryError("a pin is a set of names and values, not %r."
                            % type(pin).__name__)
    clean: dict[str, Any] = {}
    for key, value in dict(pin).items():
        name = str(key).strip()
        if not name:
            raise GeometryError("a pin has a name and a value.")
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise GeometryError("the value of %s must be a number, not %r."
                                % (name, value))
        clean[name] = value
    try:
        get_default_stealth_prefs(seed=0, pin=clean)
    except ValueError as exc:
        raise GeometryError(str(exc)) from None
    return clean


def declared(seed: Optional[int], pin: Optional[Mapping[str, Any]] = None) -> dict[str, Any]:
    """The geometry the engine is told, read back out of the prefs it is given.

    Read out of the PREFS rather than out of the sampler, because the prefs
    are what the engine ends up with: the last word on what the browser is
    told belongs to the thing that tells it.
    """
    prefs = get_default_stealth_prefs(seed=int(seed or 0), pin=dict(pin or {}) or None)
    return {
        "screen_width": int(prefs["zoom.stealth.screen.width"]),
        "screen_height": int(prefs["zoom.stealth.screen.height"]),
        "taskbar_px": int(prefs["zoom.stealth.screen.taskbar_px"]),
        "chrome_w": int(prefs["zoom.stealth.screen.chrome_w"]),
        "chrome_h": int(prefs["zoom.stealth.screen.chrome_h"]),
        "dpr": float(prefs["layout.css.devPixelsPerPx"]),
    }


def expected(declared_geometry: Mapping[str, Any]) -> dict[str, Any]:
    """What a page must answer for this device to be the one declared.

    Measured on the engine, not assumed: the outer window is the screen
    itself, the inner one is the outer minus the frame, the outer height is
    the screen minus the taskbar and the inner height is that minus the
    frame. A page that answered anything else would be a page describing a
    different machine.
    """
    width = int(declared_geometry["screen_width"])
    height = int(declared_geometry["screen_height"])
    taskbar = int(declared_geometry["taskbar_px"])
    chrome_w = int(declared_geometry["chrome_w"])
    chrome_h = int(declared_geometry["chrome_h"])
    outer_h = height - taskbar
    return {
        "innerWidth": width - chrome_w,
        "innerHeight": outer_h - chrome_h,
        "outerWidth": width,
        "outerHeight": outer_h,
        "screenWidth": width,
        "screenHeight": height,
        "availWidth": width,
        "availHeight": outer_h,
        "devicePixelRatio": float(declared_geometry["dpr"]),
    }


async def measure(session) -> dict[str, Any]:
    """What a page answers, read from a page of this browser.

    A page of its own, opened for the reading and closed again: the answer is
    about the BROWSER, not about whatever site happens to be open, and a site
    that moved its own window would otherwise be able to change what this
    says.
    """
    page = await session.new_page()
    try:
        with swallow("a page that cannot be opened cannot be measured"):
            await page.goto("about:blank")
        return dict(await page.evaluate(PROBE))
    finally:
        with swallow("the measuring page is closed on the way out"):
            await page.close()


def differences(wanted: Mapping[str, Any], live: Mapping[str, Any]) -> list[str]:
    """The ways the browser is not the device it was told to be.

    An empty list means the two are the same thing. The ratio is compared
    with a tolerance and the rest exactly: the sizes are whole pixels and a
    whole pixel of disagreement is a disagreement.
    """
    out: list[str] = []
    for key, value in wanted.items():
        if key not in live:
            out.append("the page did not answer %s" % key)
            continue
        seen = live[key]
        if key == "devicePixelRatio":
            allowed = DPR_TOLERANCE * max(1.0, abs(float(value)))
            if abs(float(seen) - float(value)) > allowed:
                out.append("devicePixelRatio is %s, the profile declares %s"
                           % (seen, value))
            continue
        if seen != value:
            out.append("%s is %s, the profile declares %s" % (key, seen, value))
    return out


def report(wanted: Mapping[str, Any], live: Mapping[str, Any]) -> str:
    """One line for a person: the device, and whether the browser is it."""
    problems = differences(wanted, live)
    if not problems:
        return ("the browser is the device the profile declares "
                "(%dx%d, ratio %s)"
                % (wanted.get("screenWidth"), wanted.get("screenHeight"),
                   wanted.get("devicePixelRatio")))
    return ("the browser is NOT the device the profile declares:\n  "
            + "\n  ".join(problems))
