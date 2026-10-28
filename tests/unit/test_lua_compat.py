"""Exercise loader fallback in an actual Lua 5.1 interpreter."""

from importlib import import_module
from pathlib import Path

import pytest

from providers.resolve.lua_compat import with_loader
from providers.resolve.lua_transport import prepare

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError


def test_loadfile_fallback_preserves_results_and_globals(tmp_path: Path) -> None:
    script = tmp_path / "module.lua"
    script.write_text("return 23, 'loaded'", encoding="utf-8")
    lua = LuaRuntime(unpack_returned_tuples=True)
    lua.execute("dofile = nil")
    result = lua.execute(with_loader("return dofile(...)"), str(script))
    assert result == (23, "loaded")
    assert lua.globals().dofile is None


def test_missing_file_preserves_error(tmp_path: Path) -> None:
    lua = LuaRuntime()
    lua.execute("dofile = nil")
    with pytest.raises(LuaError, match="missing.lua"):
        lua.execute(with_loader("return dofile(...)"), str(tmp_path / "missing.lua"))


def test_no_loader_fails_explicitly() -> None:
    lua = LuaRuntime()
    lua.execute("dofile = nil; loadfile = nil")
    with pytest.raises(LuaError, match="RESOLVE_AGENT_FILE_LOADER_UNAVAILABLE"):
        lua.execute(with_loader("error('must not execute')"))


def test_prepared_modules_each_have_local_loader(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    for script in root.glob("*.lua"):
        if script.name != "request.lua":
            assert script.read_text(encoding="utf-8").startswith(
                "-- Resolve Agent local loader compatibility."
            )
