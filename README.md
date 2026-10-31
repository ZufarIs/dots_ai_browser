<h1 align="center">dots</h1>

<p align="center"><b>Every AI agent is a model and a browser.<br>You can swap the model with one flag. The browser is what the website sees.</b></p>

---

Windows, in PowerShell:

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
uvx --from git+https://github.com/feder-cr/dots dots --openrouter-key sk-or-...
```

Linux:

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.local/bin/env
uvx --from git+https://github.com/feder-cr/dots dots --openrouter-key sk-or-...
```

Then open **http://127.0.0.1:8765**. The conversation on the left, the browser on the right, live.

## It all comes down to the browser

When a web agent fails, the model is rarely why. The page never loaded, a
challenge appeared, the login expired, the click did not land. All of that
happens in the browser, before the model gets to think.

So dots is built around one:

- **A real Firefox engine, patched in C++.** The fingerprint is decided inside
  the engine, not painted over with JavaScript that a page can inspect.
- **One identity per seed.** Screen, fonts, GPU, timezone and language agree
  with each other, and `--seed` gives back the same person on every run.
- **A device that is the one it claims to be.** The screen, the ratio and the
  window frame are pinned per profile, the host's own text scale is kept out
  of the ratio, and the browser is measured against what it was told.
- **Nothing for a page to find.** No WebDriver flag, no DevTools protocol, no
  automation globals in the page.
- **A person's hands.** The pointer travels to what it clicks and keys are
  pressed one at a time, so every event the page receives is a trusted one.
- **A browser that remembers.** `--profile-dir` keeps logins and cookies from
  one run to the next.
- **Where it connects from is who it is.** With `--proxy`, the timezone and the
  language follow the exit.

The model is any model on OpenRouter, and `--model` changes it. Any other
OpenAI-compatible endpoint works the same way through a `.env` in the
directory you run from:

```env
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.openai.com/v1
OPENAI_MODEL=gpt-4o
```

OpenAI, Azure OpenAI, DeepSeek, Together, Groq, a local Ollama / llama.cpp /
vLLM server - any endpoint that exposes the Chat Completions API. The key
and the URL can also be set in the shell, and a value already exported
wins over the file. Copy `.env.example` to `.env` and edit it; precedence
is `--flag` > the shell > `.env` > the default.

The key is read from the project's `.env` **and** from
`%APPDATA%\invisible-playwright-mcp\.env`, which applies to every
directory — the project's own key wins, the personal one fills the gaps. So
`dots` finds its key from whatever folder you run it in, and a project with
its own `.env` still gets its own.

## What to ask it

> Go to `<paste the URL>`. One way, Milan to Lisbon, economy, one adult. Check
> every date from the 12th to the 16th of next month and read the cheapest fare
> for each day. If a date has no availability, say so. Do not guess a number.

## Profiles and the logins an agent may type

A browser directory, an exit and a fingerprint are one bundle — the same one
every time, for the same account. `dots` keeps them under a name:

```bash
dots profile add work --label "Work" --proxy http://user:pass@host:8080 --seed 4242
dots --profile work          # the interface, as that profile
```

Each profile owns a browser directory (cookies, logins, the seed), a working
folder the agent writes results into, an exit and a seed. Put
`DOTS_PROFILE=work` in `.env` and every run in that directory comes up as the
same person. `--profile` is only a shorthand: `--profile-dir` typed by hand
still wins, exactly as `--flag` beats `.env` everywhere else here.

A seed draws a whole person at once, and it cannot be changed without
changing the person. A profile can still say what screen that person has:

```bash
dots profile set work --pin screen.width=1920 --pin screen.dpr=1.25
```

The pins are the engine's own vocabulary, and the browser is measured against
them when it opens: what the engine was told and what a page answers are
compared, and a difference is printed in the log.

Both halves are in the interface, not only on the command line: the launcher
window has a **Device** field with the pins and a **Check** button, and the
browser pane of the web UI has the same — the device it claims, an **edit**
box for the pins, and a **check** that asks the open browser what it says it
is.

Two profiles can run at once, and they share nothing — not the browser, not
the cookies, not the conversation, not the agent's memory of the last task:

```bash
dots profile set work --port 8801
dots profile set play --port 8802
dots --profile work     # http://127.0.0.1:8801
dots --profile play     # http://127.0.0.1:8802
```

The port belongs to the profile for exactly that reason: two servers cannot
share one, and a number somebody has to remember is a number they will get
wrong. The GUI's **Start all profiles** button brings up one server per
profile, each on its own port, and tags every log line with the profile it
came from.

The agent can also read logins you have saved against a profile, and type them
into a form when a site asks:

```bash
dots auth add work shop.example.com alice 'the password'
```

It decides when to use them — `dots profile use` before a task about a
particular account, `dots auth get` when a page asks you to sign in. The
logins it can reach are the ones belonging to the profile the browser was
opened as, and no tool takes a profile argument, so it cannot read past that.
No command prints a password; `dots auth list` shows `....` instead.

Passwords are stored as they are typed, in a `600` file beside the profile —
the same posture as the API key in `.env`, and no stronger claim than that.
The full details, including where everything lives on each platform, are in
`MANUAL.ru.md`.

## When a page needs a person

Some pages cannot be talked past: a code from an authenticator app, an SMS, a
payment confirmation, a CAPTCHA, a password nobody saved here. The agent stops
and asks rather than trying harder or reporting the task impossible — it calls
`dots_ask_operator`, writes the question down, and ends its turn. Your next
message in the chat is the answer, and it carries on from where it stopped
with the page, the browser and the session untouched.

```
You: log into shop.example.com and read the order I placed yesterday
Agent: I need the six-digit code from your authenticator app for
       shop.example.com. The browser is waiting on the code page.
You: 481902
Agent: <continues>
```

Run the browser headed (`--headed`) and you can type the code into the window
yourself instead of sending it through the chat — the agent waits and does not
click while you are. The question is also readable outside the window:

```bash
dots handover            # what the agent is waiting on
dots handover --clear    # forget it
```

## Scrolling the live pane

The pane on the right is a **video** of the browser window, not the browser
itself, and the pinned package forwards no gesture from it to the page — so
the page you were watching could not be scrolled, and everything below the
fold was visible only to the agent.

`dots` forwards one: **the wheel over a picture scrolls the page in the
browser**. A real wheel event, so a site cannot tell it from a person's, and
the agent's own actions are serialised against it rather than racing it.

Wheel up and down, Shift+wheel sideways, and the wheel over the conversation
still scrolls the conversation.

## The same browser, over HTTP

The interface is a page for a person. An application that needs data out of a
site wants a door instead, and `dots serve` is that door - the same browser,
the same profile, the same engine, reached over HTTP:

```bash
dots serve --host 0.0.0.0 --port 8080 --api-key sk-...
```

The interface's own doors are closed and the API's are opened in their place.
That is deliberate: the interface can send an instruction and show the picture
of a browser that is logged in somewhere, and not one of those asks for a key.

```bash
curl -X POST http://127.0.0.1:8080/api/v1/run \
  -H 'X-Dots-Key: sk-...' -H 'Content-Type: application/json' \
  -d '{"task": "Go to example.com and read the three cheapest fares for the 12th to the 16th"}'
```

The answer comes back with the run, everything the agent did, and whatever it
is waiting on:

```json
{"ok": true, "id": "r1730000000000", "session": "api",
 "state": "done", "answer": "The 12th is 42 EUR, the 13th is 51...",
 "data": null, "handover": null,
 "events": [{"kind": "tool", "text": "browser_navigate ..."}]}
```

| Door | What it does |
|------|--------------|
| `GET /api/v1/health` | is it alive - the one door with no key on it |
| `GET /api/v1/status` | the model, the profile, the device, the waiting |
| `POST /api/v1/run` | a task, and the answer to it |
| `POST /api/v1/tasks` | a task, and an id to come back for |
| `GET /api/v1/tasks/{id}` | what became of that task |
| `GET /api/v1/tasks/{id}/events` | the same, as it happens (SSE) |
| `GET /api/v1/sessions` | the conversations this server holds |
| `POST /api/v1/sessions` | a new one, under a name of your own |
| `POST /api/v1/reset` | forget the transcript, keep the browser |
| `GET /api/v1/handover` | the question the agent is waiting on |
| `POST /api/v1/handover` | the answer to it, and the run carries on |
| `GET /api/v1/browser` | where the browsers are |
| `GET /api/v1/frame` | a picture of the browser window |
| `POST /api/v1/stop` | stop the run in a conversation |

`"session": "prices"` in the body keeps one browser between calls - the
cookies, the logins and the page it was on. Without it the server's own
conversation is used, which starts empty and never mixes with anybody else's.

`"json": true` asks for the answer as a JSON object, and `data` is it. A task
that is still going when the wait is over is answered with the id to come
back for, not with an error.

One profile is one browser directory, and Firefox will not open the same
directory twice - so the server runs one task at a time, and the browser
moves to whichever conversation the new task is in. The cookies stay (they
live in the profile's directory); the page that was open does not.

The key is asked for in `X-Dots-Key`, in `Authorization: Bearer ...`, or as
`?key=` in the query string. It is required the moment the server is not on
loopback, and `dots serve` refuses to start without one.

## The same browser, in your own assistant

Claude Code, Codex, Gemini CLI or any MCP client:
[invisible_playwright_mcp](https://github.com/feder-cr/invisible_playwright_mcp)
gives them this browser as a server. `dots` is its interface, and `dots --help`
lists every option.

## Windows GUI launcher

A native tkinter window for those who'd rather not type. It edits
`.env`, picks and edits profiles and their saved logins, starts and stops the
server and shows its log.

The recommended way to run it is via `uvx`, which provides a real
Python that the package needs at runtime:

```powershell
uvx --from git+https://github.com/feder-cr/dots dots-gui
```

Working on a checkout of your own, run the one from that checkout instead —
otherwise the command above answers with the released copy and the two drift
apart, which is the whole reason a `.venv` is there:

```powershell
uv tool install --force --editable .   # once, makes `dots-gui` mean THIS code
.\.venv\Scripts\dots-gui.exe            # or run it directly
```

If `dots` becomes slow to start, compile the bytecode once — `uv` does not do
it by default, and every import then compiles the modules again:

```powershell
python -m compileall -q .venv\Lib\site-packages
```

The pinned `invisible-playwright-mcp` package spawns its MCP server
as a `python -m invisible_playwright_mcp` child process (`Link._params`),
and a PyInstaller bootloader is not a Python interpreter. Building
`dots.exe` (the web UI server) with PyInstaller therefore does not
produce a working binary — the server starts and dies on the first
browser page request. The GUI's launcher behaviour reflects this:
in a frozen build `Start` is wired to call the sibling `dots.exe`,
but the call lands on a server that cannot fulfil the architecture.

What a frozen `dots.exe` is good for is the MCP-server mode (no
subcommand, `python -m invisible_playwright_mcp` directly) for use
with Claude Code, Codex or Gemini CLI. The web UI needs Python.

For the full Russian-language reference, see `MANUAL.ru.md`.

---

Not affiliated with OpenAI. MIT licensed.
