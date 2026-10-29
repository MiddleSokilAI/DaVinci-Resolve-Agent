"""Read-only context guards execute in Lua 5.1 and the compiler-free bundle."""

from __future__ import annotations

import json
import struct
import zlib
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_project_context import validate_project_context_request
from providers.resolve.marker_transport import prepare_marker

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError
SOURCE = (
    Path(__file__).resolve().parents[2] / "bridges/resolve/ResolveLuaProjectContext.lua"
)
COMMAND = "2" * 32
ARGUMENTS: dict[str, Any] = {
    "expected_database": {"DbType": "Disk", "DbName": "Library — Україна"},
    "expected_folder": "",
    "expected_project_name": "Existing project — Україна",
}


class Native:
    """Expose only bounded native reads and response exports, never editing."""

    def __init__(self) -> None:
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.files: set[str] = set()
        self.exports: list[str] = []
        self.now = 100.0
        self.root = "C:/Context"
        self.lua.globals().py_export = self.export
        self.lua.globals().py_exists = lambda path: path in self.files
        self.lua.globals().py_now = lambda: int(self.now)
        self.lua.globals().py_wait = self.wait
        self.lua.execute("""
            calls={database=0,folder=0,project=0,list=0,names=0}
            database={DbType='Disk',DbName='Library — Україна'}
            databases={database}
            names={'Existing project — Україна'}
            folder=''; failure=''; missing=false
            project={GetUniqueId=function() return 'project-id' end,
                GetName=function() return 'Existing project — Україна' end}
            current=project
            pm={GetCurrentDatabase=function()
                    calls.database=calls.database+1
                    if failure=='database_race' and calls.database==2 then
                        return {DbType='Disk',DbName='Foreign'}
                    end
                    return database
                end,
                GetDatabaseList=function() calls.list=calls.list+1;return databases end,
                GetProjectListInCurrentFolder=function()
                    calls.names=calls.names+1
                    if failure=='names_race' and calls.names==2 then return {} end
                    return names
                end,
                GetCurrentFolder=function()
                    calls.folder=calls.folder+1
                    if failure=='folder_race' and calls.folder==2 then
                        return 'Foreign'
                    end
                    return folder
                end,
                GetCurrentProject=function()
                    calls.project=calls.project+1
                    if missing then return nil end
                    if failure=='project_race' and calls.project==2 then
                        return {GetName=function() return 'Foreign' end,
                            GetUniqueId=function() return 'foreign-id' end}
                    end
                    return current
                end,
                ExportProject=function(_,_,path) return py_export(path) end,
                SaveProject=function() error('MUTATION_SAVE') end,
                LoadProject=function() error('MUTATION_LOAD') end,
                SetCurrentDatabase=function() error('MUTATION_DATABASE') end,
                OpenFolder=function() error('MUTATION_FOLDER') end}
            resolve={GetProjectManager=function() return pm end}
            bmd={fileexists=function(path) return py_exists(path) end,
                wait=function(seconds) py_wait(seconds) end}
            os.time=function() return py_now() end
        """)
        self.verify = self.lua.execute(SOURCE.read_text(encoding="utf-8-sig"))

    def export(self, path: str) -> bool:
        self.files.add(path)
        self.exports.append(path)
        return True

    def wait(self, seconds: float) -> None:
        self.now += seconds
        assert self.now < 110, "Native bundle did not return after its finite lease"

    def arguments(self, value: dict[str, Any] | None = None) -> Any:
        value = ARGUMENTS if value is None else value
        return self.lua.table_from(value, recursive=True)

    def request(self, **changes: Any) -> Any:
        value: dict[str, Any] = {
            "id": COMMAND,
            "action": "verify_project_context",
            "project_id": "project-id",
            "arguments": self.arguments(),
            "expires": 130,
        }
        value.update(changes)
        return self.lua.table_from(value)

    def dispatch(self, **changes: Any) -> str:
        return str(
            self.verify(
                self.lua.globals().pm,
                self.lua.globals().project,
                self.request(**changes),
            )
        )

    def bundle(self, root: Path, *, bad_database: bool = False) -> None:
        script = prepare_marker(root, lease_seconds=5)
        self.root = root.as_posix()
        metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
        envelope = {
            "session": metadata["session"],
            "id": COMMAND,
            "action": "verify_project_context",
            "project_id": "project-id",
            "arguments": ARGUMENTS,
            "expires": 130,
        }
        payload = json.dumps(envelope, ensure_ascii=False).encode("utf-8")
        packet = (
            struct.pack("<BBHI", 77, 1, len(payload), zlib.adler32(payload)) + payload
        )
        self.files.add(self.root + "/inbox/ready.marker")
        for index, byte in enumerate(packet):
            for bit in range(8):
                if byte & 1 << bit:
                    self.files.add(self.root + f"/inbox/b{index:05d}_{bit}.bit")
        if bad_database:
            self.lua.execute("database.DbName='Wrong library'")
        self.lua.execute(
            "dofile=nil; loadfile=nil; loadstring=nil; require=nil; io=nil"
        )
        self.lua.execute(script.read_text(encoding="utf-8"))


def test_exact_context_is_checked_twice_without_mutation() -> None:
    native = Native()
    assert native.dispatch() == "context_verified"
    calls = native.lua.globals().calls
    assert (
        calls.database
        == calls.list
        == calls.folder
        == calls.project
        == calls.names
        == 2
    )
    assert native.exports == []


def test_postgresql_context_requires_exact_address() -> None:
    native = Native()
    native.lua.execute("database.DbType='PostgreSQL'; database.IpAddress='127.0.0.1'")
    arguments = dict(
        ARGUMENTS,
        expected_database={
            "DbType": "PostgreSQL",
            "DbName": "Library — Україна",
            "IpAddress": "127.0.0.1",
        },
    )
    assert native.dispatch(arguments=native.arguments(arguments)) == "context_verified"
    arguments["expected_database"]["IpAddress"] = "192.0.2.1"
    with pytest.raises(LuaError, match="DATABASE_MISMATCH"):
        native.dispatch(arguments=native.arguments(arguments))


@pytest.mark.parametrize(
    "change,code",
    [
        ("database.DbName='Other'", "DATABASE_MISMATCH"),
        ("databases={database,database}", "DATABASE_AMBIGUOUS"),
        ("databases={}", "DATABASE_AMBIGUOUS"),
        ("folder='Nested'", "FOLDER_MISMATCH"),
        ("project.GetName=function() return 'Other' end", "PROJECT_MISMATCH"),
        ("project.GetUniqueId=function() return 'Other' end", "PROJECT_MISMATCH"),
        ("missing=true", "PROJECT_MISMATCH"),
        ("failure='database_race'", "DATABASE_MISMATCH"),
        ("failure='folder_race'", "FOLDER_MISMATCH"),
        ("failure='project_race'", "PROJECT_MISMATCH"),
        ("database=nil", "DATABASE_UNAVAILABLE"),
        ("databases=nil", "DATABASE_LIST_UNAVAILABLE"),
        ("databases={unknown=database}", "DATABASE_LIST_UNAVAILABLE"),
        ("names={}", "PROJECT_NAME_AMBIGUOUS"),
        ("names={names[1],names[1]}", "PROJECT_NAME_AMBIGUOUS"),
        ("names=nil", "PROJECT_LIST_UNAVAILABLE"),
        ("failure='names_race'", "PROJECT_NAME_AMBIGUOUS"),
        (
            "databases={};for i=1,129 do databases[i]=database end",
            "DATABASE_LIST_UNAVAILABLE",
        ),
    ],
    ids=[
        "wrong_library",
        "duplicate_library",
        "missing_library",
        "nested_folder",
        "wrong_name",
        "wrong_id",
        "missing_project",
        "database_race",
        "folder_race",
        "project_race",
        "nil_database",
        "nil_list",
        "unknown_key",
        "project_missing_from_root",
        "duplicate_project_name",
        "nil_project_list",
        "project_membership_race",
        "large_list",
    ],
)
def test_wrong_or_racing_native_context_never_verifies(change: str, code: str) -> None:
    native = Native()
    native.lua.execute(change)
    with pytest.raises(LuaError, match="PROJECT_CONTEXT_" + code):
        native.dispatch()
    assert native.exports == []


def test_only_observed_typed_native_flags_are_ignored() -> None:
    native = Native()
    native.lua.execute(
        "database.__flags=4194304; databases.__flags=4194304; names.__flags=4194304"
    )
    assert native.dispatch() == "context_verified"
    native.lua.execute("databases.__flags='4194304'")
    with pytest.raises(LuaError, match="DATABASE_LIST_UNAVAILABLE"):
        native.dispatch()


@pytest.mark.parametrize(
    "changes,code",
    [
        ({"confirm": True}, "INVALID_ARGUMENTS"),
        ({"project_id": ""}, "INVALID_ARGUMENTS"),
        ({"expires": 99}, "EXPIRED"),
    ],
)
def test_native_guard_rejects_mutation_or_expired_envelopes(
    changes: dict[str, Any],
    code: str,
) -> None:
    with pytest.raises(LuaError, match="PROJECT_CONTEXT_" + code):
        Native().dispatch(**changes)


@pytest.mark.parametrize("bad_database", [False, True])
def test_compiler_free_marker_bundle_correlates_context_success_or_error(
    tmp_path: Path,
    bad_database: bool,
) -> None:
    native = Native()
    native.bundle(tmp_path / "runtime", bad_database=bad_database)
    suffix = (
        ".error_PROJECT_CONTEXT_DATABASE_MISMATCH.drp"
        if bad_database
        else ".ok_context_verified.drp"
    )
    assert native.exports.count(native.root + "/" + COMMAND + suffix) == 1
    assert not any(".before.drp" in path for path in native.exports)
    assert native.lua.globals().dofile is None


@pytest.mark.parametrize(
    "changes",
    [
        {"expected_folder": "Nested"},
        {"expected_folder": None},
        {"expected_project_name": ""},
        {"expected_project_name": "x" * 129},
        {"expected_database": {"DbType": "Cloud", "DbName": "Library"}},
        {"expected_database": {"DbType": [], "DbName": "Library"}},
        {
            "expected_database": {
                "DbType": "Disk",
                "DbName": "Library",
                "IpAddress": "x",
            }
        },
        {"expected_database": {"DbType": "Disk", "DbName": "Library", "extra": True}},
        {"extra": "arbitrary"},
    ],
    ids=[
        "folder",
        "nil_folder",
        "empty_name",
        "long_name",
        "db_type",
        "db_type_shape",
        "disk_address",
        "extra_db_field",
        "extra_argument",
    ],
)
def test_python_validator_rejects_invalid_exact_context(
    changes: dict[str, Any],
) -> None:
    with pytest.raises(ValueError):
        validate_project_context_request(ARGUMENTS | changes, "project-id")


def test_validator_accepts_unicode_and_returns_an_independent_database_copy() -> None:
    normalized = validate_project_context_request(ARGUMENTS, "project-id")
    assert normalized == ARGUMENTS
    assert normalized["expected_database"] is not ARGUMENTS["expected_database"]
    with pytest.raises(ValueError, match="identity bound"):
        validate_project_context_request(ARGUMENTS, "😀" * 33)
