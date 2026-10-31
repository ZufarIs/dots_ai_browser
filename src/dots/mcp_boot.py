"""The browser server `dots` spawns, with this build's tools on it.

The interface spawns one MCP server process per conversation, and the pinned
package builds the command for it in `link.py`:

    StdioServerParameters(command=sys.executable,
                          args=["-m", "invisible_playwright_mcp"], ...)

That command is what reaches the model: everything `agent.py` knows the model
can call is what that process answers `list_tools` with. So the place to add a
tool is not this interface but the server it starts, and `cli.py` points the
`Link` here instead of at the package's own `__main__`.

⛔ AND IT GOES THROUGH THE PACKAGE'S OWN COMMAND GROUP, NOT PAST IT. The child
is not a plain MCP server: the group is where it reads `.env`, and where
`runner.forget_key` takes the model key back out of its own environment. That
half is a security property and not a formality - `runner.child_env` hands the
child a clean environment, and the child putting the key straight back into
it was a measured leak into the browser's own environment. Calling
`mcp.server.main()` directly would have kept the tools and dropped the key
stripping, so this calls the same group `python -m invisible_playwright_mcp`
calls, with the same no-subcommand invocation, and only inserts the tool
registration in front of it.

⛔ AND IT IS A DIFFERENT COMMAND, NOT A PATCH ON THE PACKAGE'S. The package
writes into `site-packages` on somebody's machine; a `dots` upgrade must not
leave a modification there that the next reinstall silently erases. This
module imports the package, adds what is ours to the server object the package
already built, and calls the package's own group. One line is changed
(`Link._params`) and it is changed here, in our own process, at runtime.

What the group does afterwards is untouched: it reads `.env`, drops the key,
and calls `_serve`, which calls `mcp.server.main()`. `dots.tools` has already
registered `dots_profile_*` and `dots_auth_*` on the same `FastMCP` instance
`browser_open` is on, and appended a paragraph to the instructions that travel
with the handshake.

⛔ AND THE SESSIONS ARE OURS TOO, IN ONE PLACE. The browser this server opens
is a device: the geometry it claims, and the text scale of the machine it
runs on, are both decided before the engine starts. `dots.device` replaces the
factory the package opens sessions with, so every path that opens one - the
package's `browser_open` and ours - carries the same device and is measured
against the same declaration. See that module for why it is a factory and not
a browser.

Running this module by hand works, and is how to see what a client would see:

    python -m dots.mcp_boot

It is an MCP server over stdio like any other: it speaks the protocol on
stdin/stdout and prints its one waiting line on stderr.
"""
from __future__ import annotations

#: What this process says it is, in the handshake and in a usage line. The
#: package's own `__main__` runs the same group under the package's name; a
#: server that answers `initialize` with a name nobody started is confusing a
#: client that logs it.
PROGRAM = "dots-mcp"


def main() -> None:
    """Register this build's tools, then serve, through the package's group."""
    from invisible_playwright_mcp.mcp import server

    from . import device, tools

    # Before the group, and never instead of it: the group builds the served
    # server by importing `mcp.server` again, and that import hands back the
    # module object already in `sys.modules` - the one whose `mcp` the tools
    # above were registered on.
    tools.register(server)

    # And the sessions it opens carry the device the profile claims to be,
    # and are measured against it. Before the group for the same reason: the
    # factory is replaced on the `Work` the server has already built, and the
    # group is what starts serving from it.
    device.install(server.work)

    from invisible_playwright_mcp.cli import main as group

    # No subcommand: that is what makes the group answer with the browser
    # server rather than with a usage line, and it is the branch that also
    # strips the model key out of this process's own environment.
    group(args=[], prog_name=PROGRAM)


if __name__ == "__main__":
    main()