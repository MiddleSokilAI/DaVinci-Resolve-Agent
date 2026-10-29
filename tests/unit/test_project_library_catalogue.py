"""Catalogue fixtures are private synthetic files; no live library is opened."""

from __future__ import annotations

import hashlib
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from providers.resolve import project_library_catalogue as module
from providers.resolve.project_library_catalogue import (
    ProjectLibraryCatalogueError,
    verify_project_library_target,
)

IDENTITY = "11111111-2222-4333-8444-555555555555"
OTHER_ID = "66666666-7777-4888-8999-aaaaaaaaaaaa"
NAME = "Test — український проєкт"
DATABASE = {"DbType": "Disk", "DbName": "Local Database"}


def schema(connection: sqlite3.Connection) -> None:
    for table, columns in module.SUPPORTED_TABLES.items():
        parts = [
            f'"{name}" {kind}' + (" NOT NULL" if not_null else "")
            for name, kind, not_null, _ in columns
        ]
        primary = [
            name for name, _, _, rank in sorted(columns, key=lambda row: row[3]) if rank
        ]
        parts.append("PRIMARY KEY (" + ",".join(f'"{name}"' for name in primary) + ")")
        connection.execute(f'CREATE TABLE "{table}" (' + ",".join(parts) + ")")


class Library:
    def __init__(self, tmp_path: Path) -> None:
        self.registry = tmp_path / "dblist.conf"
        self.library = tmp_path / "library"
        self.projects = self.library / "Resolve Projects/Users/guest/Projects"
        self.projects.mkdir(parents=True)
        self.path = self.add(NAME, IDENTITY)
        self.write_registry()

    def add(self, name: str, identity: str) -> Path:
        folder = self.projects / name
        folder.mkdir()
        path = folder / "Project.db"
        with sqlite3.connect(path) as connection:
            schema(connection)
            connection.execute(
                "INSERT INTO SM_Project "
                "(SM_Project_id,DbType,ProjectName,ProjectVersion) "
                "VALUES (?,?,?,?)",
                (identity, "SM_Project", name, 17),
            )
        return path

    def write_registry(
        self, *, alias: str = "Local Database", selected: str = "*"
    ) -> None:
        value = str(self.library)
        if os.name == "nt":
            value = value.replace(":", "", 1)
        self.registry.write_text(
            f"{alias}:{value}:{selected}:::DISK\n", encoding="utf-8"
        )

    def verify(self, **arguments: Any) -> dict[str, Any]:
        return verify_project_library_target(
            **{
                "expected_database": dict(DATABASE),
                "project_name": NAME,
                "expected_project_id": IDENTITY,
                "registry_path": self.registry,
                **arguments,
            }
        )


@pytest.fixture
def library(tmp_path: Path) -> Library:
    return Library(tmp_path)


def test_verifies_unique_root_target_using_private_snapshots_only(
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    before = hashlib.sha256(library.path.read_bytes()).hexdigest()
    original = sqlite3.connect
    opened: list[str] = []

    def connect(path: str, **kwargs: Any) -> sqlite3.Connection:
        assert "davinci-library-catalogue-" in path
        assert "?mode=ro&immutable=1" in path
        opened.append(path)
        connection = original(path, **kwargs)
        assert isinstance(connection, sqlite3.Connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", connect)
    first, second = library.verify(), library.verify()
    assert opened and first["catalogue_signature"] == second["catalogue_signature"]
    assert first["project"] == {"id": IDENTITY, "name": NAME}
    assert first["database"] == DATABASE and first["folder"] == ""
    assert first["project_count"] == first["substring_match_count"] == 1
    assert first["source_freshness_verified"] is True
    assert first["ui_selection_verified"] is False
    assert first["native_connection_verified"] is False
    assert first["project_database_path"] == str(library.path)
    assert before == hashlib.sha256(library.path.read_bytes()).hexdigest()
    assert not any(
        library.path.with_name("Project.db" + suffix).exists()
        for suffix in ("-wal", "-shm", "-journal")
    )


def test_default_path_is_fixed_per_user_registry(
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(module, "_registry_path", lambda: library.registry)
    result = verify_project_library_target(
        expected_database=DATABASE,
        project_name=NAME,
        expected_project_id=IDENTITY,
    )
    assert result["registry_path"] == str(library.registry)


@pytest.mark.parametrize(
    "mode", ["duplicate", "unselected", "alias", "network", "unknown"]
)
def test_rejects_unsupported_registry_without_database_reads(
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
    mode: str,
) -> None:
    if mode == "duplicate":
        library.registry.write_text(library.registry.read_text() * 2)
    elif mode == "unselected":
        library.write_registry(selected="")
    elif mode == "alias":
        library.write_registry(alias="Different library")
    elif mode == "network":
        library.registry.write_text("Local Database:server:*:::POSTGRESQL")
    else:
        library.registry.write_text("Local Database:unknown:*:unexpected::DISK")
    monkeypatch.setattr(
        sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("Source rejected before SQLite"),
    )
    with pytest.raises(ProjectLibraryCatalogueError, match="SINGLE_SELECTED_DISK"):
        library.verify()


def test_other_users_are_unsupported(library: Library) -> None:
    (library.projects.parent.parent / "another-user").mkdir()
    with pytest.raises(ProjectLibraryCatalogueError, match="ONLY_GUEST"):
        library.verify()


def test_nested_project_directories_are_unsupported(library: Library) -> None:
    (library.projects / "Nested" / "Project").mkdir(parents=True)
    with pytest.raises(ProjectLibraryCatalogueError, match="NESTED_OR_UNKNOWN"):
        library.verify()


@pytest.mark.parametrize("suffix", ["-wal", "-shm", "-journal"])
def test_rejects_any_live_database_sidecar(library: Library, suffix: str) -> None:
    library.path.with_name(library.path.name + suffix).write_bytes(b"sidecar")
    with pytest.raises(ProjectLibraryCatalogueError, match="SIDECAR_PRESENT"):
        library.verify()


def test_duplicate_uuid_refuses_even_if_names_differ(library: Library) -> None:
    library.add("Different name", IDENTITY)
    with pytest.raises(ProjectLibraryCatalogueError, match="DUPLICATE_PROJECT_UUID"):
        library.verify()


def test_nonexact_substring_match_is_ambiguous(library: Library) -> None:
    library.add(NAME + " extra", OTHER_ID)
    with pytest.raises(ProjectLibraryCatalogueError, match="NOT_UNIQUE_EXACT"):
        library.verify()


def test_wrong_expected_uuid_refuses(library: Library) -> None:
    with pytest.raises(ProjectLibraryCatalogueError, match="NOT_UNIQUE_EXACT"):
        library.verify(expected_project_id=OTHER_ID)


@pytest.mark.parametrize(
    "change", ["version", "folder", "schema", "two-rows", "folder-table"]
)
def test_rejects_unknown_or_nonroot_database_profile(
    library: Library, change: str
) -> None:
    with sqlite3.connect(library.path) as connection:
        if change == "version":
            connection.execute("UPDATE SM_Project SET ProjectVersion=18")
        elif change == "folder":
            connection.execute("UPDATE SM_Project SET Folder=?", (OTHER_ID,))
        elif change == "schema":
            connection.execute("ALTER TABLE SM_Project ADD COLUMN Unexpected TEXT")
        elif change == "two-rows":
            connection.execute(
                "INSERT INTO SM_Project "
                "(SM_Project_id,DbType,ProjectName,ProjectVersion) "
                "VALUES (?,?,?,17)",
                (OTHER_ID, "SM_Project", "Other"),
            )
        else:
            connection.execute(
                "INSERT INTO SM_ProjectFolder (SM_ProjectFolder_id,DbType,Name) "
                "VALUES (?,?,?)",
                (OTHER_ID, "SM_ProjectFolder", "Nested"),
            )
    with pytest.raises(ProjectLibraryCatalogueError):
        library.verify()


@pytest.mark.parametrize("source", ["registry", "database", "layout"])
def test_source_change_during_private_query_refuses_final_proof(
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
) -> None:
    original = module._project
    changed = [False]

    def query(copy: Path) -> dict[str, str]:
        result = original(copy)
        if not changed[0]:
            changed[0] = True
            if source == "registry":
                library.registry.write_text(
                    library.registry.read_text().replace("Local Database", "Different")
                )
            elif source == "database":
                before = library.path.stat()
                data = library.path.read_bytes()
                library.path.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
                os.utime(library.path, ns=(before.st_atime_ns, before.st_mtime_ns))
            else:
                library.add("New project", OTHER_ID)
        return result

    monkeypatch.setattr(module, "_project", query)
    with pytest.raises(ProjectLibraryCatalogueError, match="CHANGED_DURING"):
        library.verify()


def test_bounded_input_files_and_totals(
    library: Library, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(module, "MAX_TOTAL_DB_BYTES", 1)
    with pytest.raises(ProjectLibraryCatalogueError, match="TOTAL_DATABASE_BOUND"):
        library.verify()


def test_linked_registry_is_rejected(library: Library, tmp_path: Path) -> None:
    linked = tmp_path / "linked.conf"
    try:
        linked.symlink_to(library.registry)
    except OSError:
        pytest.skip("The host does not permit test symlinks.")
    with pytest.raises(ProjectLibraryCatalogueError, match="LINKED_LIBRARY_SOURCE"):
        library.verify(registry_path=linked)


def test_rejects_noncanonical_id_or_non_disk_request(library: Library) -> None:
    with pytest.raises(ProjectLibraryCatalogueError, match="CANONICAL_PROJECT_UUID"):
        library.verify(expected_project_id="native-id-without-uuid-format")
    with pytest.raises(ProjectLibraryCatalogueError, match="ONLY_DISK_LIBRARY"):
        library.verify(
            expected_database={"DbType": "PostgreSQL", "DbName": "Local Database"}
        )
