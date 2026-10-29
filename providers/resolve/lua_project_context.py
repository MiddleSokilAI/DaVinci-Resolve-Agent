"""Typed read-only verification inputs for one existing root-folder project."""

from __future__ import annotations

from typing import Any

from providers.resolve.lua_editing import bounded_text

PROJECT_CONTEXT_CAPABILITY = "project_context_v1"


def validate_project_context_request(
    arguments: dict[str, Any],
    expected_project_id: str | None,
) -> dict[str, Any]:
    """Validate exact library, root folder and saved-project identity expectations.

    This helper validates data only. It never reads a database, opens a project,
    creates a session or dispatches a native operation.
    """
    identity = bounded_text(expected_project_id, "Expected project ID")
    if len(identity.encode("utf-8")) > 128:
        raise ValueError("Expected project ID exceeds the marker identity bound.")
    if not isinstance(arguments, dict) or set(arguments) != {
        "expected_database",
        "expected_folder",
        "expected_project_name",
    }:
        raise ValueError("Project context requires exact database/folder/name fields.")
    database = arguments["expected_database"]
    if (
        not isinstance(database, dict)
        or not {"DbType", "DbName"} <= database.keys()
        or set(database) - {"DbType", "DbName", "IpAddress"}
        or not isinstance(database["DbType"], str)
        or database["DbType"] not in {"Disk", "PostgreSQL"}
    ):
        raise ValueError("Expected database requires a documented exact tuple.")
    normalized = {
        "DbType": database["DbType"],
        "DbName": bounded_text(database["DbName"], "Expected database name"),
    }
    if "IpAddress" in database:
        if database["DbType"] != "PostgreSQL":
            raise ValueError("Only PostgreSQL database tuples accept IpAddress.")
        normalized["IpAddress"] = bounded_text(
            database["IpAddress"],
            "Expected database address",
        )
    if (
        type(arguments["expected_folder"]) is not str
        or arguments["expected_folder"] != ""
    ):
        raise ValueError(
            "Project context verification currently supports root folder only."
        )
    return {
        "expected_database": normalized,
        "expected_folder": "",
        "expected_project_name": bounded_text(
            arguments["expected_project_name"],
            "Expected project name",
        ),
    }
