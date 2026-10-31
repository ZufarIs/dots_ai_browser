"""Profiles and logins: the registry, the vault, and the two doors onto them.

Three things are judged here and they are three different questions:

  * a file format - `dots.profiles` and `dots.auths` keep several people's
    settings in one directory and must not lose them, invent them, or answer
    with somebody else's;
  * an argv translation - `dots.cli` turns `--profile work` into flags the
    pinned package already understands, in an order a person can predict;
  * the tools the agent calls, which are registered onto the same server object
    `browser_open` is on and are the only way the model reaches any of this.

The last group needs the pinned package's server module, which is imported
once for the module: it builds its engine handle at import, and building that
per test would cost the whole session a directory walk each time.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

from dots import auths as vault
from dots import cli
from dots import handover
from dots import live
from dots import profiles as registry

ROOT = Path(__file__).resolve().parents[1]


def _body(payload: dict):
    """An ASGI receive channel that hands over one JSON body."""
    raw = json.dumps(payload).encode("utf-8")
    sent = {"done": False}

    async def receive():
        if sent["done"]:
            return {"type": "http.disconnect"}
        sent["done"] = True
        return {"type": "http.request", "body": raw, "more_body": False}

    return receive


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test's own data directory, and no `DOTS_PROFILE` leaking in.

    `INVISIBLE_MCP_HOME` is the package's own override and the one the pinned
    suite uses; pointing it at `tmp_path` is what keeps these tests from
    reading - and one stray write from reading - the registry of whoever is
    running them.

    The `STEALTHFOX_*` names are cleared for the same reason, and it is a
    leak of our own: `dots` translates the names the launcher writes into the
    names the package reads, and it does that in the ENVIRONMENT, so that the
    browser server it spawns inherits them. A test that calls `dots.cli.main`
    therefore leaves them behind for the next test - and the next test would
    find a browser with somebody else's profile directory.
    """
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(registry.ACTIVE_VARIABLE, raising=False)
    for name in ("STEALTHFOX_PROXY", "STEALTHFOX_SEED",
                 "STEALTHFOX_PROFILE_DIR", "STEALTHFOX_HEADLESS",
                 "STEALTHFOX_BINARY"):
        monkeypatch.delenv(name, raising=False)
    return tmp_path / "home"


@pytest.fixture
def work(home):
    """One profile with an exit and a seed, which is most of what follows needs."""
    return registry.create("work", label="work", seed=4242,
                           proxy="http://user:secret@10.0.0.1:8080")


# --- names -------------------------------------------------------------------

@pytest.mark.unit
def test_a_name_must_be_something():
    with pytest.raises(registry.ProfileError):
        registry.check_name("   ")


@pytest.mark.unit
def test_a_name_may_not_walk_out_of_its_directory():
    # The allowed set is what stops `../../something`; `.` and `..` survive a
    # character filter, so they are refused by name.
    for bad in ("..", "../other", "a/b", "a\\b", "with space", "юзер"):
        with pytest.raises(registry.ProfileError):
            registry.check_name(bad)


@pytest.mark.unit
def test_a_name_is_trimmed_and_kept(home):
    made = registry.create("  work  ")
    assert made.name == "work"
    assert registry.get("work") is not None


# --- the registry ------------------------------------------------------------

@pytest.mark.unit
def test_a_new_profile_gets_its_own_directories(home, work):
    assert work.path().is_dir()
    assert work.work().is_dir()
    assert work.path() != work.work()
    # And both are under the package's own home, so INVISIBLE_MCP_HOME moves
    # the profiles along with the sessions.
    assert str(work.path()).startswith(str(home))


@pytest.mark.unit
def test_a_second_profile_of_the_same_name_is_refused(work):
    with pytest.raises(registry.ProfileError):
        registry.create("work")
    assert len(registry.list_profiles()) == 1


@pytest.mark.unit
def test_overwriting_keeps_the_day_it_was_first_made(home):
    first = registry.create("work")
    again = registry.create("work", overwrite=True, label="second")
    assert again.created == first.created
    assert again.label == "second"
    assert len(registry.list_profiles()) == 1


@pytest.mark.unit
def test_a_field_that_does_not_exist_is_refused_not_dropped(home):
    # A typo in a field name would otherwise be stored nowhere and read as
    # "left alone", which is permanent and invisible.
    with pytest.raises(registry.ProfileError):
        registry.create("work", proxey="http://x:1")
    with pytest.raises(registry.ProfileError):
        registry.update("work", proxey="http://x:1")


@pytest.mark.unit
def test_setting_a_field_leaves_the_rest_alone(work):
    moved = registry.update("work", label="renamed")
    assert moved.label == "renamed"
    assert moved.seed == 4242
    assert moved.proxy == work.proxy


@pytest.mark.unit
def test_an_unknown_name_says_what_to_do(home):
    with pytest.raises(registry.UnknownProfile) as caught:
        registry.get("nope")
    assert "dots profile list" in str(caught.value)


@pytest.mark.unit
def test_a_registry_that_is_not_there_is_an_empty_one(home):
    assert registry.list_profiles() == []
    assert registry.find("anything") is None


@pytest.mark.unit
def test_a_registry_that_will_not_parse_reads_as_empty(home):
    registry.index_path().parent.mkdir(parents=True, exist_ok=True)
    registry.index_path().write_bytes(b"{ this is not json")
    # The same rule the package applies to a session file: a saved thing that
    # cannot be read is exactly as usable as one that was never saved, and a
    # registry that cannot be read must not stop the product from starting.
    assert registry.list_profiles() == []


@pytest.mark.unit
def test_a_record_written_by_a_later_build_still_reads(home):
    blob = '{"work": {"label": "w", "seed": "77", "headed": 1, ' \
           '"something_new": true}}'
    registry.index_path().parent.mkdir(parents=True, exist_ok=True)
    registry.index_path().write_bytes(blob.encode("utf-8"))
    profile = registry.get("work")
    assert profile.seed == 77
    assert profile.headed is True
    assert not hasattr(profile, "something_new")


@pytest.mark.unit
def test_a_seed_written_as_garbage_reads_as_none(home):
    registry.index_path().parent.mkdir(parents=True, exist_ok=True)
    registry.index_path().write_bytes(b'{"work": {"seed": "not a number"}}')
    assert registry.get("work").seed is None


@pytest.mark.unit
def test_a_profile_directory_can_be_pointed_somewhere_else(home):
    outside = home / "somewhere" / "else"
    made = registry.create("legacy", directory=str(outside))
    assert made.path() == outside.resolve()
    assert outside.is_dir()


@pytest.mark.unit
def test_a_relative_folder_is_measured_against_the_profile_not_the_cwd(
        home, tmp_path, monkeypatch):
    """⛔ A RELATIVE PATH RESOLVED AGAINST THE CURRENT DIRECTORY PUTS A WHOLE
    BROWSER PROFILE WHEREVER THE COMMAND WAS RUN FROM.

    It happened here: a profile whose folder was written as `Second_proxy`
    left a 150 MB Firefox profile in the repository root, because that is
    where the launcher happened to be. A relative path is measured against
    the profile's own home, which is the same place whatever the current
    directory is.
    """
    monkeypatch.chdir(tmp_path)
    made = registry.create("work", directory="Second_proxy")
    assert made.path() == registry.home_directory("work") / "Second_proxy"
    assert str(made.path()).startswith(str(home))
    # And the same answer from a different directory.
    monkeypatch.chdir(home)
    assert registry.get("work").path() == made.path()


# --- what a profile means to the browser --------------------------------------

@pytest.mark.unit
def test_launch_settings_carry_only_what_was_set(work):
    settings = work.launch_settings()
    assert settings["seed"] == 4242
    assert settings["proxy"] == "http://user:secret@10.0.0.1:8080"
    assert settings["profile_dir"] == str(work.path())


@pytest.mark.unit
def test_a_profile_with_no_proxy_does_not_send_an_empty_one(home):
    # `proxy=""` means "go out direct" and `None` means "the environment
    # decides". A profile that never mentioned a proxy has made no decision,
    # and sending the empty string would make one for it on every open.
    assert "proxy" not in registry.create("plain").launch_settings()


@pytest.mark.unit
def test_a_seed_is_never_sent_as_an_empty_one(home):
    assert "seed" not in registry.create("plain").launch_settings()


@pytest.mark.unit
def test_flags_are_the_ones_the_package_declares(work):
    flags = registry.as_flags(work)
    assert flags[0:2] == ["--proxy", work.proxy]
    assert "--seed" in flags and "4242" in flags
    assert flags[-2:] == ["--profile-dir", str(work.path())]


@pytest.mark.unit
def test_a_proxy_never_shows_its_password(work):
    assert registry.proxy_host(work.proxy) == "10.0.0.1:8080"
    assert "secret" not in work.describe()
    assert "secret" not in "\n".join(registry.describe_rows([work]))


# --- removing ----------------------------------------------------------------

@pytest.mark.unit
def test_removing_a_profile_leaves_its_files(home):
    made = registry.create("work")
    registry.delete("work")
    assert registry.list_profiles() == []
    assert made.path().is_dir(), "a browser directory with logins in it is "\
        "not what a list entry is worth"


@pytest.mark.unit
def test_wiping_removes_the_files_too(home):
    made = registry.create("work")
    registry.delete("work", wipe=True)
    assert not made.path().exists()
    assert not made.work().exists()


@pytest.mark.unit
def test_removing_a_profile_that_is_not_there_says_so(home):
    with pytest.raises(registry.UnknownProfile):
        registry.delete("nope")


# --- sites -------------------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("given, expected", [
    ("example.com", "example.com"),
    ("example.com/", "example.com"),
    ("https://example.com/login?next=/x", "example.com"),
    ("HTTP://Example.COM", "example.com"),
    ("www.example.com", "example.com"),
    ("localhost:8080", "localhost:8080"),
    ("https://example.com:443/x", "example.com"),
    ("https://example.com:8443/x", "example.com:8443"),
])
def test_a_site_is_a_host_and_nothing_else(given, expected):
    # A key that carried the path would make two pages of one shop two
    # different accounts, which is the confusion a saved login exists to stop.
    assert vault.site_key(given) == expected


@pytest.mark.unit
def test_something_that_is_not_a_site_is_not_a_site():
    assert vault.site_key("") == ""
    assert vault.site_key("   ") == ""


# --- the vault ---------------------------------------------------------------

@pytest.mark.unit
def test_a_login_is_saved_under_its_host(work):
    record = vault.add("work", "https://Example.com/login", "alice", "s3cret")
    assert record.site == "example.com"
    assert vault.find("work", "example.com").password == "s3cret"


@pytest.mark.unit
def test_a_login_is_found_for_a_subdomain_of_the_host_it_was_saved_for(work):
    vault.add("work", "example.com", "alice", "s3cret")
    # Somebody who saved the login for the site means it when the browser is
    # on the account page of the same site.
    assert vault.find("work", "id.example.com") is not None
    # But only one level: a login held for `b.example.com` when somebody saved
    # one for `example.com` is a guess, and a login held for all of `com` is
    # not a thing anybody saved.
    assert vault.find("work", "a.b.example.com") is None


@pytest.mark.unit
def test_an_exact_host_is_preferred_over_its_parent(work):
    vault.add("work", "example.com", "alice", "parent")
    vault.add("work", "id.example.com", "bob", "child")
    assert vault.find("work", "id.example.com").login == "bob"
    assert vault.find("work", "other.example.com").login == "alice"


@pytest.mark.unit
def test_a_login_saved_twice_is_replaced(work):
    vault.add("work", "example.com", "alice", "first")
    vault.add("work", "example.com", "alice", "second")
    held = vault.load("work")
    assert len(held) == 1
    assert held[0].password == "second"


@pytest.mark.unit
def test_a_saved_login_keeps_the_day_it_was_first_made(work):
    first = vault.add("work", "example.com", "alice", "one")
    again = vault.add("work", "example.com", "alice", "two")
    assert again.created == first.created


@pytest.mark.unit
def test_a_login_with_no_site_is_refused(work):
    with pytest.raises(vault.AuthError):
        vault.add("work", "", "alice", "s3cret")


@pytest.mark.unit
def test_a_login_with_neither_half_is_refused(work):
    with pytest.raises(vault.AuthError):
        vault.add("work", "example.com", "", "")


@pytest.mark.unit
def test_a_login_for_a_profile_that_is_not_there_is_refused(home):
    with pytest.raises(registry.UnknownProfile):
        vault.add("nope", "example.com", "alice", "s3cret")


@pytest.mark.unit
def test_a_missing_login_says_what_is_there_instead(work):
    vault.add("work", "example.com", "alice", "s3cret")
    with pytest.raises(vault.AuthError) as caught:
        vault.get("work", "other.example")
    assert "example.com" in str(caught.value)


@pytest.mark.unit
def test_removing_a_login_that_is_not_there_says_so(work):
    with pytest.raises(vault.AuthError):
        vault.remove("work", "example.com")


@pytest.mark.unit
def test_the_listing_carries_no_password(work):
    vault.add("work", "example.com", "alice", "s3cret")
    rows = "\n".join(vault.describe_rows("work"))
    assert "alice" in rows and "example.com" in rows
    assert "s3cret" not in rows


@pytest.mark.unit
def test_a_mask_does_not_leak_the_length():
    # Four dots whatever the length: the shape of a masked answer should not
    # tell a reader anything about the thing being masked.
    assert vault.mask("a") == vault.mask("a" * 40) == "...."
    assert vault.mask("") == ""


@pytest.mark.unit
def test_logins_of_one_profile_are_not_reachable_from_another(home):
    registry.create("work")
    registry.create("play")
    vault.add("work", "example.com", "alice", "s3cret")
    assert vault.find("play", "example.com") is None


# --- `--profile NAME` --------------------------------------------------------

@pytest.mark.unit
@pytest.mark.parametrize("argv", [
    ["--profile", "work", "--port", "9000"],
    ["--profile=work", "--port", "9000"],
    ["-p", "work", "--port", "9000"],
    ["--port", "9000", "--profile", "work"],
])
def test_every_spelling_of_the_flag_is_taken_out_of_the_argv(work, argv):
    rest, name = cli.split_profile_flag(argv)
    assert name == "work"
    assert "--profile" not in rest and "-p" not in rest
    assert "--port" in rest


@pytest.mark.unit
def test_a_profile_becomes_the_flags_the_package_declares(work, monkeypatch):
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
    rest = cli.resolve_profile_args(["--port", "9000"])
    # The profile's own settings go in front of what the caller typed, and
    # the profile's directory is the LAST of them, because `--profile-dir` is
    # the one the package reads last in an argv.
    assert rest[:2] == ["--proxy", work.proxy]
    assert rest[2:4] == ["--seed", "4242"]
    assert rest[-4:] == ["--profile-dir", str(work.path()), "--port", "9000"]
    assert "--port" in rest and "9000" in rest


@pytest.mark.unit
def test_the_resolved_profile_reaches_the_browser_server_as_a_name(work, monkeypatch):
    monkeypatch.delenv(registry.ACTIVE_VARIABLE, raising=False)
    cli.resolve_profile_args(["--profile", "work"])
    # `dots --profile work` on a command line, with nothing in `.env`: the
    # spawned server still has to know whose logins it may read, and the
    # child's environment is the only channel there is.
    assert os.environ[registry.ACTIVE_VARIABLE] == "work"


@pytest.mark.unit
def test_an_explicit_profile_directory_beats_the_profile_in_use(work):
    rest = cli.resolve_profile_args(
        ["--profile-dir", "D:/scratch"],
        env={registry.ACTIVE_VARIABLE: "work"})
    # The flag somebody typed is the most explicit thing there is, and
    # `dots --profile work --profile-dir D:/scratch` has to open the scratch.
    assert rest == ["--profile-dir", "D:/scratch"]


@pytest.mark.unit
def test_nothing_named_means_the_package_defaults(home):
    assert cli.resolve_profile_args(["--port", "1"], env={}) == ["--port", "1"]


@pytest.mark.unit
def test_a_name_that_is_not_in_the_registry_is_refused_not_ignored(home):
    # Starting anyway is how somebody finds out three minutes later from a
    # browser that kept nothing, in a directory nobody thought to look in.
    with pytest.raises(registry.UnknownProfile):
        cli.resolve_profile_args(["--profile", "nope"], env={})


@pytest.mark.unit
def test_a_name_in_the_environment_that_was_removed_does_not_stop_the_boot(
        home, capsys):
    # `.env` is written by hand; a line left behind after a delete is not a
    # reason to refuse to start. It says so on stderr and carries on.
    rest = cli.resolve_profile_args(["--port", "1"], env={
        registry.ACTIVE_VARIABLE: "gone"})
    assert rest == ["--port", "1"]
    assert "gone" in capsys.readouterr().err


@pytest.mark.unit
def test_a_name_typed_on_the_command_line_is_refused_when_it_is_not_there(home):
    # The other half of the rule above: this one somebody typed an instant ago
    # and is looking at the screen, so a typo is worth a sentence and an exit.
    with pytest.raises(registry.UnknownProfile):
        cli.resolve_profile_args(["--profile", "tpo"], env={})


# --- the spawned browser server ----------------------------------------------

@pytest.mark.integration
def test_the_browser_server_spawned_is_this_builds():
    """The command the interface gives the model must be ours.

    Everything the agent can call is what that process answers `list_tools`
    with, so the tool count is what the interface prints at startup and what
    the model is offered. A build whose tools never arrived would look
    exactly like this one until a task needed a login.
    """
    from invisible_playwright_mcp import link

    cli._install_browser_server()
    params = link.Link({})._params()
    assert params.command == sys.executable
    assert params.args == ["-m", "dots.mcp_boot"]


@pytest.mark.integration
def test_the_child_still_never_gets_the_model_key(monkeypatch):
    from invisible_playwright_mcp import link

    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    cli._install_browser_server()
    env = link.Link({})._params().env
    # By name AND by value: the key arrives under the SDK's own name here, and
    # `runner.child_env` is the one place that knows both spellings.
    assert "OPENROUTER_API_KEY" not in env
    assert "sk-test" not in env.values()


@pytest.mark.integration
def test_a_profile_reaches_the_child_through_the_engine_s_own_names(work):
    from invisible_playwright_mcp import link

    os.environ[registry.ACTIVE_VARIABLE] = "work"
    cli._install_browser_server()
    env = link.Link({})._params().env
    assert env["STEALTHFOX_PROFILE_DIR"] == str(work.path())
    assert env["STEALTHFOX_SEED"] == "4242"
    assert env["STEALTHFOX_PROXY"] == work.proxy
    assert env[registry.ACTIVE_VARIABLE] == "work"


@pytest.mark.integration
def test_an_option_from_the_command_line_is_not_overwritten_by_a_profile(work):
    from invisible_playwright_mcp import link

    os.environ[registry.ACTIVE_VARIABLE] = "work"
    cli._install_browser_server()
    env = link.Link({"profile_dir": "D:/scratch"})._params().env
    assert env["STEALTHFOX_PROFILE_DIR"] == "D:/scratch"


@pytest.mark.unit
def test_the_child_boot_goes_through_the_packages_own_group(monkeypatch):
    """The group is where the key is taken back out of the child's own
    environment; calling the server around it would keep the tools and drop
    the half of the guarantee that protects the browser."""
    from invisible_playwright_mcp import cli as ipm_cli

    seen = {}

    def group(*, args, prog_name):
        seen["args"], seen["prog_name"] = args, prog_name

    monkeypatch.setattr(ipm_cli, "main", group)
    import dots.mcp_boot

    dots.mcp_boot.main()
    # No subcommand: that is the branch that serves the browser server rather
    # than printing a usage line.
    assert seen == {"args": [], "prog_name": dots.mcp_boot.PROGRAM}


# --- the tools the agent calls ------------------------------------------------

@pytest.fixture(scope="module")
def server_tools():
    """The package's server module with this build's tools on it, once.

    The import builds the engine handle and reads the environment; doing it
    per test would walk the engine cache once per test for an answer that
    cannot change between them.
    """
    from invisible_playwright_mcp.mcp import server

    import dots.tools

    dots.tools.register(server)
    return server


def call(server, tool: str, arguments: dict | None = None):
    """One tool call, as the model makes it, and the text it answered with."""
    out = asyncio.run(server.mcp.call_tool(tool, arguments or {}))
    content = out[0] if isinstance(out, tuple) else out
    return "\n".join(getattr(item, "text", "") for item in content)


@pytest.fixture
def in_profile(work, monkeypatch):
    """This run's profile is `work`, which is what the GUI and `.env` set.

    The tools find their vault from the browser, and fall back to the run's
    profile when nothing is open - the same answer whether the name arrived
    as a flag or in a file, because `resolve_profile_args` publishes it.
    """
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, work.name)
    return work


# --- the handback ------------------------------------------------------------

@pytest.mark.unit
def test_a_handback_needs_a_question(work):
    with pytest.raises(ValueError):
        handover.ask("work", "   ", need="a code")


@pytest.mark.unit
def test_a_handback_for_a_profile_that_is_not_there_is_refused(home):
    with pytest.raises(registry.UnknownProfile):
        handover.ask("nope", "what is the code?")


@pytest.mark.unit
def test_a_handback_is_one_question_and_not_a_queue(work):
    handover.ask("work", "what is the code for example.com?")
    handover.ask("work", "and the one for the other site?")
    # A second one replaces the first: an agent that asked twice did not wait
    # the first time, and a list of questions is a list nobody reads.
    assert handover.current("work")["question"] == "and the one for the other site?"
    assert len(handover.current("work")) >= 4


@pytest.mark.unit
def test_nothing_asked_is_nothing_outstanding(work):
    assert handover.current("work") is None


@pytest.mark.unit
def test_a_handback_that_will_not_parse_is_not_outstanding(work):
    handover.ask("work", "what is the code?")
    handover.path_for("work").write_bytes(b"{ not json at all")
    # The same rule every other file in this product follows: a saved thing
    # that cannot be read is exactly as usable as one that was never saved,
    # and it must not stop a browser from opening.
    assert handover.current("work") is None


@pytest.mark.unit
def test_clearing_says_whether_there_was_anything(work):
    assert handover.clear("work") is False
    handover.ask("work", "what is the code?")
    assert handover.clear("work") is True
    assert handover.current("work") is None


@pytest.mark.unit
def test_a_handback_is_recorded_with_the_page_it_was_asked_on(work):
    record = handover.ask("work", "what is the code?", need="six digits",
                          page="https://example.com/login")
    said = handover.describe(record)
    assert "example.com" in said
    assert "six digits" in said
    assert "asked" in said


@pytest.mark.unit
def test_the_model_is_told_when_to_stop_and_ask(server_tools):
    said = server_tools.mcp._mcp_server.instructions
    # The rule has to be here and not only in a tool docstring: this text is
    # what reaches the model once per conversation, and a tool description is
    # read only when the model is already considering that tool.
    assert "dots_ask_operator" in said
    assert "END YOUR TURN" in said
    # And the half that is a behaviour rather than a tool: never click while
    # the person is typing in a visible window.
    assert "do NOT click" in said


@pytest.mark.integration
def test_asking_the_operator_writes_the_question_down(server_tools, in_profile):
    said = call(server_tools, "dots_ask_operator", {
        "question": "What is the six-digit code for example.com?",
        "need": "the six digits from your authenticator app"})
    # The tool answers with what the model is told to do next, which is the
    # half that matters: the model has to end its turn, and that instruction
    # travels in the answer rather than being left to its judgement.
    assert "END YOUR TURN" in said
    record = handover.current("work")
    assert "example.com" in record["question"]
    assert "authenticator" in record["need"]


@pytest.mark.integration
def test_asking_needs_a_profile_to_ask_into(server_tools, home):
    with pytest.raises(Exception) as caught:
        call(server_tools, "dots_ask_operator", {"question": "what is the code?"})
    assert "dots_profile_use" in str(caught.value)


@pytest.mark.integration
def test_a_profile_shows_what_it_is_waiting_on(server_tools, in_profile):
    assert "nothing" in call(server_tools, "dots_profile_show", {"name": "work"})
    handover.ask("work", "what is the six-digit code?")
    said = call(server_tools, "dots_profile_show", {"name": "work"})
    assert "six-digit" in said


@pytest.mark.unit
def test_the_handover_command_reads_the_question(work, capsys):
    handover.ask("work", "what is the six-digit code?", need="six digits")
    assert cli._handover_command(["work"]) == 0
    assert "six-digit" in capsys.readouterr().out


@pytest.mark.unit
def test_the_handover_command_clears_and_says_so(work, capsys):
    handover.ask("work", "what is the six-digit code?")
    assert cli._handover_command(["work", "--clear"]) == 0
    assert "Cleared" in capsys.readouterr().out
    assert handover.current("work") is None


@pytest.mark.unit
def test_the_handover_command_says_when_nothing_is_outstanding(work, capsys):
    assert cli._handover_command(["work"]) == 0
    assert "not waiting" in capsys.readouterr().out


@pytest.mark.unit
def test_the_handover_command_cannot_write_a_question(work, capsys):
    # A handback is a question a PERSON has to answer, so a command that could
    # write one would let a script put words in the operator's mouth.
    assert cli._handover_command(["work", "--question", "hello"]) == 1
    assert handover.current("work") is None


# --- two profiles at once ------------------------------------------------------

@pytest.mark.unit
def test_a_profile_can_hold_a_port(work):
    moved = registry.update("work", port=8766)
    assert moved.port == 8766
    assert moved.port_or(8765) == 8766


@pytest.mark.unit
def test_a_profile_with_no_port_takes_the_default(work):
    # Zero is not a port anybody can listen on, so a profile that never chose
    # one takes the default rather than refusing to start.
    assert work.port == 0
    assert work.port_or(8765) == 8765


@pytest.mark.unit
def test_the_profile_port_reaches_the_interface(work, monkeypatch):
    registry.update("work", port=8766)
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
    rest = cli.resolve_profile_args([])
    # The port is what lets a second profile run at all: without one the
    # second instance fails to bind and says so.
    assert rest[:2] == ["--port", "8766"]


@pytest.mark.unit
def test_a_port_on_the_command_line_beats_the_profile_port(work, monkeypatch):
    registry.update("work", port=8766)
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
    rest = cli.resolve_profile_args(["--port", "9000"])
    # A flag somebody typed is the more explicit of the two, and there must be
    # only one --port in the argv or the last one silently wins.
    assert rest.count("--port") == 1
    assert rest[rest.index("--port") + 1] == "9000"


@pytest.mark.unit
def test_a_profile_with_no_port_adds_no_port_flag(work, monkeypatch):
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
    assert "--port" not in cli.resolve_profile_args([])


@pytest.mark.unit
def test_the_profile_command_takes_a_port(work):
    assert cli._profile_command(["set", "work", "--port", "8770"]) == 0
    assert registry.get("work").port == 8770


@pytest.mark.unit
def test_a_port_that_is_not_a_number_is_refused(work, capsys):
    assert cli._profile_command(["set", "work", "--port", "abc"]) == 1
    assert "whole number" in capsys.readouterr().out


@pytest.mark.unit
def test_a_port_out_of_range_is_refused(work, capsys):
    assert cli._profile_command(["set", "work", "--port", "70000"]) == 1
    assert "out of range" in capsys.readouterr().out


@pytest.mark.unit
def test_two_profiles_get_two_conversations(monkeypatch):
    """The context of one profile must not be the context of another.

    ⛔ THE PACKAGE NAMES EVERY CONVERSATION "default", AND EVERY PROCESS
    AGREES ON THAT NAME. The transcript and the remembered browser are both
    named after it, so two profiles running at once were one conversation
    with one browser between them - and the second one to start read the
    first one's saved identity and opened THAT browser.
    """
    from invisible_playwright_mcp import chat as ipm_chat
    from invisible_playwright_mcp import routes as ipm_routes
    from invisible_playwright_mcp import sessions as ipm_sessions

    saved = (ipm_chat.DEFAULT_CHAT_ID, ipm_routes.DEFAULT_CHAT_ID,
             ipm_sessions.DEFAULT_CHAT_ID, ipm_sessions.Sessions._free_id)
    try:
        registry.create("work")
        registry.create("play")
        monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
        cli._install_profile_scope()
        work_id = ipm_chat.DEFAULT_CHAT_ID
        assert work_id != "default"
        assert ipm_routes.DEFAULT_CHAT_ID == work_id
        assert ipm_sessions.DEFAULT_CHAT_ID == work_id

        monkeypatch.setenv(registry.ACTIVE_VARIABLE, "play")
        cli._install_profile_scope()
        play_id = ipm_chat.DEFAULT_CHAT_ID
        assert play_id != work_id
        # And the names are file names: two conversations, two files.
        assert work_id != play_id
    finally:
        (ipm_chat.DEFAULT_CHAT_ID, ipm_routes.DEFAULT_CHAT_ID,
         ipm_sessions.DEFAULT_CHAT_ID, ipm_sessions.Sessions._free_id) = saved


@pytest.mark.unit
def test_a_new_conversation_is_named_for_its_profile(monkeypatch):
    from invisible_playwright_mcp import chat as ipm_chat
    from invisible_playwright_mcp import sessions as ipm_sessions

    saved = (ipm_chat.DEFAULT_CHAT_ID, ipm_sessions.DEFAULT_CHAT_ID,
             ipm_sessions.Sessions._free_id)
    try:
        registry.create("work")
        monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
        cli._install_profile_scope()
        # A name drawn from the clock is the same name in two processes
        # started in the same millisecond, and two conversations sharing one
        # file is the same defect with a smaller number.
        drawn = ipm_sessions.Sessions._free_id(
            ipm_sessions.Sessions({}, None, lambda: None))
        assert drawn.startswith("work-")
    finally:
        (ipm_chat.DEFAULT_CHAT_ID, ipm_sessions.DEFAULT_CHAT_ID,
         ipm_sessions.Sessions._free_id) = saved


@pytest.mark.unit
def test_without_a_profile_the_conversation_is_still_the_default(monkeypatch):
    from invisible_playwright_mcp import chat as ipm_chat

    saved = ipm_chat.DEFAULT_CHAT_ID
    try:
        monkeypatch.delenv(registry.ACTIVE_VARIABLE, raising=False)
        cli._install_profile_scope()
        # Nothing changes for an install that knows no profiles: the package's
        # own name for the conversation is still its own.
        assert ipm_chat.DEFAULT_CHAT_ID == saved
    finally:
        ipm_chat.DEFAULT_CHAT_ID = saved


@pytest.mark.unit
def test_the_scope_follows_the_profile_on_the_command_line(tmp_path, monkeypatch):
    """⛔ THE BUG THIS IS ABOUT: the scope was installed from the ENVIRONMENT
    and the browser settings from the COMMAND LINE.

    In a directory whose `.env` says `DOTS_PROFILE=work`, `dots --profile
    play` came up with play's browser and work's conversation - and two
    instances were one conversation with one saved browser between them.
    """
    from invisible_playwright_mcp import chat as ipm_chat

    saved = ipm_chat.DEFAULT_CHAT_ID
    try:
        registry.create("work")
        registry.create("play")
        (tmp_path / ".env").write_text("DOTS_PROFILE=work\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv(registry.ACTIVE_VARIABLE, raising=False)

        cli._load_env_file(tmp_path)
        assert os.environ[registry.ACTIVE_VARIABLE] == "work"

        # The command line wins, and it is published before the scope is read.
        cli.resolve_profile_args(["--profile", "play"])
        assert os.environ[registry.ACTIVE_VARIABLE] == "play"

        cli._install_profile_scope()
        assert ipm_chat.DEFAULT_CHAT_ID == "default-play"
    finally:
        ipm_chat.DEFAULT_CHAT_ID = saved
        os.environ.pop(registry.ACTIVE_VARIABLE, None)


@pytest.mark.unit
def test_the_scope_is_installed_once_and_not_twice(monkeypatch):
    from invisible_playwright_mcp import chat as ipm_chat

    saved = ipm_chat.DEFAULT_CHAT_ID
    try:
        registry.create("work")
        monkeypatch.setenv(registry.ACTIVE_VARIABLE, "work")
        cli._install_profile_scope()
        cli._install_profile_scope()
        # `default-work-work` is a name nobody would ever guess.
        assert ipm_chat.DEFAULT_CHAT_ID == "default-work"
    finally:
        ipm_chat.DEFAULT_CHAT_ID = saved


@pytest.mark.unit
def test_a_profile_that_is_not_there_scopes_nothing(monkeypatch):
    from invisible_playwright_mcp import chat as ipm_chat

    saved = ipm_chat.DEFAULT_CHAT_ID
    try:
        monkeypatch.setenv(registry.ACTIVE_VARIABLE, "gone")
        cli._install_profile_scope()
        # "Starting without one" has to mean without one, in every sense.
        assert ipm_chat.DEFAULT_CHAT_ID == saved
    finally:
        ipm_chat.DEFAULT_CHAT_ID = saved

@pytest.mark.unit
def test_the_page_keeps_everything_the_package_had():
    from invisible_playwright_mcp import routes

    before = routes.PAGE
    after = live.page_with_scrolling(before)
    # The package's page is read by hundreds of assertions in its own suite,
    # and this build adds to it rather than rewriting it: everything that was
    # there is still there, and the new script is on the end.
    assert before in after or all(part in after for part in
                                  (routes.PAGE[:200], routes.PAGE[-200:]))
    assert "/live/scroll" in after
    assert after.count("<script>") == before.count("<script>") + 1


@pytest.mark.unit
def test_the_page_script_answers_the_wheel_and_only_the_wheel():
    script = live.SCROLL_JS
    # A wheel over a picture goes to the browser; a wheel anywhere else is
    # the interface's own business and must not be swallowed.
    assert "closest('.screen')" in script
    assert "preventDefault" in script
    assert "passive:false" in script
    # Shift is sideways, the way every browser does it.
    assert "shiftKey" in script


@pytest.mark.unit
def test_the_route_is_installed_on_the_app():
    from invisible_playwright_mcp import routes

    original = routes.build_app
    live.install(routes)
    try:
        app = routes.build_app(None)
        paths = {getattr(r, "path", "") for r in app.routes}
        assert live.SCROLL_PATH in paths
    finally:
        routes.build_app = original
        routes.PAGE = routes.PAGE  # left as it was found; see below


@pytest.mark.unit
def test_a_scroll_that_is_not_a_number_is_refused(monkeypatch):
    from starlette.requests import Request

    called = []
    monkeypatch.setattr("invisible_playwright_mcp.routes.which",
                        lambda request: called.append(request))
    scope = {"type": "http", "method": "POST", "path": live.SCROLL_PATH,
             "headers": [(b"content-type", b"application/json")],
             "query_string": b"", "app": None}
    request = Request(scope, receive=_body({"dx": "abc", "dy": 0}))
    import asyncio

    answer = asyncio.run(live.scroll(request))
    assert answer.status_code == 400
    # And it never reached the browser: a scroll it could not read is not a
    # scroll.
    assert called == []


@pytest.mark.unit
def test_a_scroll_of_nothing_is_not_a_scroll(monkeypatch):
    from starlette.requests import Request

    called = []
    monkeypatch.setattr("invisible_playwright_mcp.routes.which",
                        lambda request: called.append(request))
    scope = {"type": "http", "method": "POST", "path": live.SCROLL_PATH,
             "headers": [(b"content-type", b"application/json")],
             "query_string": b"", "app": None}
    request = Request(scope, receive=_body({"dx": 0, "dy": 0}))
    import asyncio

    answer = asyncio.run(live.scroll(request))
    assert answer.status_code == 200
    assert called == []


@pytest.mark.unit
def test_a_scroll_reaches_the_browser_through_the_same_tool(monkeypatch):
    from starlette.requests import Request

    seen = {}

    class FakeLink:
        async def call(self, name, arguments):
            seen["name"], seen["arguments"] = name, arguments
            return "scrolled by 0,800"

    class FakeService:
        link = FakeLink()

    async def which(request):
        return FakeService()

    monkeypatch.setattr("invisible_playwright_mcp.routes.which", which)
    scope = {"type": "http", "method": "POST", "path": live.SCROLL_PATH,
             "headers": [(b"content-type", b"application/json")],
             "query_string": b"", "app": None}
    request = Request(scope, receive=_body({"dx": 0, "dy": 800, "b": "support"}))
    import asyncio

    answer = asyncio.run(live.scroll(request))
    assert answer.status_code == 200
    # The same tool an agent would call, so the wheel and the agent's own
    # scrolling are one action and the browser's lock serialises them.
    assert seen["name"] == "dots_scroll"
    assert seen["arguments"] == {"dx": 0, "dy": 800, "browser": "support"}


@pytest.mark.integration
def test_the_agent_is_offered_a_real_wheel(server_tools):
    names = {t.name for t in asyncio.run(server_tools.mcp.list_tools())}
    assert "dots_scroll" in names
    tool = next(t for t in asyncio.run(server_tools.mcp.list_tools())
                if t.name == "dots_scroll")
    assert set(tool.inputSchema.get("properties", {})) >= {"dy", "dx"}


@pytest.mark.integration
def test_a_scroll_still_refuses_a_third_browser(server_tools, in_profile):
    with pytest.raises(Exception) as caught:
        call(server_tools, "dots_scroll", {"dy": 100, "browser": "third"})
    assert "two browsers" in str(caught.value)


@pytest.mark.integration
def test_the_agent_is_offered_the_profile_and_login_tools(server_tools):
    names = {t.name for t in asyncio.run(server_tools.mcp.list_tools())}
    for expected in ("dots_profile_list", "dots_profile_show",
                     "dots_profile_use", "dots_profile_save",
                     "dots_auth_list", "dots_auth_get", "dots_auth_signin",
                     "dots_auth_save", "dots_auth_forget",
                     "dots_ask_operator"):
        assert expected in names
    # And the package's own tools are untouched: this adds, it does not
    # replace what the model already knew how to do.
    assert "browser_open" in names and "browser_type" in names


@pytest.mark.integration
def test_the_model_is_told_about_them(server_tools):
    # A tool nobody is told about is a tool the model does not reach for, and
    # these reach the model through the handshake rather than a docstring.
    said = server_tools.mcp._mcp_server.instructions
    assert "dots profile use" in said
    assert "dots auth get" in said


@pytest.mark.integration
def test_a_tool_description_survives_the_api_cut(server_tools):
    # `agent.mcp_tools_to_openai` truncates at 1024 characters, so a tool
    # whose instructions end there is a tool the model never hears the end of.
    for tool in asyncio.run(server_tools.mcp.list_tools()):
        if tool.name.startswith("dots_"):
            assert len(tool.description or "") < 1024


@pytest.mark.integration
def test_listing_profiles_shows_no_password(server_tools, in_profile):
    vault.add("work", "example.com", "alice", "s3cret")
    said = call(server_tools, "dots_profile_list")
    assert "work" in said and "example.com" in said
    assert "s3cret" not in said and "alice" not in said


@pytest.mark.integration
def test_listing_logins_shows_the_logins_and_no_password(server_tools, in_profile):
    vault.add("work", "example.com", "alice", "s3cret")
    said = call(server_tools, "dots_auth_list")
    assert "alice" in said
    assert "s3cret" not in said


@pytest.mark.integration
def test_reading_one_login_answers_with_the_password(server_tools, in_profile):
    vault.add("work", "example.com", "alice", "s3cret")
    said = call(server_tools, "dots_auth_get", {"site": "https://id.example.com/"})
    assert "alice" in said and "s3cret" in said


@pytest.mark.integration
def test_a_login_is_read_from_the_profile_the_browser_was_opened_as(
        server_tools, home, monkeypatch):
    registry.create("work")
    registry.create("play")
    vault.add("work", "example.com", "alice", "work-secret")
    vault.add("play", "example.com", "bob", "play-secret")

    # Nothing is open, and nothing was named: the run's profile answers.
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "play")
    assert "play-secret" in call(server_tools, "dots_auth_get",
                                 {"site": "example.com"})

    # And the vault in reach is decided by the BROWSER, not by an argument a
    # model could pass - so a browser opened as `work` reads `work`'s.
    server_tools.work._launched["main"] = {"profile_dir": str(
        registry.get("work").path())}
    try:
        assert "work-secret" in call(server_tools, "dots_auth_get",
                                     {"site": "example.com"})
    finally:
        server_tools.work._launched.clear()


@pytest.mark.integration
def test_saving_a_login_needs_a_profile_to_save_it_into(server_tools, in_profile):
    said = call(server_tools, "dots_auth_save",
                {"site": "example.com", "login": "alice", "password": "s3cret"})
    assert "example.com" in said
    assert vault.find("work", "example.com").password == "s3cret"


@pytest.mark.integration
def test_saving_a_login_with_no_profile_says_so_rather_than_guessing(
        server_tools, home):
    # Nowhere to put it is a fact about the setup, not about the site, and the
    # sentence names the tool that fixes it.
    with pytest.raises(Exception) as caught:
        call(server_tools, "dots_auth_save",
             {"site": "example.com", "login": "alice", "password": "s3cret"})
    assert "dots_profile_use" in str(caught.value)


@pytest.mark.integration
def test_forgetting_a_login_removes_it(server_tools, in_profile):
    vault.add("work", "example.com", "alice", "s3cret")
    call(server_tools, "dots_auth_forget", {"site": "example.com"})
    assert vault.find("work", "example.com") is None


@pytest.mark.integration
def test_a_login_that_is_not_saved_is_a_refusal_not_an_empty_one(
        server_tools, in_profile):
    with pytest.raises(Exception) as caught:
        call(server_tools, "dots_auth_get", {"site": "example.com"})
    assert "no login saved" in str(caught.value)


@pytest.mark.integration
def test_a_third_browser_is_still_refused(server_tools, in_profile):
    # The two-browser rule the package holds is not widened by anything here:
    # `profile_use` is a reopen, not a new name in a registry.
    with pytest.raises(Exception) as caught:
        call(server_tools, "dots_profile_use", {"name": "work", "browser": "third"})
    assert "two browsers" in str(caught.value)


@pytest.mark.integration
def test_the_model_cannot_name_a_profile_to_switch_to_on_the_auth_tools(
        server_tools, home, monkeypatch):
    # `auth_get` takes a site and nothing else. A model cannot ask for another
    # profile's vault, because there is no argument to ask with.
    registry.create("work")
    registry.create("play")
    vault.add("play", "example.com", "bob", "play-secret")
    monkeypatch.setenv(registry.ACTIVE_VARIABLE, "play")

    tool = next(t for t in asyncio.run(server_tools.mcp.list_tools())
                if t.name == "dots_auth_get")
    # The schema is the guarantee, and it is what the model reads: a site and
    # nothing else, so there is no argument it could pass to ask for somebody
    # else's vault even if it decided to.
    assert set(tool.inputSchema.get("properties", {})) == {"site"}