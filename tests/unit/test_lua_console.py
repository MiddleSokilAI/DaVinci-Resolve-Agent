"""Console mode runs with no global disk loaders and rejects stateful jobs."""

import json
from importlib import import_module
from pathlib import Path

import pytest

from providers.resolve.lua_console import (
    console_command,
    lua_source_literal,
)
from providers.resolve.lua_transport import lua_string, prepare

LuaRuntime = import_module("lupa.lua51").LuaRuntime


def request(root: Path, action: str) -> None:
    (root / "request.lua").write_text(
        "return {session="
        + lua_string(json.loads((root / "session.json").read_text())["session"])
        + ',id="'
        + "a" * 32
        + '",action='
        + lua_string(action)
        + ",expires=9999999999}",
        encoding="ascii",
    )


def test_read_request_without_any_disk_loader(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    prepare(root)
    request(root, "get_current_project")
    lua = LuaRuntime()
    lua.execute("""
        dofile=nil; loadfile=nil; exports=0
        bmd={wait=function() end}
        local p={GetName=function() return 'Voice' end}
        local pm={GetCurrentProject=function() return p end,
                  ExportProject=function() exports=exports+1; return true end}
        resolve={GetProjectManager=function() return pm end}
    """)
    command = console_command(root)
    assert "\n" not in command
    lua.execute(command)
    assert lua.globals().exports == 1
    lua.execute(command)
    assert lua.globals().exports == 1
    assert lua.globals().dofile is None
    assert lua.globals().ResolveAgentLuaRunning is None


def test_render_job_rejected(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    prepare(root)
    request(root, "start_render")
    with pytest.raises(ValueError, match="unsupported"):
        console_command(root)


def test_source_quotes_round_trip() -> None:
    source = 'return "\\folder"\n-- ]=] українська\t\r'
    assert LuaRuntime().execute("return " + lua_source_literal(source)) == source


@pytest.mark.parametrize("anchor", ["while true do", "local handled = {}"])
def test_changed_bridge_rejected_before_console_execution(
    tmp_path: Path, anchor: str
) -> None:
    root = tmp_path / "runtime"
    prepare(root)
    request(root, "get_current_project")
    script = root / "bridge.lua"
    script.write_text(
        script.read_text(encoding="utf-8").replace(anchor, "changed"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="Unsupported bridge source"):
        console_command(root)
