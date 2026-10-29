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
from providers.resolve.project_open_service import ProjectOpenError, ProjectOpenService


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
    actions.add_argument("--open-project", metavar="EXACT_NAME")
    parser.add_argument("--expected-project-id")
    parser.add_argument("--database-name")
    parser.add_argument("--idempotency-key")
    parser.add_argument("--confirm", action="store_true")
    parser.add_argument("--project-open-timeout", type=float, default=120)
    args = parser.parse_args()
    target_args = (args.expected_project_id, args.database_name, args.idempotency_key)
    if args.open_project:
        if not all(target_args):
            parser.error("--open-project requires --expected-project-id, "
                         "--database-name and --idempotency-key")
    elif any(target_args) or args.confirm or args.project_open_timeout != 120:
        parser.error("Project target arguments require --open-project")
    profile = Profile.load(args.profile)
    if args.open_project:
        try:
            result = ProjectOpenService(profile).open_existing(
                project_name=args.open_project,
                expected_project_id=args.expected_project_id,
                expected_database={"DbType": "Disk", "DbName": args.database_name},
                folder_path=[], idempotency_key=args.idempotency_key,
                confirm=args.confirm, timeout_seconds=args.project_open_timeout,
            )
        except ProjectOpenError as error:
            print(json.dumps({"error": str(error), "code": error.code},
                             ensure_ascii=False), file=sys.stderr)
            raise SystemExit(1) from error
        except ValueError as error:
            parser.error(str(error))
        print(json.dumps(result, ensure_ascii=False))
    elif args.install_autostart or args.remove_autostart:
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
