"""The device a profile claims to be: the pins, the launch, and the check.

Four questions are judged here, and they are four different ones:

  * a vocabulary - a pin is written down by a person and read by the engine,
    and the engine owns the list of names;
  * a record - a pin is part of a profile and survives a save and a load;
  * a launch - the pin reaches the engine, and the host's own text scale is
    taken out of the ratio, which is the half that was measured and leaked;
  * a measurement - what a page answers against what the browser was told,
    which is the only way to know whether the two are the same thing.

Nothing here launches a browser: the measurement is asked of a fake session,
and the launch is asked of the settings that would go to the engine.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from dots import cli
from dots import device
from dots import geometry
from dots import profiles as registry


@pytest.fixture(autouse=True)
def home(tmp_path, monkeypatch):
    """Every test's own data directory, and no `DOTS_PROFILE` leaking in."""
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    monkeypatch.delenv(registry.ACTIVE_VARIABLE, raising=False)
    return tmp_path / "home"


# --- the vocabulary ----------------------------------------------------------

@pytest.mark.unit
def test_a_pin_is_written_as_name_equals_value():
    assert geometry.parse_pin(["screen.width=1920"]) == {"screen.width": 1920}


@pytest.mark.unit
def test_pins_may_be_written_side_by_side():
    # A box in the launcher has one line, a command line has a flag that may
    # be written twice, and both must land on the same dict.
    assert geometry.parse_pin(["screen.width=1920, screen.dpr=1.25"]) == {
        "screen.width": 1920, "screen.dpr": 1.25}
    assert geometry.parse_pin(["screen.width=1920", "screen.dpr=1.25"]) == {
        "screen.width": 1920, "screen.dpr": 1.25}


@pytest.mark.unit
def test_a_whole_number_stays_a_whole_number():
    # The engine writes both into prefs, and "1.0" is a different value to it
    # than 1 is.
    assert geometry.parse_pin(["screen.width=1920"])["screen.width"] == 1920
    assert isinstance(geometry.parse_pin(["screen.width=1920"])["screen.width"], int)
    assert geometry.parse_pin(["screen.dpr=1.25"])["screen.dpr"] == 1.25


@pytest.mark.unit
def test_a_pin_without_a_value_is_refused():
    with pytest.raises(geometry.GeometryError):
        geometry.parse_pin(["screen.width"])
    with pytest.raises(geometry.GeometryError):
        geometry.parse_pin(["screen.width=lots"])


@pytest.mark.unit
def test_the_engine_decides_which_pins_exist():
    # The vocabulary belongs to the engine: it refuses a key it does not know
    # and says which keys it does, and that answer is what comes out here.
    with pytest.raises(geometry.GeometryError) as refused:
        geometry.check_pin({"screen.nosuch": 1})
    assert "chrome_w" in str(refused.value)


@pytest.mark.unit
def test_a_pin_the_engine_knows_is_accepted():
    assert geometry.check_pin({"screen.width": 1920}) == {"screen.width": 1920}
    assert geometry.check_pin({"screen.dpr": 1.25}) == {"screen.dpr": 1.25}


@pytest.mark.unit
def test_a_pin_value_that_is_not_a_number_is_refused():
    with pytest.raises(geometry.GeometryError):
        geometry.check_pin({"screen.width": "wide"})


@pytest.mark.unit
def test_a_pin_is_written_back_the_way_a_person_reads_it():
    assert geometry.format_pin({"screen.dpr": 1.25, "screen.width": 1920}) == (
        "screen.dpr=1.25, screen.width=1920")
    assert geometry.format_pin({}) == ""


# --- the record --------------------------------------------------------------

@pytest.mark.unit
def test_a_profile_keeps_its_pins(home):
    made = registry.create("work", seed=4242, pin={"screen.width": 1920})
    assert made.pin == {"screen.width": 1920}
    assert registry.get("work").pin == {"screen.width": 1920}


@pytest.mark.unit
def test_a_pin_survives_a_save_and_a_load(home):
    registry.create("work", seed=4242, pin={"screen.width": 1920,
                                            "screen.dpr": 1.25})
    # Through the file, not through the object: the file is what the next
    # build reads.
    blob = json.loads(registry.index_path().read_text(encoding="utf-8"))
    assert blob["work"]["pin"] == {"screen.width": 1920, "screen.dpr": 1.25}


@pytest.mark.unit
def test_a_pin_the_engine_would_refuse_is_refused_at_the_door(home):
    with pytest.raises(registry.ProfileError):
        registry.create("work", seed=4242, pin={"screen.nosuch": 1})
    with pytest.raises(registry.ProfileError):
        registry.update("work", pin={"screen.nosuch": 1})


@pytest.mark.unit
def test_a_pin_this_build_cannot_read_is_dropped_not_refused(home):
    # A file written by a later build must not stop this one from working:
    # the rest of the profile is somebody's work and this field is the only
    # one the engine would refuse.
    registry.create("work", seed=4242)
    blob = json.loads(registry.index_path().read_text(encoding="utf-8"))
    blob["work"]["pin"] = {"screen.nosuch": 1}
    registry.index_path().write_text(json.dumps(blob), encoding="utf-8")
    assert registry.get("work").pin == {}
    assert registry.get("work").seed == 4242


@pytest.mark.unit
def test_a_profile_with_no_seed_has_no_device_of_its_own(home):
    made = registry.create("work")
    assert made.pin == {}
    assert made.device() == {}


# --- the seed the browser is actually opened with -----------------------------

@pytest.mark.unit
def test_the_browser_directory_remembers_who_the_profile_was(home):
    # The directory is where the cookies are, so the seed in it is the one the
    # browser has to be, whatever the registry says.
    made = registry.create("work", seed=4242)
    (made.path() / ".stealth-identity.json").write_text(
        json.dumps({"seed": 1361434516}), encoding="utf-8")
    assert registry.get("work").effective_seed() == 1361434516


@pytest.mark.unit
def test_a_profile_with_no_memory_is_the_seed_the_registry_says(home):
    registry.create("work", seed=4242)
    assert registry.get("work").effective_seed() == 4242


@pytest.mark.unit
def test_the_seed_that_is_passed_is_the_one_the_browser_will_be(home):
    # A registry that disagrees with the directory is a profile the pinned
    # package refuses to open ("this profile is seed N and you asked for M"),
    # so the seed that is passed has to be the one the directory remembers.
    made = registry.create("work", seed=4242)
    (made.path() / ".stealth-identity.json").write_text(
        json.dumps({"seed": 1361434516}), encoding="utf-8")
    profile = registry.get("work")
    assert profile.launch_settings()["seed"] == 1361434516
    assert registry.as_flags(profile)[:2] == ["--seed", "1361434516"]

@pytest.mark.unit
def test_the_device_shown_is_the_one_the_browser_will_have(home):
    made = registry.create("work", seed=4242)
    (made.path() / ".stealth-identity.json").write_text(
        json.dumps({"seed": 1361434516}), encoding="utf-8")
    profile = registry.get("work")
    assert profile.device() == geometry.declared(1361434516, profile.pin)
    assert profile.device()["screen_width"] == 1920


@pytest.mark.unit
def test_the_disagreement_is_said_out_loud(home, capsys):
    made = registry.create("work", seed=4242)
    (made.path() / ".stealth-identity.json").write_text(
        json.dumps({"seed": 1361434516}), encoding="utf-8")
    cli._profile_command(["show", "work"])
    said = capsys.readouterr().out
    # The seed the browser will be opened with, and the disagreement named.
    assert "seed            1361434516" in said
    assert "remembers seed 1361434516" in said
    assert "the registry says 4242" in said


# --- the launch --------------------------------------------------------------

@pytest.mark.unit
def test_the_pins_of_the_profile_the_directory_belongs_to_reach_the_launch(home):
    made = registry.create("work", seed=4242, pin={"screen.width": 1920})
    settings = device.launch_settings({"seed": 4242,
                                       "profile_dir": str(made.path())})
    assert settings["pin"] == {"screen.width": 1920}


@pytest.mark.unit
def test_a_directory_that_belongs_to_no_profile_gets_no_pins(home):
    settings = device.launch_settings({"seed": 4242,
                                       "profile_dir": str(home / "nowhere")})
    assert "pin" not in settings


@pytest.mark.unit
def test_the_hosts_text_scale_is_taken_out_of_the_ratio(home):
    # The measurement that decided it: with the number, the page reads the
    # profile's ratio; with the string the engine writes, it reads the host's
    # multiplied into it.
    settings = device.launch_settings({"seed": 4242})
    assert settings["extra_prefs"]["ui.textScaleFactor"] == 100
    assert not isinstance(settings["extra_prefs"]["ui.textScaleFactor"], str)


@pytest.mark.unit
def test_a_caller_that_asked_for_a_prefs_of_its_own_still_gets_them(home):
    settings = device.launch_settings({"seed": 4242,
                                       "extra_prefs": {"dom.w3c_touch_events.enabled": 2}})
    assert settings["extra_prefs"]["dom.w3c_touch_events.enabled"] == 2
    assert settings["extra_prefs"]["ui.textScaleFactor"] == 100


@pytest.mark.unit
def test_two_paths_to_the_same_directory_are_one_directory(tmp_path):
    here = tmp_path / "browser"
    here.mkdir()
    assert device.same_directory(here, str(here))
    assert device.same_directory(here, here / "." )
    assert not device.same_directory(here, tmp_path / "other")


# --- the measurement ---------------------------------------------------------

@pytest.mark.unit
def test_the_device_the_seed_draws_is_the_device_that_is_declared():
    device_geometry = geometry.declared(1361434516)
    assert device_geometry["screen_width"] == 1920
    assert device_geometry["screen_height"] == 1080
    assert device_geometry["taskbar_px"] == 48
    assert device_geometry["chrome_w"] == 0
    assert device_geometry["chrome_h"] == 85


@pytest.mark.unit
def test_a_pin_moves_the_device_it_declares():
    device_geometry = geometry.declared(1361434516, {"screen.width": 1600,
                                                      "screen.dpr": 1.25})
    assert device_geometry["screen_width"] == 1600
    assert device_geometry["dpr"] == 1.25


@pytest.mark.unit
def test_what_a_page_must_answer_for_the_device_to_be_the_declared_one():
    wanted = geometry.expected(geometry.declared(1361434516))
    assert wanted["outerWidth"] == 1920
    assert wanted["innerWidth"] == 1920
    assert wanted["outerHeight"] == 1032
    assert wanted["innerHeight"] == 947
    assert wanted["screenWidth"] == 1920
    assert wanted["availHeight"] == 1032
    assert wanted["devicePixelRatio"] == 1.0


@pytest.mark.unit
def test_a_browser_that_is_the_device_has_no_differences():
    wanted = geometry.expected(geometry.declared(1361434516))
    assert geometry.differences(wanted, dict(wanted)) == []


@pytest.mark.unit
def test_an_outer_window_narrower_than_the_inner_one_is_caught():
    wanted = geometry.expected(geometry.declared(1361434516))
    live = dict(wanted, outerWidth=1678)
    problems = geometry.differences(wanted, live)
    assert any("outerWidth" in p for p in problems)


@pytest.mark.unit
def test_a_ratio_the_host_multiplied_into_the_profiles_is_caught():
    wanted = geometry.expected(geometry.declared(1361434516))
    live = dict(wanted, devicePixelRatio=1.1538461538461537)
    problems = geometry.differences(wanted, live)
    assert any("devicePixelRatio" in p for p in problems)


@pytest.mark.unit
def test_a_ratio_the_font_grid_moved_is_not_a_difference():
    # The engine snaps the scale to the grid its fonts live on, so 1.15 comes
    # out as 1.1538. That is the same device and not a seam.
    wanted = geometry.expected(geometry.declared(1361434516, {"screen.dpr": 1.15}))
    live = dict(wanted, devicePixelRatio=1.1538461538461537)
    assert geometry.differences(wanted, live) == []


@pytest.mark.unit
def test_the_report_says_which_way_round_it_is():
    wanted = geometry.expected(geometry.declared(1361434516))
    assert "is the device" in geometry.report(wanted, dict(wanted))
    live = dict(wanted, innerWidth=1900)
    assert "is NOT the device" in geometry.report(wanted, live)


@pytest.mark.unit
def test_a_session_is_measured_against_what_it_was_told(home):
    # A fake session, so the measurement is asked without a browser: the
    # numbers are the ones a real page answers, and the report has to come
    # out the way it comes out for a real one.
    wanted = geometry.expected(geometry.declared(1361434516))

    class _Page:
        async def goto(self, _url):
            return None

        async def evaluate(self, _script):
            return dict(wanted)

        async def close(self):
            return None

    class _Session(device.CheckedSession):
        def __init__(self):
            self._kwargs = {"seed": 1361434516, "pin": {}}

        async def new_page(self):
            return _Page()

    said = asyncio.run(_Session()._check())
    assert "is the device" in said


@pytest.mark.unit
def test_a_session_that_cannot_be_measured_is_not_a_broken_one(home):
    # A browser that cannot be asked is not a browser that is the wrong
    # device, and the report says nothing rather than guessing.
    class _Session(device.CheckedSession):
        def __init__(self):
            self._kwargs = {"seed": 1361434516, "pin": {}}

        async def new_page(self):
            raise RuntimeError("no page today")

    assert asyncio.run(_Session()._check()) == ""


# --- the command line ---------------------------------------------------------

def call(server, tool: str, arguments: dict | None = None) -> str:
    """One tool call, as the model makes it, and the text it answered with."""
    out = asyncio.run(server.mcp.call_tool(tool, arguments or {}))
    content = out[0] if isinstance(out, tuple) else out
    return "\n".join(getattr(item, "text", "") for item in content)


@pytest.mark.unit
def test_a_pin_is_written_down_by_a_flag(home):
    assert cli._profile_command(["add", "work", "--seed", "4242",
                                 "--pin", "screen.width=1920"]) == 0
    assert registry.get("work").pin == {"screen.width": 1920}


@pytest.mark.unit
def test_the_flag_may_be_written_more_than_once(home):
    assert cli._profile_command(["add", "work", "--seed", "4242",
                                 "--pin", "screen.width=1920",
                                 "--pin", "screen.dpr=1.25"]) == 0
    assert registry.get("work").pin == {"screen.width": 1920,
                                        "screen.dpr": 1.25}


@pytest.mark.unit
def test_an_empty_pin_takes_the_pins_off(home):
    cli._profile_command(["add", "work", "--seed", "4242",
                          "--pin", "screen.width=1920"])
    assert cli._profile_command(["set", "work", "--pin", ""]) == 0
    assert registry.get("work").pin == {}


@pytest.mark.unit
def test_a_pin_the_engine_would_refuse_is_refused_by_the_command_line(home, capsys):
    assert cli._profile_command(["add", "work", "--pin", "screen.nosuch=1"]) == 1
    assert "nosuch" in capsys.readouterr().out


@pytest.mark.unit
def test_the_device_is_shown_with_the_profile(home, capsys):
    cli._profile_command(["add", "work", "--seed", "1361434516"])
    cli._profile_command(["show", "work"])
    said = capsys.readouterr().out
    assert "1920x1080" in said
    assert "ratio 1.0" in said


@pytest.mark.unit
def test_the_tools_the_model_calls_take_a_pin(home):
    from invisible_playwright_mcp.mcp import server

    import dots.tools

    dots.tools.register(server)
    registry.create("work", seed=4242)
    said = call(server, "dots_profile_save",
                {"name": "work", "pin": "screen.width=1920"})
    assert "Saved" in said
    assert registry.get("work").pin == {"screen.width": 1920}


# --- the door the launcher asks through ---------------------------------------

@pytest.mark.unit
def test_the_device_check_has_a_door_of_its_own():
    # The launcher asks the running server over HTTP, so the door has to be
    # there - and it has to be a door of its own, because the wheel's is a
    # POST and this one is a question.
    from dots import live

    class _App:
        def __init__(self):
            self.routes = []

        def add_route(self, path, handler, methods=None):
            self.routes.append((path, methods))

    class _Routes:
        PAGE = "<html></body></html>"

        def build_app(self, sessions):
            return _App()

    routes = _Routes()
    live.install(routes)
    app = routes.build_app(None)
    assert (live.SCROLL_PATH, ["POST"]) in app.routes
    assert (live.DEVICE_PATH, ["GET"]) in app.routes
    assert (live.DEVICE_PROFILE_PATH, ["GET", "POST"]) in app.routes


@pytest.mark.unit
def test_the_page_carries_the_device_bar(home):
    # The bar is appended to the package's page, after its own script, so
    # that the page this build serves is the package's page plus what this
    # build adds.
    from invisible_playwright_mcp import ui

    from dots import live

    page = live.page_with_the_device(ui.PAGE)
    assert "dots-device" in page
    assert "dots-device-check" in page
    assert "dots-device-pins" in page
    # And it is AFTER the package's own script, not before it: the page this
    # build serves is the package's page plus what this build adds.
    assert page.rindex("dots-device") > ui.PAGE.rindex("</script>")


@pytest.mark.unit
def test_the_tool_answers_what_a_profile_claims(home):
    from invisible_playwright_mcp.mcp import server

    import dots.tools

    dots.tools.register(server)
    registry.create("work", seed=1361434516, pin={"screen.dpr": 1.25})
    said = call(server, "dots_profile_device", {"name": "work"})
    answer = json.loads(said)
    assert answer["name"] == "work"
    assert "1920x1080" in answer["device"]
    assert "ratio 1.25" in answer["device"]
    assert answer["pins"] == "screen.dpr=1.25"
