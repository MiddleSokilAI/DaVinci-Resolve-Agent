"""Typed SDK references over a data-only native channel.

The canonical Python bridge keeps its validation, backups and readbacks. Only
SDK object calls cross the transport; incoming data is never evaluated as code.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from typing import Any, Protocol

from agent.client import BridgeProtocolError
from providers.resolve.sdk_api import (
    SDK_CONSTANTS,
    SDK_INVALIDATES,
    SDK_METHODS,
    SDK_MUTATIONS,
)


class SDKChannel(Protocol):
    def exchange(self, payload: dict[str, Any]) -> dict[str, Any]: ...


class NativeSDKError(RuntimeError):
    """The native SDK rejected one fixed adapter operation."""


class RemoteSDK:
    def __init__(self, channel: SDKChannel) -> None:
        self.channel = channel
        self.objects: dict[str, SDKObject] = {}
        self.surfaces: dict[str, frozenset[str]] = {}
        self.mutations_sent = 0

    def root(self) -> SDKObject:
        result = self._exchange({"kind": "root"})
        if not isinstance(result, SDKObject):
            raise BridgeProtocolError("Native SDK root is not an object.")
        return result

    def _encode(self, value: Any) -> Any:
        if value is None or type(value) in {str, bool, int}:
            return value
        if type(value) is float and math.isfinite(value):
            return value
        if isinstance(value, SDKObject):
            if value._sdk is not self:
                raise ValueError("SDK reference belongs to another operation.")
            return {"r": value.identity}
        if isinstance(value, (list, tuple)):
            return {"a": [self._encode(item) for item in value]}
        if isinstance(value, dict):
            return {"m": [[self._encode(k), self._encode(v)] for k, v in value.items()]}
        raise TypeError("Unsupported SDK argument type.")

    def _decode(self, value: Any) -> Any:
        if value is None or type(value) in {str, bool, int, float}:
            return value
        if not isinstance(value, dict) or len(value) != 1:
            raise BridgeProtocolError("Invalid native SDK value.")
        if "r" in value and isinstance(value["r"], str):
            try:
                return self.objects[value["r"]]
            except KeyError as error:
                raise BridgeProtocolError("Unknown native SDK reference.") from error
        if "a" in value and isinstance(value["a"], list):
            return [self._decode(item) for item in value["a"]]
        if "m" in value and isinstance(value["m"], list):
            result: dict[Any, Any] = {}
            for pair in value["m"]:
                if not isinstance(pair, list) or len(pair) != 2:
                    raise BridgeProtocolError("Invalid native SDK map.")
                key = self._decode(pair[0])
                if type(key) not in {str, int, float, bool} or key in result:
                    raise BridgeProtocolError("Invalid or duplicate native SDK key.")
                result[key] = self._decode(pair[1])
            return result
        raise BridgeProtocolError("Unknown native SDK value tag.")

    @staticmethod
    def _key(method: str, encoded_arguments: Any) -> str:
        return method + ":" + json.dumps(encoded_arguments, sort_keys=True)

    def _exchange(self, call: dict[str, Any]) -> Any:
        response = self.channel.exchange(call)
        if response.get("error"):
            raise NativeSDKError(str(response["error"]))
        definitions = response.get("objects", {})
        if not isinstance(definitions, dict):
            raise BridgeProtocolError("Invalid native SDK object catalogue.")
        for identity, definition in definitions.items():
            if (
                not isinstance(identity, str)
                or not 1 <= len(identity) <= 128
                or not isinstance(definition, dict)
                or not isinstance(definition.get("methods"), list)
                or not all(method in SDK_METHODS for method in definition["methods"])
            ):
                raise BridgeProtocolError("Invalid native SDK object definition.")
            obj = self.objects.setdefault(identity, SDKObject(self, identity))
            obj._methods = frozenset(definition["methods"])
            kind = definition.get("kind")
            if isinstance(kind, str):
                self.surfaces[kind] = obj._methods
            properties = definition.get("properties", {})
            if not isinstance(properties, dict) or set(properties) - SDK_CONSTANTS:
                raise BridgeProtocolError("Invalid native SDK object properties.")
            obj._properties = properties
        # Register every reference before resolving cached graphs and cycles.
        for identity, definition in definitions.items():
            obj = self.objects[identity]
            for item in definition.get("cache", []):
                if (
                    not isinstance(item, dict)
                    or item.get("method") not in obj._methods
                    or "arguments" not in item
                    or "result" not in item
                ):
                    raise BridgeProtocolError("Invalid native SDK cache entry.")
                obj._cache[self._key(item["method"], item["arguments"])] = self._decode(
                    item["result"]
                )
        return self._decode(response.get("result"))

    def call(self, identity: str, method: str, arguments: tuple[Any, ...]) -> Any:
        obj = self.objects[identity]
        encoded = self._encode(arguments)
        key = self._key(method, encoded)
        if method not in SDK_INVALIDATES and key in obj._cache:
            return obj._cache[key]
        if method not in SDK_INVALIDATES and len(arguments) == 1:
            # SDK getter overloads expose a snapshot or one key from it.
            snapshot = obj._cache.get(self._key(method, {"a": []}))
            if (
                method in {"GetProperty", "GetClipProperty", "GetSetting"}
                and isinstance(snapshot, dict)
                and arguments[0] in snapshot
            ):
                return snapshot[arguments[0]]
        if method in SDK_INVALIDATES:
            for reference in self.objects.values():
                reference._cache.clear()
        if method in SDK_MUTATIONS:
            self.mutations_sent += 1
        return self._exchange(
            {
                "kind": "call",
                "object": identity,
                "method": method,
                "arguments": encoded,
            }
        )


class SDKObject:
    """An opaque, operation-scoped reference to one live SDK object."""

    def __init__(self, sdk: RemoteSDK, identity: str) -> None:
        self._sdk, self.identity = sdk, identity
        self._methods: frozenset[str] = frozenset()
        self._properties: dict[str, Any] = {}
        self._cache: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Callable[..., Any] | Any:
        if name in self._methods:
            return lambda *args: self._sdk.call(self.identity, name, args)
        if name in self._properties:
            return self._properties[name]
        raise AttributeError(name)
