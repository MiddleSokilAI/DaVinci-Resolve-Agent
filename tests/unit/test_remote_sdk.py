"""Opaque SDK references and cached readback invalidation, without native UI."""

from __future__ import annotations

from typing import Any

import pytest

from agent.client import BridgeProtocolError
from providers.resolve.remote_sdk import RemoteSDK
from providers.resolve.sdk_api import SDK_METHODS, SDK_WRITE_METHODS


class Channel:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.enabled = False

    def exchange(self, call: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(call)
        if call["kind"] == "root":
            return {
                "result": {"r": "root"},
                "objects": {
                    "root": {
                        "methods": ["GetClipEnabled", "SetClipEnabled", "ImportMedia"],
                        "cache": [
                            {
                                "method": "GetClipEnabled",
                                "arguments": {"a": []},
                                "result": self.enabled,
                            }
                        ],
                    }
                },
            }
        if call["method"] == "SetClipEnabled":
            self.enabled = call["arguments"]["a"][0]
            return {"result": True}
        if call["method"] == "GetClipEnabled":
            return {"result": self.enabled}
        return {"result": {"a": []}}


def test_readback_after_mutation_never_reuses_the_pre_edit_cache() -> None:
    channel = Channel()
    item = RemoteSDK(channel).root()
    assert item.GetClipEnabled() is False
    assert len(channel.calls) == 1
    assert item.SetClipEnabled(True) is True
    assert item.GetClipEnabled() is True
    assert [call.get("method") for call in channel.calls] == [
        None,
        "SetClipEnabled",
        "GetClipEnabled",
    ]


def test_user_dictionary_cannot_impersonate_a_native_reference() -> None:
    channel = Channel()
    item = RemoteSDK(channel).root()
    assert item.ImportMedia({"r": "root"}) == []
    assert channel.calls[-1]["arguments"] == {"a": [{"m": [["r", "root"]]}]}


def test_references_cannot_cross_operation_boundaries() -> None:
    first = RemoteSDK(Channel()).root()
    second = RemoteSDK(Channel()).root()
    with pytest.raises(ValueError, match="another operation"):
        first.ImportMedia([second])


def test_source_execution_and_unallowlisted_methods_are_absent() -> None:
    item = RemoteSDK(Channel()).root()
    for name in ("Execute", "RunScript", "DeleteProject", "system"):
        assert name not in SDK_METHODS
        with pytest.raises(AttributeError):
            getattr(item, name)


def test_unknown_native_reference_is_rejected() -> None:
    class Broken:
        def exchange(self, _: dict[str, Any]) -> dict[str, Any]:
            return {"result": {"r": "unregistered"}}

    with pytest.raises(BridgeProtocolError, match="Unknown native SDK reference"):
        RemoteSDK(Broken()).root()


def test_native_write_action_policy_matches_canonical_bridge() -> None:
    from bridges.resolve.ResolveBridge import WRITE_ACTIONS

    assert set(SDK_WRITE_METHODS) == WRITE_ACTIONS
