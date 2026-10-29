"""Real Lua 5.1 project creation guards and marker lifecycle ownership."""

from __future__ import annotations

import json
import struct
import zlib
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.marker_transport import prepare_marker

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError
SOURCES = Path(__file__).resolve().parents[2] / "bridges" / "resolve"
COMMAND = "2" * 32


class NativeCreation:
    """Exercise native API order with no MediaPool, loaders or UI dependency."""

    def __init__(self) -> None:
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.now = 100.0
        self.files: set[str] = set()
        self.exports: list[tuple[str, str]] = []
        self.next_request: dict[str, Any] | None = None
        self.root = "C:/Marker"
        globals_ = self.lua.globals()
        globals_.py_now = lambda: int(self.now)
        globals_.py_export = self.export
        globals_.py_exists = lambda path: path in self.files
        globals_.py_wait = self.wait

        def load_module(path: str) -> Any:
            name = (
                "ResolveLua"
                + "".join(part.title() for part in Path(path).stem.split("_"))
                + ".lua"
            )
            return self.lua.execute((SOURCES / name).read_text("utf-8-sig"))

        globals_.dofile = load_module
        self.lua.execute("""
            os.time=function() return py_now() end
            bmd={fileexists=function(path) return py_exists(path) end,
                 wait=function(seconds) py_wait(seconds) end}
            calls={}; failure=''; old_invalid=false
            local function record(value) calls[#calls+1]=value end
            local function make_project(name,id)
                return {GetName=function() return name end,
                    GetUniqueId=function() return id end,
                    IsRenderingInProgress=function() return false end}
            end
            old={GetName=function()
                    assert(not old_invalid,'OLD_PROJECT_REUSED'); return 'Existing'
                 end,
                 GetUniqueId=function()
                    assert(not old_invalid,'OLD_PROJECT_REUSED'); return 'old-id'
                 end,
                 IsRenderingInProgress=function() return rendering==true end}
            current=old; names={'Existing'}; rendering=false; saves=0
            pm={GetCurrentProject=function() return current end,
                GetProjectListInCurrentFolder=function() return names end,
                SaveProject=function()
                    saves=saves+1; record('save')
                    if failure=='pre_save' and saves==1 then return false end
                    if failure=='post_save' and saves==2 then return false end
                    if failure=='save_raises' and saves==2 then
                        error('native save failed')
                    end
                    if failure=='after_save_switch' and saves==2 then
                        current=make_project('Unrelated','unrelated-id')
                    end
                    return true
                end,
                ExportProject=function(_,name,path)
                    record('export:'..name)
                    if path:match('%.before%.drp$') then
                        if failure=='backup' then return false end
                        if failure=='pre_switch' then
                            current=make_project('Unrelated','unrelated-id')
                        elseif failure=='render_after_backup' then rendering=true
                        elseif failure=='expired' then py_advance() end
                    end
                    return py_export(name,path)
                end,
                CreateProject=function(_,name)
                    record('create:'..name)
                    if failure=='create_none' then return nil end
                    local identity=failure=='same_id' and 'old-id' or 'created-id'
                    if failure=='invalid_id' then identity='bad_id' end
                    if failure=='empty_id' then identity='' end
                    local actual_name=failure=='wrong_name' and 'Wrong' or name
                    local result=make_project(actual_name,identity)
                    if failure~='not_selected' then
                        current=result; old_invalid=true
                    end
                    if failure=='wrong_current' then
                        current=make_project('Unrelated','unrelated-id')
                    end
                    if failure=='create_raises' then
                        error('opaque mutation failure')
                    end
                    if failure=='invalid_return' then return true end
                    if failure=='readback_raises' then
                        result.GetUniqueId=function()
                            error('native identity failed')
                        end
                    end
                    return result
                end}
            resolve={GetProjectManager=function() return pm end}
        """)
        globals_.py_advance = lambda: setattr(self, "now", 131.0)
        self.shared = self.lua.table()
        self.edit = self.lua.execute(
            (SOURCES / "ResolveLuaEditing.lua").read_text("utf-8-sig")
        )(globals_.resolve, self.root, self.lua.table(), self.shared)

    def export(self, name: str, path: str) -> bool:
        self.files.add(path)
        self.exports.append((name, path))
        return True

    def wait(self, seconds: float) -> None:
        self.now += seconds
        if self.next_request is not None:
            self.publish(self.next_request)
            self.next_request = None
        assert self.now < 140, "Unbounded native loop"

    def request(self, name: str = "Новий проєкт", **changes: Any) -> Any:
        request = {
            "id": COMMAND,
            "action": "create_project",
            "arguments": self.lua.table_from({"name": name}),
            "project_id": "old-id",
            "confirm": True,
            "expires": 130,
        }
        request.update(changes)
        return self.lua.table_from(request)

    def dispatch(self, name: str = "Новий проєкт", **changes: Any) -> str:
        return str(
            self.edit(
                self.lua.globals().pm,
                self.lua.globals().old,
                self.request(name, **changes),
            )
        )

    def calls(self) -> list[str]:
        values = self.lua.globals().calls
        return [values[index] for index in range(1, len(values) + 1)]

    def publish(self, request: dict[str, Any]) -> None:
        payload = json.dumps(
            request, ensure_ascii=False, separators=(",", ":")
        ).encode()
        packet = (
            struct.pack("<BBHI", 77, 1, len(payload), zlib.adler32(payload)) + payload
        )
        self.files = {path for path in self.files if "/inbox/" not in path}
        self.files.add(self.root + "/inbox/ready.marker")
        for index, byte in enumerate(packet):
            for bit in range(8):
                if byte & 1 << bit:
                    self.files.add(self.root + f"/inbox/b{index:05d}_{bit}.bit")

    def bundle(self, root: Path, *, stop: bool = False) -> None:
        script = prepare_marker(root, lease_seconds=5)
        self.root = root.as_posix()
        metadata = json.loads((root / "session.json").read_text("utf-8"))
        request = {
            "session": metadata["session"],
            "id": COMMAND,
            "action": "create_project",
            "expires": 130,
            "confirm": True,
            "project_id": "old-id",
            "arguments": {"name": "Новий проєкт"},
        }
        self.publish(request)
        if stop:
            self.next_request = {
                "session": metadata["session"],
                "id": "3" * 32,
                "action": "stop",
                "expires": 130,
            }
        self.lua.execute("dofile=nil; loadfile=nil; loadstring=nil")
        self.lua.execute(script.read_text("utf-8"))


def test_creation_preserves_source_before_switch_without_media_pool() -> None:
    native = NativeCreation()
    assert native.dispatch() == "project_created-id"
    assert native.calls() == ["save", "export:Existing", "create:Новий проєкт", "save"]
    assert native.exports == [("Existing", "C:/Marker/" + COMMAND + ".before.drp")]
    assert native.shared.project_id == "created-id"
    assert native.lua.globals().current.GetName() == "Новий проєкт"


@pytest.mark.parametrize(
    "name", ["", " ", " padded", "padded ", "a\x00", "a\x1f", "a\x7f", "я" * 129]
)
def test_invalid_names_fail_before_backup(name: str) -> None:
    native = NativeCreation()
    with pytest.raises(LuaError, match="INVALID_ARGUMENTS"):
        native.dispatch(name)
    assert native.calls() == []


@pytest.mark.parametrize("name", ["я" * 128, "😀" * 128])
def test_unicode_character_bound_is_not_a_byte_limit(name: str) -> None:
    assert NativeCreation().dispatch(name) == "project_created-id"


@pytest.mark.parametrize("locale_name", ["C", "English_United States.1252"])
def test_unicode_creation_is_independent_of_native_locale(locale_name: str) -> None:
    native = NativeCreation()
    setlocale = native.lua.eval("os.setlocale")
    previous = setlocale(None, "ctype")
    if not setlocale(locale_name, "ctype"):
        pytest.skip(f"Native locale {locale_name} is unavailable")
    try:
        assert native.dispatch() == "project_created-id"
    finally:
        setlocale(previous, "ctype")


@pytest.mark.parametrize("name", ["Existing", "EXISTING", "existing"])
def test_ascii_case_collisions_fail_before_backup(name: str) -> None:
    native = NativeCreation()
    with pytest.raises(LuaError, match="NAME_EXISTS"):
        native.dispatch(name)
    assert native.calls() == []


def test_exact_unicode_collision_fails_before_backup() -> None:
    native = NativeCreation()
    native.lua.globals().names = native.lua.table_from(["Новий проєкт"])
    with pytest.raises(LuaError, match="NAME_EXISTS"):
        native.dispatch()
    assert native.calls() == []


@pytest.mark.parametrize(
    "changes,error",
    [
        ({"confirm": False}, "INVALID_ARGUMENTS"),
        ({"project_id": "unrelated"}, "PROJECT_CHANGED"),
    ],
)
def test_identity_and_confirmation_precede_all_api_writes(
    changes: dict[str, Any], error: str
) -> None:
    native = NativeCreation()
    with pytest.raises(LuaError, match=error):
        native.dispatch(**changes)
    assert native.calls() == []


def test_extra_arguments_and_missing_api_are_rejected_before_backup() -> None:
    native = NativeCreation()
    with pytest.raises(LuaError, match="INVALID_ARGUMENTS"):
        native.dispatch(
            arguments=native.lua.table_from({"name": "New", "overwrite": True})
        )
    native.lua.execute("pm.CreateProject=nil")
    with pytest.raises(LuaError, match="CREATE_PROJECT_UNAVAILABLE"):
        native.dispatch()
    assert native.calls() == []


@pytest.mark.parametrize(
    "names", [False, {"extra": "Project"}, {2: "Project"}, [False]]
)
def test_invalid_library_listing_is_not_treated_as_empty(names: Any) -> None:
    native = NativeCreation()
    native.lua.globals().names = (
        native.lua.table_from(names) if isinstance(names, (dict, list)) else names
    )
    with pytest.raises(LuaError, match="PROJECT_LIST_UNAVAILABLE"):
        native.dispatch()
    assert native.calls() == []


@pytest.mark.parametrize(
    "failure,error",
    [
        ("pre_save", "BACKUP_SAVE_FAILED"),
        ("backup", "BACKUP_EXPORT_FAILED"),
        ("pre_switch", "PROJECT_CHANGED"),
        ("render_after_backup", "RENDERING"),
        ("expired", "EXPIRED"),
    ],
)
def test_preflight_or_backup_failures_do_not_create(failure: str, error: str) -> None:
    native = NativeCreation()
    native.lua.globals().failure = failure
    with pytest.raises(LuaError, match=error):
        native.dispatch()
    assert not any(call.startswith("create:") for call in native.calls())


def test_rendering_rejected_before_backup() -> None:
    native = NativeCreation()
    native.lua.globals().rendering = True
    with pytest.raises(LuaError, match="RENDERING"):
        native.dispatch()
    assert native.calls() == []


@pytest.mark.parametrize(
    "failure,error",
    [
        ("create_none", "CREATE_PROJECT_FAILED"),
        ("same_id", "VERIFY_FAILED"),
        ("create_raises", "CREATE_PROJECT_FAILED"),
        ("invalid_return", "VERIFY_FAILED"),
        ("readback_raises", "VERIFY_FAILED"),
        ("save_raises", "POST_SAVE_FAILED"),
        ("invalid_id", "VERIFY_FAILED"),
        ("empty_id", "VERIFY_FAILED"),
        ("wrong_name", "VERIFY_FAILED"),
        ("not_selected", "VERIFY_FAILED"),
        ("wrong_current", "VERIFY_FAILED"),
        ("post_save", "POST_SAVE_FAILED"),
        ("after_save_switch", "VERIFY_FAILED"),
    ],
)
def test_after_create_failures_remain_uncertain(failure: str, error: str) -> None:
    native = NativeCreation()
    native.lua.globals().failure = failure
    with pytest.raises(LuaError, match=error):
        native.dispatch()
    assert sum(call.startswith("create:") for call in native.calls()) == 1


@pytest.mark.parametrize("stop", [True, False])
def test_marker_creation_ack_and_lifecycle_follow_owned_new_identity(
    tmp_path: Path, stop: bool
) -> None:
    native = NativeCreation()
    native.bundle(tmp_path / "runtime", stop=stop)
    assert sum(call.startswith("create:") for call in native.calls()) == 1
    assert (
        "Новий проєкт",
        native.root + "/" + COMMAND + ".ok_project_created-id.drp",
    ) in native.exports
    reason = "acknowledged_stop" if stop else "lease_expired"
    assert native.exports[-1] == (
        "Новий проєкт",
        native.root + "/marker-exit-" + reason + ".drp",
    )
    assert native.lua.globals().ResolveAgentLuaRunning is None
    assert native.lua.globals().ResolveAgentMarkerSession is None


def test_marker_lifecycle_preserves_owned_creation_after_post_save_failure(
    tmp_path: Path,
) -> None:
    native = NativeCreation()
    native.lua.globals().failure = "post_save"
    native.bundle(tmp_path / "runtime")
    assert (
        "Новий проєкт",
        native.root + "/" + COMMAND + ".error_POST_SAVE_FAILED.drp",
    ) in native.exports
    assert native.exports[-1] == (
        "Новий проєкт",
        native.root + "/marker-exit-lease_expired.drp",
    )
