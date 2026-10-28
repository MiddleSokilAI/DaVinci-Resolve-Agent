"""Public connection and menu installation boundaries of the Free backend."""

from pathlib import Path

import pytest

from mcp_server.free_marker import (
    MENU_FILE,
    MENU_HEADER,
    create_server,
    install_menu_script,
)
from providers.resolve.lua_transport import prepare


def test_free_backend_refuses_a_file_loader_session(tmp_path: Path) -> None:
    root = tmp_path / "session"
    prepare(root)
    with pytest.raises(ValueError, match="marker session"):
        create_server(root)


def test_install_preserves_unmanaged_user_script(tmp_path: Path) -> None:
    runtime, scripts = tmp_path / "session", tmp_path / "Utility"
    runtime.mkdir()
    scripts.mkdir()
    (runtime / "marker-bridge.lua").write_text("return\n", encoding="utf-8")
    target = scripts / MENU_FILE
    target.write_text("-- user's own script\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unmanaged"):
        install_menu_script(runtime, scripts)
    assert target.read_text(encoding="utf-8") == "-- user's own script\n"


def test_install_updates_only_managed_script(tmp_path: Path) -> None:
    runtime, scripts = tmp_path / "session", tmp_path / "Utility"
    runtime.mkdir()
    scripts.mkdir()
    (runtime / "marker-bridge.lua").write_text("return\n", encoding="utf-8")
    other = scripts / "personal.lua"
    other.write_text("-- personal\n", encoding="utf-8")
    target = install_menu_script(runtime, scripts)
    assert target.name == MENU_FILE
    assert target.read_text(encoding="utf-8") == MENU_HEADER + "return\n"
    (runtime / "marker-bridge.lua").write_text("return true\n", encoding="utf-8")
    install_menu_script(runtime, scripts)
    assert target.read_text(encoding="utf-8") == MENU_HEADER + "return true\n"
    assert other.read_text(encoding="utf-8") == "-- personal\n"
