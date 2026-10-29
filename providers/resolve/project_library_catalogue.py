"""Strict, read-only local-library evidence for a fixed Project Manager adapter.

Only the observed single selected Disk-library, guest/root-project layout and
project-schema version 17 are supported. SQLite opens private byte snapshots,
never a live Resolve database. This evidence cannot authorize an Open action by
itself: the selected candidate still needs an independent native DRP identity.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, NoReturn

from providers.resolve.lua_editing import bounded_text
from providers.resolve.lua_project_context import validate_project_context_request

MAX_REGISTRY_BYTES = 8192
MAX_PROJECTS = 128
MAX_DB_BYTES = 32 * 1024 * 1024
MAX_TOTAL_DB_BYTES = 128 * 1024 * 1024
MAX_ENTRIES = 512
_UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
_SIDECARS = ("-wal", "-shm", "-journal")

# Exact PRAGMA table_info(name,type,notnull,pk) observed on two independently
# saved Resolve 21.1.2 project files. Unknown layouts require separate acceptance.
_PROJECT_COLUMNS = """
SM_Project_id|uuid|1|1
DbType|character varying(128)|1|0
LockId|character varying|0|0
User|uuid|0|0
UserId|INTEGER|0|0
SysId|character varying|0|0
ProjectId|INTEGER|0|0
RevivalTaskSetID|INTEGER|0|0
PlayHeadsSplitDisplay|boolean|0|0
pGallery|uuid|0|0
ProjectName|character varying|0|0
MasterSession|uuid|0|0
CurrentSessionIndex|INTEGER|0|0
StreamCursorVector|character varying|0|0
StreamIdVector|character varying|0|0
StreamPlayHeadVector|character varying|0|0
StreamAdvanceVector|character varying|0|0
GangPlayHeadVector|character varying|0|0
NumTotalStreams|INTEGER|0|0
NumPlayStreams|INTEGER|0|0
CurrentStreamId|INTEGER|0|0
PlayingStreamId|INTEGER|0|0
PlayHeadMode|character varying|0|0
CommonConfig|uuid|0|0
DeletedSsnList|character varying|0|0
IsADeliverableProject|boolean|0|0
DeliverableProjectEDLStr|character varying|0|0
DeliverableSessionName|character varying|0|0
NumReader|INTEGER|0|0
ClipInfoSeed|bigint|0|0
ReelSeed|bigint|0|0
EDLSeed|bigint|0|0
PTZRPresetMgr|uuid|0|0
TextRenderItemVec|uuid|0|0
RenderTextEnabled|boolean|0|0
activeStereoLeftSession|character varying|0|0
activeStereoRightSession|character varying|0|0
IsAutoSave|boolean|0|0
ProjRef|uuid|0|0
UpToDate|boolean|0|0
ReadOnly|boolean|0|0
OrigProjUpToDate|boolean|0|0
pMediaPool|uuid|0|0
Folder|uuid|0|0
CurrentTimelineIndex|INTEGER|0|0
RenderTextGanged|boolean|0|0
RenderTextPrefixed|boolean|0|0
MediaPool|uuid|0|0
ProjectVersion|INTEGER|0|0
IsCollaborationEnabled|boolean|0|0
MediaPoolForTimelines|uuid|0|0
LastModTimeInSecs|bigint|0|0
FieldsBlob|BLOB|0|0
CurrentGroup|uuid|0|0
IsLiveCollaborationEnabled|boolean|0|0
GroupListObj|uuid|0|0
UpgradeLockSysId|character varying|0|0
PowerNodeList|uuid|0|0
ProjectAgeInMs|bigint|0|0
LockableBlobMap|uuid|0|0
Notes|character varying|0|0
CollabGroupId|uuid|0|0
"""
_FOLDER_COLUMNS = """
SM_ProjectFolder_id|uuid|1|1
DbType|character varying(128)|1|0
User|uuid|0|0
ParentFolder|uuid|0|0
Name|character varying|0|0
ModDateTime|character varying|0|0
CreateDateTime|character varying|0|0
IsExpanded|boolean|0|0
FieldsBlob|BLOB|0|0
"""
_MAP_COLUMNS = """
DbOwner|uuid|1|1
DbKey|bigint|1|2
DbVal|uuid|1|3
"""


def _columns(text: str) -> tuple[tuple[str, str, int, int], ...]:
    return tuple(
        (parts[0], parts[1], int(parts[2]), int(parts[3]))
        for line in text.strip().splitlines()
        if (parts := line.split("|"))
    )


SUPPORTED_TABLES = {
    "SM_Project": _columns(_PROJECT_COLUMNS),
    "SM_ProjectFolder": _columns(_FOLDER_COLUMNS),
    "SM_Project_FolderMap": _columns(_MAP_COLUMNS),
}


class ProjectLibraryCatalogueError(RuntimeError):
    """Local evidence is unsupported, ambiguous, unstable or incomplete."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(f"Project library catalogue: {code}. No project was opened.")


def _fail(code: str) -> NoReturn:
    raise ProjectLibraryCatalogueError(code)


def _unlinked(path: Path) -> None:
    if not path.is_absolute():
        _fail("ABSOLUTE_SOURCE_PATH_REQUIRED")
    for item in (path, *path.parents):
        value = item.lstat()
        if (
            stat.S_ISLNK(value.st_mode)
            or getattr(value, "st_file_attributes", 0) & 0x400
        ):
            _fail("LINKED_LIBRARY_SOURCE")


def _stat_key(value: os.stat_result) -> tuple[int, ...]:
    return (
        value.st_dev,
        value.st_ino,
        value.st_size,
        value.st_mtime_ns,
        value.st_ctime_ns,
    )


def _sidecars(path: Path) -> None:
    for suffix in _SIDECARS:
        candidate = path.with_name(path.name + suffix)
        if candidate.exists() or candidate.is_symlink():
            _fail("LIVE_DATABASE_SIDECAR_PRESENT")


def _stable_bytes(
    path: Path, maximum: int, *, database: bool = False
) -> tuple[bytes, dict[str, Any]]:
    _unlinked(path)
    if database:
        _sidecars(path)
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= maximum:
        _fail("SOURCE_FILE_BOUND_EXCEEDED")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        first = stream.read(maximum + 1)
        after_read = os.fstat(stream.fileno())
    if (
        len(first) > maximum
        or len(first) != before.st_size
        # Windows path-stat ctime and handle-stat ctime have different native
        # meanings. Compare each API to itself, and cross-bind file ID/size/mtime.
        or _stat_key(before)[:4] != _stat_key(opened)[:4]
        or _stat_key(opened) != _stat_key(after_read)
    ):
        _fail("SOURCE_CHANGED_DURING_COPY")
    with path.open("rb") as stream:
        second = stream.read(maximum + 1)
    _unlinked(path)
    after = path.stat()
    if database:
        _sidecars(path)
    digest = hashlib.sha256(first).hexdigest()
    if (
        _stat_key(before) != _stat_key(after)
        or hashlib.sha256(second).hexdigest() != digest
    ):
        _fail("SOURCE_CHANGED_DURING_COPY")
    return first, {
        "path": str(path),
        "size": before.st_size,
        "mtime_ns": before.st_mtime_ns,
        "ctime_ns": before.st_ctime_ns,
        "device": before.st_dev,
        "file_id": before.st_ino,
        "sha256": digest,
    }


def _registry_path() -> Path:
    value = os.environ.get("APPDATA")
    if os.name != "nt" or not value:
        _fail("WINDOWS_REGISTRY_PATH_REQUIRED")
    return Path(value) / "Blackmagic Design/DaVinci Resolve/Preferences/dblist.conf"


def _library(registry: bytes, expected_name: str, *, fixture_override: bool) -> Path:
    try:
        lines = registry.decode("utf-8-sig").splitlines()
        if len(lines) != 1:
            _fail("SINGLE_SELECTED_DISK_LIBRARY_REQUIRED")
        fields = lines[0].split(":")
        if (
            len(fields) != 6
            or fields[0] != expected_name
            or fields[2:] != ["*", "", "", "DISK"]
        ):
            _fail("SINGLE_SELECTED_DISK_LIBRARY_REQUIRED")
        encoded = fields[1]
        if re.fullmatch(r"[A-Za-z]\\[^:\x00-\x1f\x7f]+", encoded):
            if any(part in {"", ".", ".."} for part in encoded.split("\\")):
                _fail("LIBRARY_PATH_ENCODING_UNSUPPORTED")
            path = Path(encoded[0] + ":" + encoded[1:])
        elif fixture_override and os.name != "nt" and encoded.startswith("/"):
            path = Path(encoded)
        else:
            _fail("LIBRARY_PATH_ENCODING_UNSUPPORTED")
        _unlinked(path)
        if not path.is_dir():
            _fail("LIBRARY_DIRECTORY_REQUIRED")
        return path
    except (UnicodeError, IndexError) as error:
        raise ProjectLibraryCatalogueError("REGISTRY_FORMAT_UNSUPPORTED") from error


def _entries(path: Path) -> list[Path]:
    _unlinked(path)
    values: list[Path] = []
    for item in path.iterdir():
        if len(values) >= MAX_ENTRIES:
            _fail("LIBRARY_ENTRY_BOUND_EXCEEDED")
        _unlinked(item)
        values.append(item)
    return sorted(values, key=lambda value: value.name)


def _layout(library: Path) -> tuple[list[Path], list[dict[str, Any]]]:
    users = library / "Resolve Projects/Users"
    user_entries = _entries(users)
    if (
        len(user_entries) != 1
        or user_entries[0].name != "guest"
        or not user_entries[0].is_dir()
    ):
        _fail("ONLY_GUEST_USER_SUPPORTED")
    projects = users / "guest/Projects"
    children = _entries(projects)
    if not 1 <= len(children) <= MAX_PROJECTS:
        _fail("PROJECT_COUNT_BOUND_EXCEEDED")
    files: list[Path] = []
    manifest: list[dict[str, Any]] = []
    for directory in (users, projects):
        value = directory.stat()
        manifest.append(
            {
                "path": str(directory),
                "mtime_ns": value.st_mtime_ns,
                "file_id": value.st_ino,
            }
        )
    for child in children:
        if not child.is_dir():
            _fail("ROOT_PROJECT_DIRECTORY_REQUIRED")
        _sidecars(child / "Project.db")
        entries = _entries(child)
        if (
            len(entries) != 1
            or entries[0].name != "Project.db"
            or not entries[0].is_file()
        ):
            _fail("NESTED_OR_UNKNOWN_PROJECT_LAYOUT")
        _sidecars(entries[0])
        value = child.stat()
        manifest.append(
            {"path": str(child), "mtime_ns": value.st_mtime_ns, "file_id": value.st_ino}
        )
        files.append(entries[0])
    return files, manifest


def _project(copy: Path) -> dict[str, str]:
    # URI flags apply only to a private copy that this function owns. Never pass
    # a source path to SQLite: mode=ro on a live WAL database may create sidecars.
    connection = sqlite3.connect(copy.as_uri() + "?mode=ro&immutable=1", uri=True)
    calls = [0]

    def bounded_progress() -> int:
        calls[0] += 1
        return int(calls[0] > 2000)

    connection.set_progress_handler(bounded_progress, 1000)
    try:
        if connection.execute("PRAGMA quick_check(1)").fetchall() != [("ok",)]:
            _fail("PROJECT_DATABASE_INVALID")
        for table, expected in SUPPORTED_TABLES.items():
            kind = connection.execute(
                "SELECT type FROM sqlite_master WHERE name=?",
                (table,),
            ).fetchall()
            if kind != [("table",)]:
                _fail("PROJECT_SCHEMA_UNSUPPORTED")
            columns = tuple(
                (row[1], row[2], row[3], row[5])
                for row in connection.execute(f'PRAGMA table_info("{table}")')
            )
            if columns != expected:
                _fail("PROJECT_SCHEMA_UNSUPPORTED")
        rows = connection.execute(
            "SELECT SM_Project_id,ProjectName,Folder,DbType,ProjectVersion "
            'FROM "SM_Project" LIMIT 2'
        ).fetchall()
        if len(rows) != 1:
            _fail("ONE_PROJECT_ROW_REQUIRED")
        identity, name, folder, kind, version = rows[0]
        if (
            not isinstance(identity, str)
            or not _UUID.fullmatch(identity)
            or not isinstance(name, str)
            or folder is not None
            or kind != "SM_Project"
            or type(version) is not int
            or version != 17
        ):
            _fail("ROOT_PROJECT_PROFILE_UNSUPPORTED")
        bounded_text(name, "Catalogue project name")
        for table in ("SM_ProjectFolder", "SM_Project_FolderMap"):
            if (
                connection.execute(f'SELECT 1 FROM "{table}" LIMIT 1').fetchone()
                is not None
            ):
                _fail("PROJECT_FOLDERS_UNSUPPORTED")
        return {"id": identity, "name": name}
    except sqlite3.Error as error:
        raise ProjectLibraryCatalogueError("PROJECT_DATABASE_UNREADABLE") from error
    finally:
        connection.close()


def verify_project_library_target(
    *,
    expected_database: dict[str, Any],
    project_name: str,
    expected_project_id: str,
    registry_path: Path | None = None,
) -> dict[str, Any]:
    """Verify a unique root candidate from stable private copies, without UI.

    Production callers leave registry_path unset. The override exists for local
    fixture tests; public MCP/UI entrypoints must not expose arbitrary registries.
    Catalogue identity is only a guard and never proves a selected UI item.
    """
    arguments = validate_project_context_request(
        {
            "expected_database": expected_database,
            "expected_folder": "",
            "expected_project_name": project_name,
        },
        expected_project_id,
    )
    if arguments["expected_database"]["DbType"] != "Disk":
        _fail("ONLY_DISK_LIBRARY_SUPPORTED")
    if not _UUID.fullmatch(expected_project_id):
        _fail("CANONICAL_PROJECT_UUID_REQUIRED")
    registry_path = _registry_path() if registry_path is None else Path(registry_path)
    override = registry_path != _registry_path() if os.name == "nt" else True
    try:
        registry, registry_proof = _stable_bytes(registry_path, MAX_REGISTRY_BYTES)
        library = _library(
            registry, expected_database["DbName"], fixture_override=override
        )
        paths, manifest = _layout(library)
        if sum(path.stat().st_size for path in paths) > MAX_TOTAL_DB_BYTES:
            _fail("TOTAL_DATABASE_BOUND_EXCEEDED")
        sources: list[dict[str, Any]] = []
        projects: list[dict[str, str]] = []
        copied_bytes = 0
        with tempfile.TemporaryDirectory(
            prefix="davinci-library-catalogue-"
        ) as temporary:
            working = Path(temporary).resolve()
            # Verify the exact owned cleanup directory before TemporaryDirectory
            # recursively removes this private snapshot on context exit.
            if working.parent != Path(
                tempfile.gettempdir()
            ).resolve() or not working.name.startswith("davinci-library-catalogue-"):
                _fail("PRIVATE_COPY_DIRECTORY_UNVERIFIED")
            for index, path in enumerate(paths):
                data, proof = _stable_bytes(path, MAX_DB_BYTES, database=True)
                copied_bytes += len(data)
                if copied_bytes > MAX_TOTAL_DB_BYTES:
                    _fail("TOTAL_DATABASE_BOUND_EXCEEDED")
                sources.append(proof)
                copy = working / f"{index}.db"
                copy.write_bytes(data)
                project = _project(copy)
                if path.parent.name != project["name"]:
                    _fail("PROJECT_DIRECTORY_NAME_MISMATCH")
                projects.append(project)
        if len({project["id"] for project in projects}) != len(projects):
            _fail("DUPLICATE_PROJECT_UUID")
        names = [
            unicodedata.normalize("NFKC", item["name"]).casefold() for item in projects
        ]
        if len(set(names)) != len(names):
            _fail("AMBIGUOUS_PROJECT_NAMES")
        query = unicodedata.normalize("NFKC", project_name).casefold()
        candidates = [
            item for item, name in zip(projects, names, strict=True) if query in name
        ]
        target = {"id": expected_project_id, "name": project_name}
        if candidates != [target]:
            _fail("TARGET_NOT_UNIQUE_EXACT_ROOT_PROJECT")
        final_registry, final_registry_proof = _stable_bytes(
            registry_path, MAX_REGISTRY_BYTES
        )
        final_paths, final_manifest = _layout(library)
        if (
            registry != final_registry
            or registry_proof != final_registry_proof
            or paths != final_paths
            or manifest != final_manifest
        ):
            _fail("CATALOGUE_CHANGED_DURING_VERIFICATION")
        for path, original in zip(paths, sources, strict=True):
            _, fresh = _stable_bytes(path, MAX_DB_BYTES, database=True)
            if fresh != original:
                _fail("CATALOGUE_CHANGED_DURING_VERIFICATION")
        evidence = {
            "registry": registry_proof,
            "directories": manifest,
            "sources": sources,
        }
        signature = hashlib.sha256(
            json.dumps(
                evidence,
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        target_index = projects.index(target)
        return {
            "verification": "readonly_project_library_copy_v1",
            "schema_profile": "resolve-disk-project-v17-observed-21.1.2",
            "database": arguments["expected_database"],
            "folder": "",
            "project": target,
            "registry_path": str(registry_path),
            "library_path": str(library),
            "project_database_path": str(paths[target_index]),
            "project_count": len(projects),
            "substring_match_count": 1,
            "catalogue_signature": signature,
            "sources": sources,
            "selected_library_registered": True,
            "root_folder_verified": True,
            "source_freshness_verified": True,
            "ui_selection_verified": False,
            "native_connection_verified": False,
        }
    except (OSError, UnicodeError, ValueError) as error:
        raise ProjectLibraryCatalogueError("LIBRARY_SOURCE_UNREADABLE") from error
