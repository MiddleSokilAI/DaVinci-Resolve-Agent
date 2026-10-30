"""Fixed external SDK worker, using only Resolve's bundled Python and stdlib."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from bridges.resolve import ResolveBridge as bridge
from bridges.resolve.SDKCapabilities import augment_sdk_state
from bridges.resolve.SDKTracker import SDKTracker


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--probe", action="store_true")
    parser.add_argument("--runtime", type=Path)
    parser.add_argument("--command")
    args = parser.parse_args()
    import DaVinciResolveScript  # type: ignore[import-not-found]

    resolve = DaVinciResolveScript.scriptapp("Resolve")
    if args.probe:
        print(json.dumps({"available": resolve is not None}))
        return 0
    if resolve is None:
        return 3
    if (
        args.runtime is None
        or args.command is None
        or not re.fullmatch(
            r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}",
            args.command,
        )
    ):
        parser.error("An owned runtime and one canonical command UUID are required.")
    root = args.runtime.resolve()
    directories = bridge.ensure_runtime_directories(root)
    source = directories["commands"] / (args.command + ".json")
    processing = directories["processing"] / source.name
    source.rename(processing)
    tracker = SDKTracker()
    resolve = tracker.wrap(resolve)
    state = bridge.collect_bridge_state(resolve)
    bridge._apply_verified_capabilities(state, directories["state"])
    augment_sdk_state(
        state,
        resolve,
        directories["state"],
        discover=True,
        transport="python_sdk",
    )
    bridge.process_command_file(
        processing,
        directories["responses"] / source.name,
        directories["failed"] / source.name,
        state,
        resolve,
        directories,
    )
    augment_sdk_state(state, resolve, directories["state"], transport="python_sdk")
    bridge.atomic_write_json(
        directories["state"] / (args.command + ".sdk.json"),
        {
            "command_id": args.command,
            "mutations_sent": tracker.mutations_sent,
        },
    )
    bridge.publish_bridge_state(
        directories["state"] / "bridge.json",
        state,
        directories["logs"] / "bridge.jsonl",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
