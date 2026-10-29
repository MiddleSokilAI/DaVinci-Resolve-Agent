"""Manage the opt-in per-user Resolve Free marker watcher, without recording."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from providers.resolve.managed_status import managed_status
from providers.resolve.marker_supervisor import Profile, Supervisor


def startup_entry(profile: Profile, operation: str) -> dict[str, Any]:
    """Install/inspect/remove only the owned per-user Windows Startup shortcut."""
    if sys.platform != "win32":
        raise ValueError("Windows Startup registration requires Windows.")
    if operation not in {"install", "remove", "status"}:
        raise ValueError("Unknown Startup operation.")
    from providers import resolve

    driver = Path(resolve.__file__).with_name("ResolveAutostartEntry.ps1")
    executable = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32/WindowsPowerShell/v1.0/powershell.exe"
    )
    payload = {
        "operation": operation,
        "profile_path": str(profile.path),
        "pythonw_executable": str(Path(sys.executable).with_name("pythonw.exe")),
    }
    completed = subprocess.run(
        [str(executable), "-NoLogo", "-NoProfile", "-NonInteractive", "-STA",
         "-ExecutionPolicy", "Bypass", "-File", str(driver)],
        input=json.dumps(payload, ensure_ascii=False),
        encoding="utf-8", capture_output=True, timeout=20, check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        result = json.loads(completed.stdout.lstrip("\ufeff"))
    except ValueError as error:
        raise RuntimeError(
            "Windows Startup driver returned no valid result "
            f"(exit {completed.returncode}); inspect the local invocation."
        ) from error
    if completed.returncode or not isinstance(result, dict) or not result.get("ok"):
        raise RuntimeError("Windows Startup registration refused: " + str(result))
    return result


def start_hidden(profile: Profile) -> dict[str, Any]:
    """Start a watcher with no terminal; its own singleton lock arbitrates races."""
    if sys.platform != "win32":
        raise ValueError("Hidden watcher startup requires Windows.")
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    if not pythonw.is_file():
        raise ValueError("Installed Python windowless executable is unavailable.")
    log_path = profile.state_directory / "watcher.log"
    if log_path.is_symlink():
        raise ValueError("Watcher log cannot be a symbolic link.")
    with log_path.open("ab") as output:
        process = subprocess.Popen(
            [str(pythonw), "-m", "agent.resolve_watch", "--profile", str(profile.path)],
            stdin=subprocess.DEVNULL, stdout=output, stderr=output,
            cwd=profile.state_directory,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    return {"watcher_start_requested": True, "pid": process.pid,
            "native_connection_verified": False}


def main() -> None:
    """Configure the owned startup entry or run the bounded-operation watcher."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile", type=Path, required=True)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--once", action="store_true")
    actions.add_argument("--status", action="store_true")
    actions.add_argument("--install-autostart", action="store_true")
    actions.add_argument("--remove-autostart", action="store_true")
    actions.add_argument("--start", action="store_true")
    args = parser.parse_args()
    profile = Profile.load(args.profile)
    if args.install_autostart or args.remove_autostart:
        print(json.dumps(startup_entry(
            profile, "install" if args.install_autostart else "remove"
        ), ensure_ascii=False))
    elif args.status:
        print(json.dumps(managed_status(profile), ensure_ascii=False))
    elif args.start:
        print(json.dumps(start_hidden(profile), ensure_ascii=False))
    elif args.once:
        print(json.dumps(Supervisor(profile).tick(), ensure_ascii=False))
    else:
        Supervisor(profile).run()


if __name__ == "__main__":
    main()
