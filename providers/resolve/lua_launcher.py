"""Opt-in Windows launcher for the experimental Fusion scriptlib startup path.

Preparing files is not proof that a particular Resolve edition executes the hook.
No GUI input, service, scheduled task or network listener is used.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
from pathlib import Path
from uuid import uuid4

from providers.resolve.lua_compat import with_loader
from providers.resolve.lua_editing import atomic_json
from providers.resolve.lua_transport import lua_string, prepare

HOOK_NAME = "ResolveAgentStartup.scriptlib"
HOOK_HEADER = "-- Managed by DaVinci-Resolve-Agent lua_launcher v1\n"


def validate_previous(root: Path) -> None:
    """Refuse rotation around uncertain receipts; retain all old artifacts."""
    if not (root / "session.json").is_file():
        raise ValueError("Previous runtime has no session metadata.")
    for path in (root / "receipts").glob("*.json"):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt.get("status") not in {"completed", "rejected"}:
            raise ValueError("Reconcile the previous runtime's uncertain writes first.")
    if (root / "client.lock").exists():
        raise ValueError("Previous runtime still has a client lock.")


def stage(
    root: Path,
    media_roots: list[Path],
    project_id: str,
    previous: Path,
    *,
    project_name: str = "",
    ready_timeout_seconds: int = 180,
) -> Path:
    """Prepare one fresh, project-bound runtime and a non-installed startup hook."""
    if not project_id or len(project_id) > 128:
        raise ValueError("An expected project ID is required.")
    if (
        not isinstance(project_name, str)
        or len(project_name) > 512
        or "\0" in project_name
    ):
        raise ValueError("Invalid project name.")
    if type(ready_timeout_seconds) is not int or not 1 <= ready_timeout_seconds <= 600:
        raise ValueError("Startup readiness timeout must be 1..600 seconds.")
    validate_previous(previous)
    prepare(root, media_roots)
    root = root.resolve()
    metadata = json.loads((root / "session.json").read_text(encoding="utf-8"))
    template = (
        Path(__file__).resolve().parents[2] / "bridges/resolve/ResolveLuaStartup.lua"
    )
    worker = template.read_text(encoding="utf-8")
    for key, value in {
        "__RUNTIME_ROOT__": root.as_posix(),
        "__SESSION__": metadata["session"],
        "__EXPECTED_PROJECT__": project_id,
        "__PROJECT_NAME__": project_name,
    }.items():
        worker = worker.replace(f'"{key}"', lua_string(value))
    worker = worker.replace("__READY_TIMEOUT__", str(ready_timeout_seconds))
    (root / "startup.lua").write_text(with_loader(worker), encoding="utf-8")
    command = with_loader(
        "_G.ResolveAgentStartupContext=" + lua_string(metadata["session"]) + "; "
        "dofile(" + lua_string((root / "startup.lua").as_posix()) + ")"
    )
    hook = HOOK_HEADER + with_loader(
        "local ok, err = pcall(function()\n"
        "  if _G.ResolveAgentStartupQueued then return end\n"
        "  local app = assert(fusion or fu, 'Fusion context unavailable')\n"
        "  _G.ResolveAgentStartupQueued = true\n"
        f"  app:Execute({lua_string(command)})\n"
        "end)\n"
        "if not ok then\n"
        "  print('Resolve Agent startup dispatch failed: '..tostring(err))\nend\n"
    )
    path = root / HOOK_NAME
    path.write_text(hook, encoding="utf-8")
    atomic_json(
        root / "startup.json",
        {
            "previous_runtime": str(previous.resolve()),
            "expected_project_id": project_id,
            "project_name": project_name,
            "ready_timeout_seconds": ready_timeout_seconds,
            "validation": "staged_not_live_verified",
        },
    )
    return path


def install_hook(source: Path, scripts: Path) -> Path:
    """Install only our named hook, refusing any conflicting unmanaged file."""
    scripts.mkdir(parents=True, exist_ok=True)
    target = scripts / HOOK_NAME
    if target.exists() and not target.read_text(encoding="utf-8").startswith(
        HOOK_HEADER
    ):
        raise ValueError("Refusing to replace an unmanaged startup hook.")
    pending = scripts / (HOOK_NAME + ".pending")
    with pending.open("x", encoding="utf-8") as output:
        output.write(source.read_text(encoding="utf-8"))
    pending.replace(target)
    return target


def resolve_running() -> bool:
    """Query processes without focusing or interacting with any application."""
    if os.name != "nt":
        raise OSError("The experimental launcher currently supports Windows only.")
    result = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq Resolve.exe", "/FO", "CSV", "/NH"],
        capture_output=True,
        text=True,
        check=True,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    return any(
        row and row[0].lower() == "resolve.exe"
        for row in csv.reader(result.stdout.splitlines())
    )


def launch(
    base: Path,
    previous: Path,
    media_roots: list[Path],
    project_id: str,
    executable: Path,
    scripts: Path,
    *,
    project_name: str = "",
    ready_timeout_seconds: int = 180,
) -> Path:
    """Rotate sessions only with Resolve closed, install the hook, then launch.

    The exclusive launcher lock serializes our launches. A crash leaves it for
    explicit reconciliation instead of guessing whether a predecessor is alive.
    """
    base.mkdir(parents=True, exist_ok=True)
    lock = base / "launcher.lock"
    with lock.open("x", encoding="ascii") as handle:
        handle.write(str(os.getpid()))
    try:
        if resolve_running():
            raise ValueError(
                "Resolve is already running; nothing was installed or launched."
            )
        if not executable.is_file() or executable.name.lower() != "resolve.exe":
            raise ValueError("Select the installed Resolve.exe.")
        active = base / "active.json"
        if active.exists():
            previous = Path(json.loads(active.read_text(encoding="utf-8"))["runtime"])
        runtime = base / ("session-" + uuid4().hex)
        hook = stage(
            runtime,
            media_roots,
            project_id,
            previous,
            project_name=project_name,
            ready_timeout_seconds=ready_timeout_seconds,
        )
        install_hook(hook, scripts)
        atomic_json(active, {"runtime": str(runtime.resolve()), "status": "prepared"})
        # No shell or keyboard automation; the app itself loads the scriptlib.
        subprocess.Popen([str(executable)], cwd=str(executable.parent))
        return runtime
    finally:
        lock.unlink()


def main() -> None:
    """Stage by default; --launch explicitly installs the hook and opens Resolve."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--previous-runtime", type=Path, required=True)
    parser.add_argument("--media-root", type=Path, action="append", default=[])
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--project-name", default="")
    parser.add_argument("--ready-timeout-seconds", type=int, default=180)
    parser.add_argument("--launch", action="store_true")
    parser.add_argument(
        "--executable",
        type=Path,
        default=Path("C:/Program Files/Blackmagic Design/DaVinci Resolve/Resolve.exe"),
    )
    args = parser.parse_args()
    if args.launch:
        scripts = Path(os.environ["APPDATA"]) / (
            "Blackmagic Design/DaVinci Resolve/Support/Fusion/Scripts"
        )
        runtime = launch(
            args.base,
            args.previous_runtime,
            args.media_root,
            args.project_id,
            args.executable,
            scripts,
            project_name=args.project_name,
            ready_timeout_seconds=args.ready_timeout_seconds,
        )
        print(f"Launched; verify MCP ping before use. Runtime: {runtime}")
    else:
        path = stage(
            args.base / ("staged-" + uuid4().hex),
            args.media_root,
            args.project_id,
            args.previous_runtime,
            project_name=args.project_name,
            ready_timeout_seconds=args.ready_timeout_seconds,
        )
        print(f"Staged only; hook NOT installed: {path}")


if __name__ == "__main__":
    main()
