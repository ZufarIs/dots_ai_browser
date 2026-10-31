"""The wheel over a live picture, carried to the browser that picture is of.

⛔ THE LIVE PANE IS A VIDEO, NOT A BROWSER. The page asks `/live/frame` for a
JPEG of the browser WINDOW - with the tab strip and the address bar in it,
because that is the only capture that shows the pointer - and the pane draws
that picture cropped to fit. Nothing in the package forwards a gesture from the
pane to the page: there is no click, no wheel, no key. So a person watching
the agent cannot scroll the page they are watching, and the part of it below
the fold is a part of it they cannot see at all.

This is that forwarding, and only that. The wheel over a screen goes to the
browser as a real wheel event, the page scrolls, and the next frame shows the
new position. The picture is still a picture: a click in it still clicks
nothing, and the agent still owns the browser.

Two seams, both narrow:

  * the PAGE, which the package assembles at import and serves whole. The
    script below is appended to it, and it finds the screen under the pointer
    the same way the package's own code finds the browser behind a request.
  * the ROUTE, one door at `/live/scroll`, which reaches the browser through
    the same `dots_scroll` tool an agent would call - so the wheel and the
    agent's own scrolling are the same action, and the package's lock on the
    browser serialises them against each other.

⛔ AND IT IS THE WHEEL, NOT A SCRIPT. `window.scrollBy` would have been one
line and would have scrolled the page; the engine's own wheel event is what a
person's wheel produces, and a page that is scrolled by a script is a page
that can tell it is being driven.
"""
from __future__ import annotations

#: Where the wheel goes. One door, like every other request the page makes.
SCROLL_PATH = "/live/scroll"

#: Where the device check goes. The same kind of door: the launcher asks the
#: server what the browser says it is, and the answer comes back the same way
#: the wheel's does.
DEVICE_PATH = "/live/device"

#: Where the device of a profile is read and written. The same door, both
#: ways: a GET answers what the profile claims, a POST writes the pins.
DEVICE_PROFILE_PATH = "/live/device/profile"

#: The bar the device is shown in, and the style it is shown with.
#:
#: ⛔ IT IS APPENDED TO THE PAGE, NOT WOVEN INTO IT. The package assembles
#: the page and owns every pixel of it; a change in that page is a change in
#: this file too, and the honest way to keep that visible is to put our
#: markup after theirs rather than to pretend it is theirs. The bar lives in
#: the browser pane's header, because that pane is about the browser and the
#: device is the browser.
DEVICE_CSS = """<style>
/* The device bar, in the page's own ladder: the same surfaces, the same
   quiet ink, the same pill buttons as the mode switch beside it. */
#dots-device{display:inline-flex;align-items:center;gap:var(--s2);
              font-size:var(--t-ui);color:var(--fg-2)}
#dots-device-text{white-space:nowrap;max-width:34ch;overflow:hidden;
                  text-overflow:ellipsis}
#dots-device button{height:var(--h-ctl);display:inline-flex;
                    align-items:center;font-family:var(--sans);
                    font-size:var(--t-ui);color:var(--fg-2);
                    background:var(--raised);border:1px solid var(--line-2);
                    padding:0 var(--s3);border-radius:var(--r-pill);
                    cursor:pointer;transition:background-color 120ms ease-out,
                    color 120ms ease-out}
#dots-device button:hover{background:var(--hover);color:var(--fg)}
#dots-device button:active{background:var(--top)}
#dots-device input{height:var(--h-ctl);font-family:var(--sans);
                   font-size:var(--t-ui);color:var(--fg);background:var(--base);
                   border:1px solid var(--line-2);border-radius:var(--r-pill);
                   padding:0 var(--s2);width:22ch;cursor:text;user-select:text}
</style>"""

#: The bar's own behaviour: read the device, let it be changed, and check the
#: browser that is open against it.
#:
#: `at()` is the package's helper for putting the conversation id into a
#: request - the one place that writes that parameter - and it is used here
#: rather than a second copy of the rule, for the same reason the wheel uses
#: it: a request without `?s=` is answered with the DEFAULT conversation
#: while the page shows another.
DEVICE_JS = """/* ---- dots: the device this browser claims to be ---- */
(function(){
  var bar = document.getElementById('chrome');
  if(!bar) return;
  var group = document.createElement('span');
  group.id = 'dots-device';
  group.innerHTML =
      '<span id="dots-device-text">device</span>'
    + '<input id="dots-device-pins" type="text" spellcheck="false"'
    + ' placeholder="screen.width=1920" hidden>'
    + '<button id="dots-device-edit" type="button"'
    + ' title="Name the screen and the ratio this profile claims">edit</button>'
    + '<button id="dots-device-save" type="button" hidden'
    + ' title="Save these pins into the profile">save</button>'
    + '<button id="dots-device-check" type="button"'
    + ' title="Ask the open browser what it says it is">check</button>';
  bar.insertBefore(group, bar.firstChild);

  var text = group.querySelector('#dots-device-text');
  var pins = group.querySelector('#dots-device-pins');
  var edit = group.querySelector('#dots-device-edit');
  var save = group.querySelector('#dots-device-save');
  var check = group.querySelector('#dots-device-check');
  var name = '';

  function door(path){
    /* ⛔ THE PACKAGE'S `at()`, AND NOT A COPY OF IT. It is the one place
       that writes the conversation id into a request, and a request without
       it is answered with the DEFAULT conversation while the page shows
       another. It is a global lexical binding - a top-level `const` - so it
       is visible here as a bare name and NOT as a property of `window`.
       That is also why this helper is not called `at`: a function of that
       name would shadow it, and the shadow would call itself. */
    return (typeof at === 'function') ? at(path) : path;
  }

  function load(){
    fetch(door('/live/device/profile'))
      .then(function(got){ return got.json(); })
      .then(function(body){
        if(!body || !body.ok){ text.textContent = 'device: unknown'; return; }
        var p = body.profile || {};
        name = p.name || '';
        text.textContent = 'device ' + (p.device || 'unknown');
        group.title = 'the device this profile claims: ' + (p.device || '');
        if(pins.value === '') pins.value = p.pins || '';
      })
      .catch(function(){ text.textContent = 'device: could not be read'; });
  }

  edit.addEventListener('click', function(){
    pins.hidden = !pins.hidden;
    save.hidden = pins.hidden;
    if(!pins.hidden) pins.focus();
  });

  save.addEventListener('click', function(){
    if(!name) return;
    fetch(door('/live/device/profile'), {method:'POST',
        headers:{'Content-Type':'application/json'},
        body: JSON.stringify({name:name, pin:pins.value})})
      .then(function(got){ return got.json(); })
      .then(function(body){
        if(body && body.ok){ load(); }
        else { text.textContent = 'device: ' + ((body && body.error) || 'not saved'); }
      })
      .catch(function(){ text.textContent = 'device: could not be saved'; });
  });

  check.addEventListener('click', function(){
    text.textContent = 'device: checking...';
    fetch(door('/live/device'))
      .then(function(got){ return got.json(); })
      .then(function(body){
        if(body && body.ok){ text.textContent = 'device: ' + (body.report || 'checked'); }
        else { text.textContent = 'device: ' + ((body && body.error) || 'not checked'); }
      })
      .catch(function(){ text.textContent = 'device: could not be checked'; });
  });

  load();
})();"""

#: The script appended to the page.
#:
#: `at()` is the package's own helper for adding the conversation id to a
#: request - the one place that writes that parameter - and it is used here
#: rather than a second copy of the rule, because a request without `?s=` is
#: answered with the DEFAULT conversation while the page shows another, and
#: the way that shows up is the picture on the right belonging to somebody
#: else's browser.
SCROLL_JS = """/* ---- dots: the wheel over a live picture goes to the browser ---- */
(function(){
  const path = (typeof at === 'function') ? at('/live/scroll') : '/live/scroll';
  document.addEventListener('wheel', (ev) => {
    const cell = ev.target && ev.target.closest ? ev.target.closest('.screen') : null;
    if(!cell) return;                       /* not over a picture: let the page have it */
    ev.preventDefault();                    /* the pane does not scroll; the browser does */
    let dx = ev.deltaX, dy = ev.deltaY;
    if(ev.shiftKey && !dx){ dx = dy; dy = 0; }   /* shift+wheel is sideways */
    dx = Math.round(dx); dy = Math.round(dy);
    if(!dx && !dy) return;
    fetch(path, {method:'POST', headers:{'Content-Type':'application/json'},
                 body: JSON.stringify({dx:dx, dy:dy, b:cell.dataset.id || ''})})
      .catch(()=>{});
  }, {passive:false});
})();"""


def page_with_scrolling(page: str) -> str:
    """The package's page with this build's script on the end.

    Appended rather than woven in: the package's script is one `<script>` with
    top-level bindings in a declared order, and changing the order is how a
    page breaks in a way that still draws. A separate `<script>` after it runs
    after the whole of it, and the top-level `const` and `let` it declares
    live in the global lexical environment - which is why `at()` is visible
    here at all.
    """
    script = "<script>%s</script>" % SCROLL_JS
    marker = "</body>"
    if marker not in page:
        return page + script
    return page.replace(marker, script + "\n" + marker, 1)


def page_with_the_device(page: str) -> str:
    """The package's page with this build's device bar on the end.

    The same rule as the wheel's script: appended, after the package's own,
    so that the page this build serves is the package's page plus two things
    this build adds - and a change in the package's page is a change in this
    file too, rather than a thing that quietly stops drawing.
    """
    block = DEVICE_CSS + "\n<script>%s</script>" % DEVICE_JS
    marker = "</body>"
    if marker not in page:
        return page + block
    return page.replace(marker, block + "\n" + marker, 1)


async def scroll(request):
    """The wheel over a live picture, carried to the browser it pictures.

    Answers with what happened rather than with nothing: a page that did not
    scroll because the browser was not open, and a page that did, are
    different facts and the caller is better off knowing which.
    """
    from starlette.responses import JSONResponse

    from invisible_playwright_mcp.link import answer_of
    from invisible_playwright_mcp.routes import which

    try:
        body = await request.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    try:
        dx = int(body.get("dx") or 0)
        dy = int(body.get("dy") or 0)
    except (TypeError, ValueError):
        return JSONResponse({"scrolled": False,
                             "error": "the scroll was not a number"},
                            status_code=400)
    if not dx and not dy:
        return JSONResponse({"scrolled": False, "error": "nothing to scroll by"})

    seen = await which(request)
    arguments: dict = {"dx": dx, "dy": dy}
    watching = str(body.get("b") or "").strip()
    if watching:
        arguments["browser"] = watching
    try:
        result = await seen.link.call("dots_scroll", arguments)
    except Exception as exc:
        return JSONResponse({"scrolled": False, "error": str(exc)[:200]},
                            status_code=503)
    text, failed = answer_of(result)
    if failed:
        return JSONResponse({"scrolled": False, "error": text[:200]},
                            status_code=503)
    return JSONResponse({"scrolled": True, "answer": text[:200]})


async def device(request):
    """What the open browser says it is, measured against what it was told.

    ⛔ THE QUESTION CROSSES TO THE SERVER PROCESS, WHERE THE BROWSER IS. The
    browser lives in the browser server and not in the process that serves
    this page, so the measurement is asked of the tool that owns it - the same
    tool an agent would call - and the answer comes back the same way the
    wheel's does.
    """
    from starlette.responses import JSONResponse

    from invisible_playwright_mcp.link import answer_of
    from invisible_playwright_mcp.routes import which

    try:
        seen = await which(request)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                             status_code=503)
    try:
        result = await seen.link.call("dots_profile_check", {})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                             status_code=503)
    text, failed = answer_of(result)
    if failed:
        return JSONResponse({"ok": False, "error": text[:200]},
                             status_code=503)
    return JSONResponse({"ok": True, "report": text[:400]})


async def device_profile(request):
    """What a profile claims, and the pins that say it.

    ⛔ READ AND WRITE THROUGH THE SERVER, WHERE THE PROFILE LIVES. The
    registry is the server's own home, and the server is the process that
    opened the browser - so the profile this answers about is the one the
    browser belongs to, and the write lands in the same file the launch
    reads.
    """
    import json

    from starlette.responses import JSONResponse

    from invisible_playwright_mcp.link import answer_of
    from invisible_playwright_mcp.routes import which

    try:
        seen = await which(request)
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                             status_code=503)

    if request.method == "POST":
        try:
            body = await request.json()
        except Exception:
            body = {}
        if not isinstance(body, dict):
            body = {}
        name = str(body.get("name") or "").strip()
        pin = str(body.get("pin") or "")
        if not name:
            return JSONResponse({"ok": False, "error": "no profile was named"},
                                 status_code=400)
        try:
            result = await seen.link.call("dots_profile_save",
                                          {"name": name, "pin": pin})
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)[:200]},
                                 status_code=503)
        text, failed = answer_of(result)
        if failed:
            return JSONResponse({"ok": False, "error": text[:200]},
                                 status_code=503)
        return JSONResponse({"ok": True, "answer": text[:400]})

    try:
        result = await seen.link.call("dots_profile_device", {})
    except Exception as exc:
        return JSONResponse({"ok": False, "error": str(exc)[:200]},
                             status_code=503)
    text, failed = answer_of(result)
    if failed:
        return JSONResponse({"ok": False, "error": text[:200]},
                             status_code=503)
    try:
        return JSONResponse({"ok": True, "profile": json.loads(text)})
    except Exception:
        return JSONResponse({"ok": True, "profile": {"device": text}})


def install(routes) -> None:
    """Put the scrolling and the device into the package's routes.

    Two things, and both are attributes of the module the interface builds
    its app from: the PAGE it serves, and the `build_app` it calls. The
    package's own `cli.ui` imports `build_app` from inside the function, so
    replacing the module attribute is enough - the import happens at call
    time and picks up this one.
    """
    routes.PAGE = page_with_the_device(page_with_scrolling(routes.PAGE))
    original = routes.build_app

    def build_app(sessions):
        app = original(sessions)
        app.add_route(SCROLL_PATH, scroll, methods=["POST"])
        app.add_route(DEVICE_PATH, device, methods=["GET"])
        app.add_route(DEVICE_PROFILE_PATH, device_profile, methods=["GET", "POST"])
        return app

    routes.build_app = build_app
