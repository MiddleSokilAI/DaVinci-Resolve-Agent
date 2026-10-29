"""Real Lua 5.1 marker parsing, lease and bundled native-dispatch checks."""

from __future__ import annotations

import json
import struct
import zlib
from importlib import import_module
from pathlib import Path
from typing import Any

import pytest

from providers.resolve.lua_marker_bundle import build_marker_bridge
from providers.resolve.lua_transport import prepare

LuaRuntime = import_module("lupa.lua51").LuaRuntime
LuaError = import_module("lupa.lua51").LuaError
SESSION = "1" * 32
COMMAND = "2" * 32
READER = (
    Path(__file__).resolve().parents[2]
    / "bridges"
    / "resolve"
    / "ResolveLuaMarkerReader.lua"
)


def encode(payload: bytes, *, magic: int = 77, version: int = 1) -> bytes:
    return (
        struct.pack("<BBHI", magic, version, len(payload), zlib.adler32(payload))
        + payload
    )


def markers(base: str, packet: bytes) -> set[str]:
    result = {base + "/ready.marker"}
    for index, byte in enumerate(packet):
        for bit in range(8):
            if byte & 1 << bit:
                result.add(base + f"/b{index:05d}_{bit}.bit")
    return result


def envelope(**extra: Any) -> dict[str, Any]:
    return dict(session=SESSION, id=COMMAND, action="ping", expires=130, **extra)


class Harness:
    def __init__(self, root: str = "C:/Marker", lease: int = 5) -> None:
        self.files: set[str] = set()
        self.now = 100.0
        self.root = root
        self.exports: list[str] = []
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().py_exists = lambda path: path in self.files
        self.lua.globals().py_now = lambda: int(self.now)
        self.lua.globals().py_wait = self.wait
        self.lua.globals().py_export = self.export
        self.lua.execute("""
            bmd={fileexists=function(path) return py_exists(path) end,
                 wait=function(seconds) py_wait(seconds) end}
            os.time=function() return py_now() end
            dofile=nil; loadfile=nil; loadstring=nil
        """)
        factory = self.lua.execute(READER.read_text(encoding="utf-8-sig"))
        self.reader = factory(root, SESSION, 4096, lease)

    def wait(self, seconds: float) -> None:
        self.now += seconds
        if self.now > 120:
            raise AssertionError("Bridge exceeded finite test lease")

    def export(self, path: str) -> bool:
        self.files.add(path)
        self.exports.append(path)
        return True

    def publish(self, value: dict[str, Any] | bytes) -> None:
        payload = (
            value
            if isinstance(value, bytes)
            else json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        self.files = markers(self.root + "/inbox", encode(payload))

    def lease(self, expires: int, *, corrupt: bool = False) -> None:
        payload = struct.pack("<I", expires)
        checksum = zlib.adler32(payload) ^ int(corrupt)
        packet = payload + struct.pack("<I", checksum)
        self.files.difference_update({p for p in self.files if "/lease/" in p})
        self.files.update(markers(self.root + "/lease", packet))


def test_request_unicode_and_nulls_are_data_not_code() -> None:
    h = Harness()
    request = envelope(
        arguments={
            "name": 'Український "голос" 😀 ]=]',
            "path": "C:/it's/a\\b.wav",
            "optional": None,
            "literal": "return resolve:Quit()",
        }
    )
    h.publish(request)
    result = h.reader.read_request()
    assert result["arguments"]["name"] == request["arguments"]["name"]
    assert result["arguments"]["path"] == request["arguments"]["path"]
    assert result["arguments"]["optional"] is None
    assert result["arguments"]["literal"] == "return resolve:Quit()"
    assert h.exports == []


@pytest.mark.parametrize(
    "changes, error",
    [
        ({"session": "3" * 32}, "MARKER_SESSION"),
        ({"id": "../not-an-id"}, "MARKER_ID"),
        ({"expires": 99}, "MARKER_EXPIRED"),
        ({"expires": 3701}, "MARKER_EXPIRED"),
        ({"expires": 101.5}, "MARKER_EXPIRED"),
        ({"action": "reload_modules"}, "MARKER_ACTION"),
        ({"action": "eval"}, "MARKER_ACTION"),
        ({"unknown": True}, "MARKER_FIELD"),
        ({"action": "create_timeline", "arguments": {"name": "X"}}, "MARKER_CONFIRM"),
    ],
)
def test_envelope_is_strict(changes: dict[str, Any], error: str) -> None:
    h = Harness()
    request = envelope()
    request.update(changes)
    h.publish(request)
    assert h.reader.read_request() is None
    assert h.reader.get_last_error() == error


@pytest.mark.parametrize(
    "payload",
    [
        b'{"session":"'
        + SESSION.encode()
        + b'","session":"'
        + SESSION.encode()
        + b'"}',
        b'{"unknown":null,"unknown":true}',
        b'{"number":01}',
        b'{"number":1e999}',
        b'{"number":NaN}',
        b'{"text":"\\uD800"}',
        b'{"text":"\\uDC00"}',
        b'{"text":"\xc0\xaf"}',
        b'{"text":"\xed\xa0\x80"}',
        b'{"text":"\xf4\x90\x80\x80"}',
        b'{"text":"\xff"}',
        b'{"items":[null]}',
        b'{"items":[1,]}',
        b'{"x":true,}',
        b'{"x":true} return resolve:Quit()',
        b"[" * 34 + b"0" + b"]" * 34,
    ],
)
def test_malformed_json_does_not_reach_dispatch(payload: bytes) -> None:
    h = Harness()
    h.publish(payload)
    assert h.reader.read_request() is None
    assert h.reader.get_last_error().startswith("MARKER_JSON_")
    assert h.exports == []


def test_json_surrogate_pair_and_false() -> None:
    h = Harness()
    value = h.reader.decode_json(b'{"text":"\\uD83D\\uDE00","disabled":false}')
    assert value["text"] == "😀"
    assert value["disabled"] is False


def test_partial_corrupt_and_changed_packets_are_ignored() -> None:
    h = Harness()
    h.publish(envelope())
    h.files.remove(h.root + "/inbox/ready.marker")
    assert h.reader.read_request() is None
    h.publish(envelope())
    one = next(p for p in h.files if "/b00008_" in p)
    h.files.remove(one)
    assert h.reader.read_request() is None
    assert h.reader.get_last_error() == "MARKER_CHECKSUM"


def test_oversized_header_rejected_before_payload_reads() -> None:
    h = Harness()
    h.files = markers(h.root + "/inbox", struct.pack("<BBHI", 77, 1, 4097, 1))
    assert h.reader.read_request() is None
    assert h.reader.get_last_error() == "MARKER_LENGTH"


def test_replayed_request_cannot_keep_lease_alive() -> None:
    h = Harness()
    h.publish(envelope())
    assert h.reader.read_request() is not None
    deadline = h.reader.get_deadline()
    h.now = 104
    assert h.reader.read_request() is not None
    assert h.reader.get_deadline() == deadline
    h.now = 106
    assert h.reader.is_alive() is False


def test_compact_host_lease_is_bounded_and_checksummed() -> None:
    h = Harness()
    h.now = 102
    h.lease(108)
    assert h.reader.is_alive() is True
    assert h.reader.get_deadline() == 108
    h.now = 103
    h.lease(4000)
    h.reader.is_alive()
    assert h.reader.get_deadline() == 108
    h.now = 104
    h.lease(110, corrupt=True)
    h.reader.is_alive()
    assert h.reader.get_deadline() == 108
    h.now = 109
    assert h.reader.is_alive() is False


def prepare_runtime(root: Path) -> str:
    prepare(root)
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    metadata.update(
        session=SESSION,
        transport="marker",
        marker_protocol=1,
        max_payload_bytes=4096,
        lease_seconds=5,
    )
    (root / "session.json").write_text(json.dumps(metadata), encoding="utf-8")
    bridge = root / "bridge.lua"
    # Keep generated session/source identity in sync for the test's stable nonce.
    from providers.resolve.lua_transport import lua_string

    source = bridge.read_text(encoding="utf-8")
    import re

    source = re.sub(
        r"local session = .*",
        lambda match: "local session = " + lua_string(SESSION),
        source,
        count=1,
    )
    bridge.write_text(source, encoding="utf-8")
    return build_marker_bridge(root)


def native_project(h: Harness) -> None:
    h.lua.execute("""
        local project={GetName=function() return 'Voice' end,
                       GetUniqueId=function() return 'project-id' end}
        local pm={GetCurrentProject=function() return project end,
                  ExportProject=function(self,name,path,stills)
                      return py_export(path)
                  end}
        resolve={GetProjectManager=function() return pm end}
    """)


def test_bundle_without_compiler_or_loaders_dispatches_once_and_exits(
    tmp_path: Path,
) -> None:
    root = tmp_path / "runtime"
    script = prepare_runtime(root)
    h = Harness(root.as_posix())
    native_project(h)
    h.publish(envelope())
    h.lua.execute(script)
    assert h.exports.count(root.as_posix() + "/" + COMMAND + ".drp") == 1
    assert h.exports[-1].endswith("marker-exit-lease_expired.drp")
    assert h.lua.globals().ResolveAgentLuaRunning is None
    assert h.lua.globals().ResolveAgentMarkerSession is None
    assert h.lua.globals().dofile is None
    with pytest.raises(LuaError, match="MARKER_SESSION_ALREADY_CONSUMED"):
        h.lua.execute(script)


def test_stop_acknowledged_through_native_export(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    script = prepare_runtime(root)
    h = Harness(root.as_posix())
    native_project(h)
    request = envelope()
    request["action"] = "stop"
    h.publish(request)
    h.lua.execute(script)
    assert h.exports[-1].endswith("marker-exit-acknowledged_stop.drp")
    assert h.now == 100


def test_metadata_or_changed_bridge_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "runtime"
    prepare_runtime(root)
    bridge = root / "bridge.lua"
    bridge.write_text(bridge.read_text().replace("while true do", "while false do"))
    with pytest.raises(ValueError, match="template anchor"):
        build_marker_bridge(root)


@pytest.mark.parametrize("locale_name", ["C", "English_United States.1252"])
@pytest.mark.parametrize(
    "name", ["Нова доріжка тесту", "Bad\x00", "Bad\x1f", "Bad\x7f"]
)
def test_native_edit_text_is_utf8_safe_and_rejects_ascii_controls(
    tmp_path: Path,
    locale_name: str,
    name: str,
) -> None:
    root = tmp_path / "runtime"
    script = prepare_runtime(root)
    h = Harness(root.as_posix())
    h.lua.execute("""
        local timeline=nil
        local pool={CreateEmptyTimeline=function(_,name)
            created_name=name
            timeline={GetName=function() return name end,
                      GetUniqueId=function() return 'timeline-id' end}
            return timeline
        end}
        local project={GetName=function() return 'Voice' end,
            GetUniqueId=function() return 'project-id' end,
            GetMediaPool=function() return pool end,
            GetTimelineCount=function() return timeline and 1 or 0 end,
            GetTimelineByIndex=function() return timeline end,
            IsRenderingInProgress=function() return false end}
        local pm={GetCurrentProject=function() return project end,
            SaveProject=function() return true end,
            ExportProject=function(_,_,path) return py_export(path) end}
        resolve={GetProjectManager=function() return pm end}
    """)
    setlocale = h.lua.eval("os.setlocale")
    previous = setlocale(None, "ctype")
    if not setlocale(locale_name, "ctype"):
        pytest.skip(f"Native C locale {locale_name} is unavailable")
    try:
        request = envelope(
            confirm=True, project_id="project-id", arguments={"name": name}
        )
        request["action"] = "create_timeline"
        h.publish(request)
        h.lua.execute(script)
    finally:
        setlocale(previous, "ctype")
    if name.startswith("Bad"):
        assert h.lua.globals().created_name is None
        assert (
            root.as_posix() + "/" + COMMAND + ".error_INVALID_ARGUMENTS.drp"
            in h.exports
        )
        assert not any(path.endswith(".before.drp") for path in h.exports)
    else:
        assert h.lua.globals().created_name == name
        assert root.as_posix() + "/" + COMMAND + ".before.drp" in h.exports
        assert root.as_posix() + "/" + COMMAND + ".ok.drp" in h.exports
