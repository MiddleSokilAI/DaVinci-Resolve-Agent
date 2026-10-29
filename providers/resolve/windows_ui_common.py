"""Shared native identity and mailbox ownership for host-side Resolve UI work."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from uuid import uuid4

from providers.resolve.lua_editing import bounded_text
from providers.resolve.lua_transport import LuaSnapshotClient


def native_project(client: LuaSnapshotClient, expected: str) -> dict[str, str]:
    """Require fresh native identity rather than trusting a window caption."""
    bounded_text(expected, "Expected project ID")
    project: dict[str, str] = client.request("get_current_project")["project"]
    if project["id"] != expected:
        raise ValueError("Active native project identity changed.")
    return project


@contextmanager
def hold_ui_mailbox(root: Path) -> Iterator[None]:
    """Exclude native requests during GUI work and remove only our own lock."""
    lock = root / "client.lock"
    owner = f"{os.getpid()}:windows-ui:{uuid4().hex}"
    with lock.open("x", encoding="ascii") as stream:
        stream.write(owner)
    try:
        yield
    finally:
        with suppress(FileNotFoundError):
            if lock.read_text(encoding="ascii") == owner:
                lock.unlink()
