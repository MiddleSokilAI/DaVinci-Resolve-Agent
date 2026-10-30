"""Exercise real Lua 5.1 SDK guards and post-close replies, without a live editor."""

from __future__ import annotations

import json
from importlib import import_module
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest

from providers.resolve.lua_editing import lua_value
from providers.resolve.remote_sdk import NativeSDKError, RemoteSDK
from providers.resolve.sdk_api import (
    SDK_COMMON_WRITE_METHODS,
    SDK_CONSTANTS,
    SDK_METHODS,
    SDK_MUTATIONS,
    SDK_WRITE_METHODS,
)
from providers.resolve.sdk_command_client import SDKCommandClient

BRIDGES = Path(__file__).resolve().parents[2] / "bridges/resolve"


class LuaChannel:
    def __init__(self, root: Path, operation: dict[str, Any]) -> None:
        self.root, self.operation = root, operation
        self.session = "a" * 32
        self.exports: dict[str, str] = {}
        self.lua = import_module("lupa.lua51").LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().py_export = self.exports.__setitem__
        self.lua.globals().py_exists = lambda path: path in self.exports
        self.lua.globals().py_backup = self.backup
        self.lua.execute("""
            bmd={fileexists=function(path) return py_exists(path) end,
                 wait=function() end}
            local project=setmetatable({}, {})
            function project:GetName() return 'Close fixture' end
            function project:GetUniqueId() return 'project-id' end
            function project:GetTimelineCount() return 0 end
            function project:GetCurrentTimeline() return nil end
            function project:GetMediaPool() return nil end
            function project:GetSetting() return {} end
            function project:IsRenderingInProgress() return false end
            local pm=setmetatable({}, {})
            api={open=true, closed=0, saves=0, presets={}}
            function pm:GetName() return 'Project manager' end
            function pm:GetCurrentProject() return api.open and project or nil end
            function pm:SaveProject() api.saves=api.saves+1; return true end
            function pm:ExportProject(name,path,stills) py_backup(path); return true end
            function pm:CloseProject(target)
                assert(target==project and api.open)
                api.open=false; api.closed=api.closed+1; return true
            end
            function api:GetProjectManager() return pm end
            function api:GetProductName() return 'DaVinci Resolve' end
            function api:GetVersionString() return '21.1' end
            function api:GetVersion() return {21,1,0,0} end
            function api:GetCurrentPage() return 'edit' end
            function api:GetLayoutPresetList() return {} end
            function api:SaveLayoutPreset(name) self.presets[name]=true; return true end
            function api:ExportLayoutPreset(name,path)
                assert(self.presets[name]); py_export(path,name); return true
            end
            function api:DeleteLayoutPreset(name)
                self.presets[name]=nil; return true
            end
        """)
        factory = self.lua.execute((BRIDGES / "ResolveLuaMarkerReader.lua").read_text())
        self.decoder = factory(str(root), self.session, 4096, 600).decode_json
        self.lua.globals().decode = self.decoder
        self.lua.execute("function read_request() return decode(pending) end")
        sdk_factory = self.lua.execute((BRIDGES / "ResolveLuaSDK.lua").read_text())
        policy = dict(
            methods=sorted(SDK_METHODS),
            constants=sorted(SDK_CONSTANTS),
            mutations=sorted(SDK_MUTATIONS),
            write_methods={
                key: list(value) for key, value in SDK_WRITE_METHODS.items()
            },
            common_write_methods=list(SDK_COMMON_WRITE_METHODS),
            backup_roots=[str(root / "core/backups")],
        )
        self.proxy = sdk_factory(
            self.lua.globals().api,
            str(root),
            self.session,
            self.decoder,
            self.lua.execute("return " + lua_value(policy)),
            self.lua.globals().read_request,
        )

    @staticmethod
    def backup(path: str) -> None:
        Path(path).write_text("saved backup")

    def exchange(self, payload: dict[str, Any]) -> dict[str, Any]:
        identity = uuid4().hex
        request = json.dumps({"operation": self.operation, "call": payload})
        assert len(request) <= 2000
        self.lua.globals().pending = json.dumps(
            {
                "action": "ping",
                "id": uuid4().hex,
                "arguments": {
                    "sdk": {"id": identity, "seq": 1, "count": 1, "data": request}
                },
            }
        )
        self.proxy.poll()
        name = self.exports[str(self.root) + "/sdk-" + identity + ".preset"]
        return cast(dict[str, Any], json.loads(bytes.fromhex(name.split("_O_", 1)[1])))


def test_complete_canonical_close_over_actual_lua_adapter(tmp_path: Path) -> None:
    channels: list[LuaChannel] = []

    def factory(operation: dict[str, Any], timeout: float) -> LuaChannel:
        channel = LuaChannel(tmp_path, operation)
        channels.append(channel)
        return channel

    client = SDKCommandClient(tmp_path, "a" * 32, channel_factory=factory)
    kwargs: dict[str, Any] = dict(
        provider="resolve",
        action="close_project",
        arguments={"expected_project_id": "project-id", "confirm_close": True},
        create_backup=True,
        idempotency_key="close-lua",
    )
    result = client.request(**kwargs)
    assert result["closed"] is True
    assert Path(result["backup_path"]).exists()
    assert channels[0].lua.globals().api.closed == 1
    assert channels[0].lua.globals().api.saves == 2
    assert client.request(**kwargs) == result
    assert len(channels) == 1


def test_lua_close_is_rejected_outside_its_canonical_action(tmp_path: Path) -> None:
    operation = dict(
        id=uuid4().hex,
        action="get_capabilities",
        fingerprint="b" * 64,
        expires=4102444800,
        create_backup=True,
        allow_destructive=False,
    )
    channel = LuaChannel(tmp_path, operation)
    sdk = RemoteSDK(channel)
    manager = sdk.root().GetProjectManager()
    project = manager.GetCurrentProject()
    with pytest.raises(NativeSDKError, match="WRITE_NOT_AUTHORIZED"):
        manager.CloseProject(project)
    assert channel.lua.globals().api.closed == 0


def test_lua_close_requires_completed_native_backup(tmp_path: Path) -> None:
    operation = dict(
        id=uuid4().hex,
        action="close_project",
        fingerprint="b" * 64,
        expires=4102444800,
        create_backup=True,
        allow_destructive=False,
    )
    channel = LuaChannel(tmp_path, operation)
    sdk = RemoteSDK(channel)
    manager = sdk.root().GetProjectManager()
    project = manager.GetCurrentProject()
    with pytest.raises(NativeSDKError, match="BACKUP_REQUIRED"):
        manager.CloseProject(project)
    assert channel.lua.globals().api.closed == 0
