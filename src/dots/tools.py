"""The tools `dots` adds to the browser server: profiles and logins.

The pinned package gives the model two fixed browsers and no way to name a
third, which is the right rule for the package and the wrong rule for a person
who keeps more than one account. These tools are how the agent reaches the
registry in `dots.profiles` and the logins in `dots.auths`, and they are
registered onto the SAME FastMCP server as `browser_open` - so they travel the
same wire, appear in the same `list_tools`, and cost the model the same
1024-character budget as everything else.

⛔ NOT A THIRD BROWSER. `profile_use` closes a browser and opens it again as
the named profile, through `work.open` - the same call `browser_open` makes.
The two-browser rule the package holds is still true: there are still exactly
two live browsers at any moment, and switching profiles is a reopen rather
than a new name in a registry. Nothing here widens what the package decided.

⛔ AND THE ACTIVE PROFILE IS DECIDED BY THE BROWSER, NOT BY THE MODEL. The
logins a call may read are the logins of the profile the browser it is driving
was opened as, read back from `work`'s own record of what it launched. A model
cannot ask for another profile's vault, because the argument it could pass is
not a parameter - `get_auth` takes a site and nothing else. `profile_use` is
the only way to change which vault is in reach, and it changes which browser
you are in at the same time, which is the point.
"""
from __future__ import annotations

import json
from typing import Optional

from invisible_playwright_mcp.mcp import DEFAULT_BROWSER_ID, actions

from . import auths, device, geometry, handover, profiles

#: The variable the pinned package reads for `browser`, and the two values it
#: allows. Said here rather than imported from `server.py`, which builds it
#: with a pydantic `Annotated` for the schema; the values are the rule, the
#: annotation is how the package happens to publish it.
BROWSERS = (DEFAULT_BROWSER_ID, "support")

#: What the model is told about all of this, appended to the server's own
#: instructions. Sent once per conversation rather than once per tool, which is
#: where the package puts everything about what `main` and `support` are for.
INSTRUCTIONS = """Some tools reach saved work this server does not know about,
because a person set it up before the session started.

`dots profile list` answers what identities are saved - each with its own
browser directory, exit, seed and working folder. `dots profile use NAME`
closes the browser and opens it again as that profile, so the second call
afterwards needs no profile argument anywhere: the cookies, the logins the
browser kept, the fingerprint and the working folder all belong to that
profile from then on. Use it when a task is about a particular account, and say
in your answer which profile you worked as.

`dots auth list` answers which sites have a saved login, with the logins and no
passwords. `dots auth get SITE` answers one login and one password - read it
when a page asks you to sign in, type it with browser_type, and never print it,
never put it in your answer, and never send it anywhere but the form in front
of you. `dots auth signin SITE` does both in one call when the saved selectors
fit the page. `dots auth save SITE LOGIN PASSWORD` records a login the person
asked you to remember, and `dots auth forget SITE` deletes one.

These read the profile the CURRENT browser was opened as. Switching profiles
switches which logins are in reach, so open the right one before asking for a
login - `dots auth get` on the wrong profile says there is nothing saved
rather than handing you somebody else's.

Sometimes a page needs a PERSON and not a model, and the right answer to that
is never to try harder. Read this before you give up on anything.

WHAT COUNTS AS NEEDING A PERSON

  * a code from an authenticator app - the six digits that change every half
    minute and exist only on their phone;
  * an SMS or a push notification they have to confirm;
  * a payment, a transfer, or anything that says `3-D Secure`;
  * a CAPTCHA the page will not let you past;
  * a password nobody has saved here with dots_auth_save;
  * a captcha, a slider or a puzzle that wants a drag;
  * anything that asks them to confirm they are a person and not a browser.

THE ONE RULE: ASK ONCE, THEN STOP

Call `dots_ask_operator` with the question and what you need, and then
END YOUR TURN. Their next message is the answer, and you carry on from
where you stopped - the page, the browser and the session are all still
exactly as you left them.

  * Do NOT retry the page, click around looking for another way in, or call
    browser_navigate again. A second attempt is not diligence, it is the run
    spending the person's time.
  * Do NOT report the task as impossible. A task stopped at a code that
    arrived twenty seconds later is a task that finished; one reported
    impossible is a task that was thrown away.
  * Do NOT ask twice for the same thing. If they have not answered yet, they
    are not looking; say what you are waiting for and stop again.
  * Ask ONE question. Two questions is a form, and a person fills in one form
    at a time.

WHAT THE QUESTION HAS TO CONTAIN

Say what you need, not where you got stuck. "I need the six digits from your
authenticator app for example.com" is answerable in five seconds. "I could not
log in" is not, and it costs a round trip to find out why. Name the site, say
whether the browser is waiting on a page for it, and if the window is visible
say that they can type it in themselves.

IF THE BROWSER IS VISIBLE

When it was opened headed, the person can type into the window themselves, and
that is usually faster than typing a code into a chat. Say that you are
waiting and stop.

  * While you wait, do NOT click, do NOT navigate, do NOT reload and do NOT
    call any other tool. Every one of those cancels what they are doing - the
    field loses what they typed, the page goes back to the start, and the
    second factor resets itself.
  * Their next message is what tells you they are done. Re-read the page before
    you carry on rather than assuming they finished.

WHAT IS STILL YOUR JOB

Nothing here is permission to stop early. Retry a failed page load, try the
other selector, take the screenshot, look again. Hand back only what a person
has and you do not - never the work you have not tried yet.
"""

#: How the handback record is worded when the agent gets it wrong. Not the
#: record itself: the same sentence twice is a rule with two homes, and the
#: launcher shows one of them.
HANDOVER_SENTENCE = ("The question is written down and the launcher is showing "
                     "it. END YOUR TURN NOW.")


def _role(browser: Optional[str]) -> str:
    """`main` unless the caller named one, and a refusal if it named a third.

    The package's own rule, in the package's own words. `profile_use` is a
    reopen of one of the two rather than a new name, so the two-browser
    contract is not widened by anything in this file.
    """
    role = browser or DEFAULT_BROWSER_ID
    if role not in BROWSERS:
        raise ValueError("there are two browsers here: `main`, your own "
                         "identity, and `support`, the helper beside it. "
                         "There is no %r." % role)
    return role


def _which_profile(work, browser: Optional[str]) -> str:
    """The profile the browser this call is aimed at was opened as.

    Read back from `work`'s own record of the launch, by matching the profile
    directory it holds against the registry. Falls back to the profile this run
    was started as, and then to an empty string - which answers "there are no
    logins for this" rather than guessing at another profile's.

    A module function rather than a closure so that a tool registered outside
    `register` - `dots_ask_operator` - reaches the same answer by the same
    rule. Two copies of "whose vault is in reach" would be two chances for
    them to disagree about it.
    """
    role = browser or DEFAULT_BROWSER_ID
    launched = getattr(work, "_launched", {}) or {}
    settings = launched.get(role) or {}
    directory = settings.get("profile_dir")
    if directory:
        for profile in profiles.list_profiles():
            if device.same_directory(profile.path(), directory):
                return profile.name
    found = profiles.active()
    return found.name if found is not None else ""


async def _device_report(work, role: str) -> str:
    """What the measurement said about the browser that was just opened.

    Read through the package's own funnel, so a browser that is not open is
    a sentence rather than an exception, and the answer arrives as a string
    the tool can put into its reply.
    """
    async def _said(session) -> str:
        return getattr(session, "report", "") or ""

    try:
        return await work.acting(_said, role=role)
    except Exception:
        return "The device was not measured."


def register(server) -> None:
    """Put these tools on `server`'s FastMCP instance, and tell the model.

    `server` is the pinned package's `mcp.server` module - imported by
    `dots.mcp_boot`, in the browser server's own process, so the tools are
    registered on the same object `browser_open` is on and reach the same
    `Work`. Nothing here patches a method or reaches a private name to get at
    a browser: every call below goes through `work.open` or `work.acting`,
    which is what the package's own tools use.
    """
    mcp = server.mcp
    work = server.work

    from mcp.types import ToolAnnotations

    def says(title: str, *, read_only: bool = False, destructive: bool = False,
             open_world: bool = False) -> ToolAnnotations:
        """Both hints, always. The package's own rule (`mcp/server.py`): a
        client treating a missing `destructiveHint` as true would prompt for
        every one of these, and one that is absent here is not absent there."""
        return ToolAnnotations(title=title, readOnlyHint=read_only,
                               destructiveHint=destructive,
                               openWorldHint=open_world)

    # -- which profile is this ------------------------------------------------

    # The one thing the profile is decided by, closed over `work` so that
    # every tool below - and `dots_ask_operator`, registered beside them -
    # answers "whose is this" by the same rule rather than by a copy of it.
    which = lambda browser: _which_profile(work, browser)  # noqa: E731
    # -- profiles -------------------------------------------------------------

    @mcp.tool(annotations=says("List saved profiles", read_only=True))
    async def dots_profile_list() -> str:
        """The identities this installation has saved.

        Each line is a name, what it is called, where its browser directory is
        and which sites have a saved login. `*` marks the profile this run
        started as.

        Names, never secrets: a proxy is shown as a host, and no password is
        in this answer. Use dots_auth_list for the logins of one profile.
        """
        held = profiles.list_profiles()
        if not held:
            return ("No profiles are saved yet. Ask the person to run "
                    "`dots profile add NAME`, or carry on with browser_open - "
                    "it works without one, nothing is kept between opens.")
        here = profiles.active_name()
        lines = []
        for profile in held:
            mark = "*" if profile.name == here else " "
            sites = [a.site for a in auths.load(profile.name)]
            lines.append(
                "%s %-20s %s" % (mark, profile.name, profile.path())
                + ("\n    logins saved for: %s" % ", ".join(sites) if sites else "")
                + ("\n    out through %s" % profiles.proxy_host(profile.proxy)
                   if profile.proxy.strip() else "")
                + ("\n    working folder %s" % profile.work()))
        return "\n".join(lines)

    @mcp.tool(annotations=says("Work as a saved profile", destructive=True))
    async def dots_profile_use(name: str, browser: Optional[str] = None) -> str:
        """Close a browser and open it again as a saved profile.

        The browser you name - `main` unless you say otherwise - is closed and
        reopened with that profile's directory, exit and seed. Its cookies and
        the sessions the browser kept come back with it, and the logins of
        that profile become the ones dots_auth_get can reach.

        Use this when a task is about one particular account. Every tool after
        it works on that profile without naming it again. Say in your answer
        which profile you worked as.
        """
        profile = profiles.get(name)
        settings = profile.launch_settings()
        role = _role(browser)
        answer = await work.open(role, seed=settings.get("seed"),
                                 proxy=settings.get("proxy"),
                                 profile=settings.get("profile_dir"))
        profiles.touch(profile.name)
        said = ("%s\nThis browser is now %s. Its working folder is %s; put "
                "anything the task produces there."
                % (answer, profile.name, profile.work()))
        return said + "\n" + await _device_report(work, role)

    @mcp.tool(annotations=says("Check the browser's device", read_only=True))
    async def dots_profile_check(browser: Optional[str] = None) -> str:
        """Whether the open browser is the device its profile says it is.

        A browser is measured, not taken on trust: the engine is told a
        screen and a ratio, a page is asked what it sees, and the two are
        compared. A browser that answers something else is a browser a site
        can tell, and this is where that shows up.

        Read-only: it opens a page of its own to ask, and closes it again.
        """
        role = _role(browser)

        async def _measure(session) -> str:
            wanted = getattr(session, "wanted", None)
            if not wanted:
                return ("the device this browser was told is not known here - "
                        "it was opened without a seed, or by something other "
                        "than the device wrapper")
            live = await geometry.measure(session)
            return geometry.report(wanted, live)

        return await work.acting(_measure, role=role)

    @mcp.tool(annotations=says("The device a profile claims to be", read_only=True))
    async def dots_profile_device(name: Optional[str] = None,
                                  browser: Optional[str] = None) -> str:
        """What device a profile claims to be.

        The screen, the ratio, the window frame and the taskbar - the geometry
        the seed draws with this profile's pins on it - and the pins by name,
        because a pin nobody can see is a pin nobody can take off.

        With no name, the profile the named browser (main unless you say
        otherwise) was opened as. The answer is JSON, because the panel reads
        it as well as a person does.
        """
        role = _role(browser)
        wanted = (name or "").strip() or which(role)
        profile = profiles.find(wanted) if wanted else None
        if profile is None:
            return json.dumps({
                "error": "no profile is named, and no browser is open as one"})
        return json.dumps({
            "name": profile.name,
            "device": profile.device_line(),
            "pins": geometry.format_pin(profile.pin),
        })

    @mcp.tool(annotations=says("Save a profile", read_only=False, destructive=False))
    async def dots_profile_save(name: str, label: Optional[str] = None,
                                proxy: Optional[str] = None,
                                seed: Optional[int] = None,
                                pin: Optional[str] = None,
                                workdir: Optional[str] = None,
                                notes: Optional[str] = None) -> str:
        """Record an identity, or change one that is already recorded.

        Only the arguments you pass are changed; the rest of the profile is
        left exactly as it was. Leaving an argument out means "leave that
        field alone", not "clear it" - so a profile that has a proxy keeps it
        when you only change its label.

        `proxy` is `http://user:pass@host:port` or `socks5://host:port`, and
        it is written down with the password in it. `seed` is the identity: the
        same seed is the same fingerprint on any machine, so read it back with
        dots_profile_show rather than inventing one.

        `pin` is the device the browser claims to be - `screen.width=1920,
        screen.dpr=1.25` and the rest of the engine's own vocabulary - and it
        is how a profile that must keep its seed still says what screen it
        has. An empty value takes the pins off again.

        Changing the seed of a profile that has already run is refused at the
        next browser_open - the directory remembers who it was. Say so rather
        than retrying.
        """
        values: dict = {}
        for key, value in (("label", label), ("proxy", proxy),
                           ("seed", seed), ("workdir", workdir),
                           ("notes", notes)):
            if value is not None:
                values[key] = value
        if pin is not None:
            try:
                values["pin"] = geometry.parse_pin([pin])
            except geometry.GeometryError as exc:
                return "Not saved. %s" % exc
        if not values:
            return ("Nothing to change. Pass at least one of label, proxy, "
                    "seed, pin, workdir or notes.")
        profile = profiles.update(name, **values)
        return "Saved. %s\nbrowser directory %s\nworking folder %s" % (
            profile.describe(), profile.path(), profile.work())

    @mcp.tool(annotations=says("Scroll the page", read_only=False,
                               destructive=False))
    async def dots_scroll(dy: int, dx: int = 0,
                          browser: Optional[str] = None) -> str:
        """Scroll the page, the way a wheel does.

        `dy` is the vertical distance in pixels and `dx` the horizontal one;
        a negative `dy` scrolls up.

        A real wheel event, not a script that assigns `scrollTop`: a site can
        tell the difference, and one that is scrolled by a script is one that
        knows it is being driven. The pointer is moved to the middle of the
        page first, because a wheel is delivered to whatever is under it and
        the pointer is somewhere else entirely.

        The live pane in the interface scrolls the page this scrolls, so a
        person watching can move the page without taking the agent's hands
        off it.
        """
        role = _role(browser)

        async def _wheel(session) -> str:
            page = session.page()
            size = page.viewport_size or {"width": 800, "height": 600}
            await page.mouse.move(size["width"] // 2, size["height"] // 2)
            await page.mouse.wheel(dx, dy)
            return "scrolled by %d,%d" % (dx, dy)

        return await work.acting(_wheel, role=role)

    @mcp.tool(annotations=says("Show one profile", read_only=True))
    async def dots_profile_show(name: str) -> str:
        """Everything about one profile, except anybody's password.

        It carries an outstanding question, if there is one: when a run stopped
        to ask the person something, that question is still on file and this is
        where a new run finds out about it before it repeats the work.
        """
        profile = profiles.get(name)
        held = auths.load(name)
        waiting = handover.current(name)
        lines = [profile.describe(),
                 "browser directory  %s" % profile.path(),
                 "working folder     %s" % profile.work(),
                 "exit               %s" % (profiles.proxy_host(profile.proxy)
                                             or "this machine"),
                 "seed               %s" % (profile.seed if profile.seed is not None
                                          else "in the profile directory"),
                 "last used          %s" % (profile.last_used or "never"),
                 "logins saved for   %s" % (", ".join(a.site for a in held)
                                             or "nothing yet"),
                 "waiting on         %s" % (waiting["question"] if waiting
                                             else "nothing")]
        if profile.notes.strip():
            lines.append("notes              %s" % profile.notes.strip())
        return "\n".join(lines)

    # -- logins ---------------------------------------------------------------

    @mcp.tool(annotations=says("List saved logins", read_only=True))
    async def dots_auth_list(site: Optional[str] = None) -> str:
        """Which sites have a saved login, and what the login is.

        No passwords, in this answer or any other. `site` narrows the answer
        to one host, as a bare host (`example.com`) or as a URL; a login saved
        for `example.com` is also reported for `id.example.com`.

        These are the logins of the profile the current browser was opened as.
        """
        name = which(None)
        if not name:
            return ("No profile is in use, so there are no saved logins to "\
                    "reach. Open a browser as one with dots_profile_use, or "\
                    "ask the person to save a profile first.")
        held = auths.load(name)
        if site and site.strip():
            found = auths.find(name, site)
            held = [found] if found is not None else []
        if not held:
            return ("Profile %r has %s" % (name,
                    "no login saved for %s." % (auths.site_key(site) or site)
                    if site and site.strip() else "no saved logins."))
        lines = ["Profile %r:" % name]
        for record in held:
            lines.append("  %-40s %-24s %s" % (record.site, record.login or "-",
                                               auths.mask(record.password)))
            if record.login_selector or record.password_selector:
                lines.append("      selectors: login %r, password %r"
                             % (record.login_selector or "not saved",
                                record.password_selector or "not saved"))
            if record.notes.strip():
                lines.append("      %s" % record.notes.strip())
        return "\n".join(lines)

    @mcp.tool(annotations=says("Read a saved login", read_only=True))
    async def dots_auth_get(site: str) -> str:
        """One site's login and password, to type into a form.

        This answer is a secret. Type it with browser_type into the fields in
        front of you and do nothing else with it: never write it into your
        answer, never send it to a page that is not the one you are signing in
        to, and never repeat it back to the person.

        From the profile the current browser was opened as. If it says there
        is nothing saved, that is a fact about this profile - open the right
        one with dots_profile_use and ask again.
        """
        name = which(None)
        if not name:
            return ("No profile is in use, so there are no saved logins to "\
                    "read. Open a browser as one with dots_profile_use.")
        record = auths.get(name, site)
        return ("site     %s\nlogin    %s\npassword %s"
                % (record.site, record.login, record.password))

    @mcp.tool(annotations=says("Sign in with a saved login", destructive=True))
    async def dots_auth_signin(site: str, login_selector: str,
                               password_selector: str,
                               submit_selector: Optional[str] = None,
                               browser: Optional[str] = None) -> str:
        """Fill a login form from a saved login and submit it.

        `login_selector` and `password_selector` are the fields, exactly as
        browser_type takes them - from browser_snapshot on the page in front
        of you, or from the selectors saved with this login (dots_auth_list
        prints them when there are any).

        Every keystroke goes through the real keyboard, so this is the same
        thing a person does. `submit_selector` clicks the button when you give
        one; left out, nothing is submitted, which is what you want on a page
        with a second factor.
        """
        name = which(browser)
        if not name:
            raise ValueError("no profile is in use, so there are no saved "
                             "logins to sign in with")
        record = auths.get(name, site)
        role = _role(browser)

        async def _fill(session) -> str:
            done = []
            done.append(await actions.type_text(session, login_selector,
                                                record.login))
            done.append(await actions.type_text(session, password_selector,
                                                record.password))
            if submit_selector:
                done.append(await actions.click(session, submit_selector))
            return ("filled %s and %s for %s%s"
                    % (login_selector, password_selector, record.site,
                       ", and clicked %s" % submit_selector if submit_selector
                       else ". Nothing was submitted - press Enter or click the "
                            "button yourself."))

        return await work.acting(_fill, role=role)

    @mcp.tool(annotations=says("Save a login", read_only=False, destructive=False))
    async def dots_auth_save(site: str, login: str, password: str,
                             login_selector: Optional[str] = None,
                             password_selector: Optional[str] = None,
                             notes: Optional[str] = None) -> str:
        """Remember a login, for this profile, on that site.

        Save one when the person asked you to remember it, or told you the
        password themselves in order to sign in. Do not save a login nobody
        asked you to keep.

        The site is a host: `example.com` or any URL on it. A login already
        saved for that host is replaced. Saving writes the password in plain
        text to a file only this machine reads.
        """
        name = which(None)
        if not name:
            raise ValueError("no profile is in use, so there is nowhere to "
                             "save a login. Open a browser as a profile with "
                             "dots_profile_use first.")
        record = auths.add(name, site, login, password,
                           login_selector=login_selector or "",
                           password_selector=password_selector or "",
                           notes=notes or "")
        return "Saved the login for %s in profile %r." % (record.site, name)

    @mcp.tool(annotations=says("Forget a login", destructive=True))
    async def dots_auth_forget(site: str) -> str:
        """Delete one saved login. It cannot be brought back."""
        name = which(None)
        if not name:
            raise ValueError("no profile is in use, so there are no saved "
                             "logins to forget")
        record = auths.remove(name, site)
        return "Forgot the login for %s in profile %r." % (record.site, name)

    _register_handover(server, says, work)
    _teach_the_model(server)


def _register_handover(server, says, work) -> None:
    """The handback tool, and the one thing it needs the browser for.

    Split out from `register` only because it is the only tool here that asks
    the browser a question rather than acting on it: it reads the page the
    agent is looking at so the question it records says WHERE the answer has
    to be typed, which is the difference between a question and a puzzle.
    """
    mcp = server.mcp

    @mcp.tool(annotations=says("Hand back to the person", read_only=False,
                               destructive=False))
    async def dots_ask_operator(question: str, need: Optional[str] = None,
                                browser: Optional[str] = None) -> str:
        """Stop and ask the person for something only they can provide.

        For a code from an authenticator app, a payment confirmation, a bank
        SMS, a CAPTCHA, a password they have not saved here, or anything else
        that is one message away from a human.

        Write down what you need, then END YOUR TURN with the question. Do not
        retry the page, do not click around looking for another way in, and do
        not call the task impossible: their next message is the answer, and you
        carry on from where you stopped.

        `need` is the thing itself - "the six digits from your authenticator
        app" - and it is what the person reads first, so make it specific.

        If the browser is visible they can also type into the window
        themselves. Say so and wait; do not click or navigate while they do,
        because that cancels what they are doing.
        """
        name = _which_profile(work, browser)
        if not name:
            raise ValueError("no profile is in use, so there is nowhere to put "
                             "a question for the person. Open a browser as one "
                             "with dots_profile_use first.")
        page = ""
        try:
            rows = await work.listing()
            row = next((r for r in rows.get("browsers", [])
                        if r.get("id") == (browser or DEFAULT_BROWSER_ID)), None)
            page = (row or {}).get("url") or ""
        except Exception:
            # The page is a nicety in the record, not the point of it. A
            # question that cannot say where it is still asks the question.
            page = ""
        record = handover.ask(name, question, need=need or "", page=page)
        return ("%s\n\nAsk the person: %s%s\n\nThen stop. Their next message "
                "is the answer - carry on from where you stopped, and do not "
                "report the task as impossible."
                % (HANDOVER_SENTENCE,
                   record["question"],
                   (" You need %s." % record["need"]) if record["need"] else ""))


def _teach_the_model(server) -> None:
    """Append the paragraph above to what the server says about itself.

    The instructions are the one place the package puts things that are not
    about a single tool - what `support` is for, that whoever opens it closes
    it - and they reach the model once per conversation through the handshake.
    A tool nobody is told about is a tool the model does not reach for, so the
    sentence about signing in has to be in the same text as the sentence about
    the two browsers.

    `FastMCP.instructions` is a read-only property over
    `_mcp_server.instructions`, so it is the low-level server's attribute that
    is written. That is the same reach the pinned package itself makes two
    lines above this one when it sets `mcp._mcp_server.version`.
    """
    mcp = server.mcp
    held = getattr(mcp._mcp_server, "instructions", "") or ""
    if INSTRUCTIONS in held:
        return
    mcp._mcp_server.instructions = (held + "\n\n" + INSTRUCTIONS).strip()