"""`dots` is the interface of the pinned invisible-playwright-mcp, and only that.

These tests are what a bump of the pin is judged by: the package's command
group is not a published API, so the day it changes shape is the day one of
these goes red.
"""
from __future__ import annotations

import importlib.metadata
import os
import re
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

import dots.cli

ROOT = Path(__file__).resolve().parents[1]


def _run(*args: str, cwd: Path, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "dots", *args],
        cwd=cwd, env=env, capture_output=True, text=True, timeout=120,
    )


def _clean_env(home: Path) -> dict:
    """This environment without a model key, and with its own data directory."""
    env = {k: v for k, v in os.environ.items()
           if k.upper() not in {"OPENROUTER_API_KEY", "OPENAI_API_KEY",
                                "OPENAI_BASE_URL", "OPENAI_MODEL",
                                # ⛔ A PROFILE NAME IS NOW CONFIG TOO, AND IT
                                # REWRITES THE ARGV. `dots.cli` resolves
                                # `DOTS_PROFILE` into the flags the package
                                # declares, so a developer's shell carrying one
                                # would decide what the group is called with -
                                # the same reason the key names are removed.
                                "DOTS_PROFILE"}}
    env["INVISIBLE_MCP_HOME"] = str(home)
    env["PYTHONPATH"] = str(ROOT / "src")
    return env


@pytest.fixture
def clean_provider_env(monkeypatch):
    """A process env with no provider config, restored between tests.

    The four names `_apply_provider_overrides` reads must be absent for the
    tests to be deterministic. Without the removal, a key a developer has in
    their shell would override the assertions here, and what the test sees as
    a default would be what their shell exports.

    `DOTS_PROFILE` is in the same list for a sharper reason: `main` reads
    `.env` into `os.environ` itself, without monkeypatch, so a name that gets
    in from a developer's file stays in for every test after it. The argv a
    test asserts is then decided by whatever is on this machine.
    """
    for name in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
                 "OPENROUTER_API_KEY", "INVISIBLE_MCP_MODEL",
                 "DOTS_PROFILE"):
        monkeypatch.delenv(name, raising=False)
    # ⛔ AND THE NAMES `dots` TRANSLATES INTO, because the translation
    # happens in the environment - that is how the browser server inherits it
    # - and a test that called `main` leaves them behind for the next one.
    for name in ("STEALTHFOX_PROXY", "STEALTHFOX_SEED",
                 "STEALTHFOX_PROFILE_DIR", "STEALTHFOX_HEADLESS"):
        monkeypatch.delenv(name, raising=False)
    return monkeypatch


# ⛔ PACKAGE DEFAULTS, CAPTURED AT MODULE-IMPORT TIME BEFORE ANY TEST RUNS.
# The very first test that calls `dots.cli.main` mutates `invisible_playwright_mcp.llm`
# (the `OPENAI_BASE_URL` env the developer has in their shell hits the
# provider override). A fixture that captured `originals` lazily on first
# use would save the already-mutated values and "restore" them back to
# the broken state - the test that asserts "no patch when no overrides"
# would then fail forever. The defaults are constants - one capture at
# import is the only shape that keeps the test honest.
import invisible_playwright_mcp.cli as _ipm_cli
import invisible_playwright_mcp.llm as _ipm_llm
_PACKAGE_ORIGINALS: dict[tuple[str, str], object] = {
    ("llm", "BASE_URL"): _ipm_llm.BASE_URL,
    ("llm", "APP_URL"): _ipm_llm.APP_URL,
    ("llm", "APP_TITLE"): _ipm_llm.APP_TITLE,
    ("cli", "BASE_URL"): _ipm_cli.BASE_URL,
}


@pytest.fixture
def restored_module_attrs():
    """Restore the package attributes the overrides may rewrite.

    `_apply_provider_overrides` patches `BASE_URL` on both `llm` and `cli`
    (the latter because `cli` imported it by name at module load) and the
    attribution headers on `llm`. A test that calls it has to put them back
    or the next test inherits the change.
    """
    try:
        yield _PACKAGE_ORIGINALS
    finally:
        for (module_name, attr), value in _PACKAGE_ORIGINALS.items():
            mod = _ipm_cli if module_name == "cli" else _ipm_llm
            setattr(mod, attr, value)


@pytest.mark.unit
def test_the_group_is_called_with_ui_in_front(monkeypatch, clean_provider_env):
    # Through the GROUP, because the group is where `.env` is read. A `dots`
    # that called the `ui` command on its own would pass every other test here
    # and never find a key kept in a file.
    #
    # ⛔ IN AN EMPTY DIRECTORY, OR THIS TEST DECIDES ITS OWN ANSWER FROM THE
    # DEVELOPER'S `.env`. `main` loads that file into `os.environ` directly -
    # not through `monkeypatch` - so a `DOTS_PROFILE` in the file at the
    # repository root would add its flags to the argv below and then stay in
    # the environment for every test that runs after this one.
    monkeypatch.chdir(ROOT / "tests")
    seen = {}

    def group(*, args, prog_name):
        seen["args"], seen["prog_name"] = args, prog_name

    monkeypatch.setattr("invisible_playwright_mcp.cli.main", group)
    dots.cli.main(["--port", "9000", "--headed"])
    # ⛔ THE ARGV MAY CARRY A `--binary`, AND THAT IS NOT A CHANGE TO WHAT WAS
    # ASKED FOR. An engine this machine has already checked is named to the
    # servers, because the package checks the engine again in every process
    # it starts and that is nineteen seconds of hashing a 250 MB file - once
    # per process. The flags the caller typed are still there, in the same
    # order, behind it.
    args = seen["args"]
    assert args[0] == "ui"
    assert seen["prog_name"] == "dots"
    assert args[-3:] == ["--port", "9000", "--headed"]
    if "--binary" in args:
        engine = args[args.index("--binary") + 1]
        assert Path(engine).is_file()


@pytest.mark.integration
def test_help_is_the_interface_help(tmp_path):
    done = _run("--help", cwd=tmp_path, env=_clean_env(tmp_path / "home"))
    assert done.returncode == 0, done.stderr
    assert "Usage: dots" in done.stdout
    for option in ("--openrouter-key", "--model", "--proxy", "--seed", "--profile-dir"):
        assert option in done.stdout


@pytest.mark.integration
def test_without_a_key_it_refuses_before_starting_anything(tmp_path):
    done = _run(cwd=tmp_path, env=_clean_env(tmp_path / "home"))
    assert done.returncode != 0
    assert "OpenRouter" in done.stdout + done.stderr


@pytest.mark.unit
def test_the_installed_package_is_the_pinned_one():
    # An editable install of a sibling checkout would make every test above
    # judge code that `uvx` never delivers.
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    (line,) = [d for d in pyproject["project"]["dependencies"]
               if d.startswith("invisible-playwright-mcp")]
    pinned = re.fullmatch(r"invisible-playwright-mcp==(\S+)", line).group(1)
    assert importlib.metadata.version("invisible_playwright_mcp") == pinned


# --- provider overrides ---------------------------------------------------------
#
# What these exercise is the layer `dots` adds on top of the pinned package:
# reading `OPENAI_*` names, and rewriting the two module attributes the package
# hardcoded to OpenRouter. They are unit tests because they judge a pure
# function: no subprocess, no browser, no model call.

@pytest.mark.unit
def test_load_env_file_applies_unset_names(tmp_path, clean_provider_env):
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-test\nOPENAI_MODEL=gpt-4o\n",
                    encoding="utf-8")
    dots.cli._load_env_file(tmp_path)
    assert os.environ["OPENAI_API_KEY"] == "sk-test"
    assert os.environ["OPENAI_MODEL"] == "gpt-4o"


@pytest.mark.unit
def test_load_env_file_does_not_override_what_is_already_set(
        tmp_path, clean_provider_env):
    clean_provider_env.setenv("OPENAI_API_KEY", "from-shell")
    path = tmp_path / ".env"
    path.write_text("OPENAI_API_KEY=sk-test\n", encoding="utf-8")
    dots.cli._load_env_file(tmp_path)
    # The shell's value wins; the file's never reaches the environment.
    assert os.environ["OPENAI_API_KEY"] == "from-shell"


@pytest.mark.unit
def test_load_env_file_is_silent_when_the_file_is_absent(
        tmp_path, clean_provider_env):
    # `dots` boots either way; a missing `.env` is a no-op, not an error.
    dots.cli._load_env_file(tmp_path)
    assert "OPENAI_API_KEY" not in os.environ


@pytest.mark.unit
def test_openai_api_key_is_mirrored_into_openrouter_api_key(
        clean_provider_env, restored_module_attrs):
    clean_provider_env.setenv("OPENAI_API_KEY", "sk-test")
    dots.cli._apply_provider_overrides()
    # The name the package's `resolve_key` reads is now set too. A `dots`
    # boot that only ever knew OpenRouter still finds the key.
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test"


@pytest.mark.unit
def test_openrouter_api_key_set_in_the_shell_is_not_overridden(
        clean_provider_env, restored_module_attrs):
    clean_provider_env.setenv("OPENAI_API_KEY", "sk-fresh")
    clean_provider_env.setenv("OPENROUTER_API_KEY", "sk-kept")
    dots.cli._apply_provider_overrides()
    # Shell > file. A user who already has the legacy name exported does not
    # have it silently rewritten by the new mirror.
    assert os.environ["OPENROUTER_API_KEY"] == "sk-kept"


@pytest.mark.unit
def test_openai_model_is_mirrored_into_invisible_mcp_model(
        clean_provider_env, restored_module_attrs):
    clean_provider_env.setenv("OPENAI_MODEL", "gpt-4o")
    dots.cli._apply_provider_overrides()
    # The package's `resolve_model` reads this name, so a model typed under
    # the SDK's conventional name reaches the same lookup.
    assert os.environ["INVISIBLE_MCP_MODEL"] == "gpt-4o"


@pytest.mark.unit
def test_openai_base_url_overrides_the_package_endpoint(
        clean_provider_env, restored_module_attrs):
    import invisible_playwright_mcp.cli as ipm_cli
    import invisible_playwright_mcp.llm as llm

    clean_provider_env.setenv("OPENAI_BASE_URL", "https://api.example.com/v1")
    clean_provider_env.setenv("OPENAI_API_KEY", "sk-test")
    dots.cli._apply_provider_overrides()

    # Both modules: `cli` imported `BASE_URL` by name at load, so its copy
    # is its own reference. Patching `llm` alone would leave the boot line
    # printing the old URL.
    assert llm.BASE_URL == "https://api.example.com/v1"
    assert ipm_cli.BASE_URL == "https://api.example.com/v1"
    # The attribution headers belong to OpenRouter's app rankings and would
    # be a misleading line in another provider's request log; they go.
    assert llm.APP_URL == "" and llm.APP_TITLE == ""


@pytest.mark.unit
def test_openai_base_url_trailing_slash_is_stripped(
        clean_provider_env, restored_module_attrs):
    import invisible_playwright_mcp.llm as llm

    clean_provider_env.setenv("OPENAI_BASE_URL", "https://api.example.com/v1/")
    dots.cli._apply_provider_overrides()
    # The SDK joins end and path itself; a doubled slash is a 404 that
    # explains itself poorly.
    assert llm.BASE_URL == "https://api.example.com/v1"


# --- the key, from wherever you are ------------------------------------------

@pytest.mark.unit
def test_the_key_is_found_in_a_folder_with_no_env_of_its_own(
        tmp_path, monkeypatch):
    # ⛔ A KEY IS A PROPERTY OF A PERSON, NOT OF A FOLDER. It used to be read
    # only from the directory the command was run in, so `dots` in another
    # folder found no key and refused to start - which reads as a broken
    # program and is not.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "home" / ".env").write_text("OPENAI_API_KEY=sk-personal\n",
                                            encoding="utf-8")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    dots.cli._load_every_env_file(elsewhere)
    assert os.environ["OPENAI_API_KEY"] == "sk-personal"


@pytest.mark.unit
def test_a_projects_own_key_beats_the_personal_one(tmp_path, monkeypatch):
    # The project's file is read FIRST, so it owns every name it mentions, and
    # the personal file can only fill in what the project did not say.
    monkeypatch.setenv("INVISIBLE_MCP_HOME", str(tmp_path / "home"))
    (tmp_path / "home").mkdir(parents=True)
    (tmp_path / "home" / ".env").write_text(
        "OPENAI_API_KEY=sk-personal\nOPENAI_MODEL=personal-model\n",
        encoding="utf-8")
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("OPENAI_API_KEY=sk-project\n", encoding="utf-8")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_MODEL", raising=False)

    dots.cli._load_every_env_file(project)
    assert os.environ["OPENAI_API_KEY"] == "sk-project"
    # And the personal file still supplies what the project did not.
    assert os.environ["OPENAI_MODEL"] == "personal-model"


@pytest.mark.unit
def test_the_names_the_launcher_writes_are_where_the_package_reads(monkeypatch):
    # ⛔ THE LAUNCHER WROTE FIVE NAMES INTO `.env` AND THE PACKAGE READ
    # NONE OF THEM. A person who set a profile directory in the `.env` - which
    # is what the launcher writes - and ran `dots` from a terminal got a
    # browser with NO profile: an ephemeral one, which keeps no cookies, no
    # logins and no fingerprint between runs. The setting was in the file, the
    # file was read, and the setting was thrown away.
    env = {"INVISIBLE_MCP_PROXY": "http://p:1",
           "INVISIBLE_MCP_SEED": "4242",
           "INVISIBLE_MCP_PROFILE_DIR": r"C:\somewhere\browser"}
    dots.cli._translate_env_names(env)
    assert env["STEALTHFOX_PROXY"] == "http://p:1"
    assert env["STEALTHFOX_SEED"] == "4242"
    assert env["STEALTHFOX_PROFILE_DIR"] == r"C:\somewhere\browser"


@pytest.mark.unit
def test_the_headed_name_is_the_other_way_round(monkeypatch):
    # The launcher's name says "headed" and the package's says "headless", so
    # one of them has to be inverted. Getting it wrong runs a browser with a
    # window when the person asked for one without.
    env = {"INVISIBLE_MCP_HEADED": "1"}
    dots.cli._translate_env_names(env)
    assert env["STEALTHFOX_HEADLESS"] == "0"
    env = {"INVISIBLE_MCP_HEADED": "0"}
    dots.cli._translate_env_names(env)
    assert env["STEALTHFOX_HEADLESS"] == "1"


@pytest.mark.unit
def test_the_shell_beats_the_file_when_it_says_the_same_thing():
    env = {"INVISIBLE_MCP_PROXY": "http://from-file:1",
           "STEALTHFOX_PROXY": "http://from-shell:1"}
    dots.cli._translate_env_names(env)
    assert env["STEALTHFOX_PROXY"] == "http://from-shell:1"


@pytest.mark.unit
def test_a_profile_directory_in_the_environment_reaches_the_browser():
    # The end of the chain: the name the package reads is the one the browser
    # is launched with, and a profile directory is what makes the browser
    # KEEP anything between runs.
    from invisible_playwright_mcp.mcp import plan

    env = {"STEALTHFOX_PROFILE_DIR": r"C:\somewhere\browser"}
    chosen = plan.plan_session(profile=None, env=env)
    assert chosen.kwargs["profile_dir"] == r"C:\somewhere\browser"


@pytest.mark.unit
def test_without_provider_overrides_the_endpoint_is_unchanged(
        clean_provider_env, restored_module_attrs):
    import invisible_playwright_mcp.cli as ipm_cli
    import invisible_playwright_mcp.llm as llm

    original = llm.BASE_URL
    dots.cli._apply_provider_overrides()
    # No `OPENAI_*` names, no patch. OpenRouter remains the default.
    assert llm.BASE_URL == original
    assert ipm_cli.BASE_URL == original
    # And the attribution headers survive for an OpenRouter install.
    assert llm.APP_URL != ""
    assert llm.APP_TITLE != ""


@pytest.mark.unit
def test_main_runs_the_group_after_applying_overrides(
        monkeypatch, clean_provider_env, restored_module_attrs):
    # End-to-end shape: `dots` reads `.env`, applies the overrides, then
    # hands the argv to the package's group - in that order, with the group
    # last because the group is what announces what the file applied.
    seen = {}

    def group(*, args, prog_name):
        seen["args"] = args
        seen["prog_name"] = prog_name

    monkeypatch.setattr("invisible_playwright_mcp.cli.main", group)
    monkeypatch.chdir(ROOT / "tests")  # an empty directory: no `.env`

    clean_provider_env.setenv("OPENAI_API_KEY", "sk-test")
    dots.cli.main(["--port", "9000"])

    # The same as the test above: a `--binary` may be named, and the flags
    # the caller typed are still there behind it.
    args = seen["args"]
    assert args[0] == "ui"
    assert args[-2:] == ["--port", "9000"]
    assert seen["prog_name"] == "dots"
    # And the key reached the name the package reads.
    assert os.environ["OPENROUTER_API_KEY"] == "sk-test"