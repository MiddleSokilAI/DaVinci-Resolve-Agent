"""Count SDK write dispatches without changing the canonical command handlers."""

from __future__ import annotations

from typing import Any


class SDKTracker:
    def __init__(self) -> None:
        self.mutations_sent = 0

    def wrap(self, value: Any) -> Any:
        if value is None or isinstance(value, (str, bool, int, float)):
            return value
        if isinstance(value, dict):
            return {key: self.wrap(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.wrap(item) for item in value]
        return _Object(self, value)


def _unwrap(value: Any) -> Any:
    if isinstance(value, _Object):
        return value.native
    if isinstance(value, dict):
        return {key: _unwrap(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_unwrap(item) for item in value]
    return value


class _Object:
    _sdk = None  # Capability discovery observes these Python bindings directly.

    def __init__(self, tracker: SDKTracker, native: Any) -> None:
        self.tracker, self.native = tracker, native

    def __getattr__(self, name: str) -> Any:
        method = getattr(self.native, name)
        if not callable(method):
            return method

        def call(*arguments: Any) -> Any:
            if not name.startswith(("Get", "Is", "Export")) and name != "OpenPage":
                self.tracker.mutations_sent += 1
            return self.tracker.wrap(method(*(_unwrap(arg) for arg in arguments)))

        return call
