"""Media path allowlist and bridge-policy publication."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from agent.configuration import load_config
from agent.paths import runtime_directory
from agent.storage import managed_media_root

POLICY_VERSION = "1.0"
ENVIRONMENT_PLACEHOLDER = re.compile(r"\$\{([A-Z][A-Z0-9_]*)\}")


class MediaPolicyError(ValueError):
    """Raised when a media path violates the configured allowlist."""


def _expand_environment(
    value: str,
    environment: Mapping[str, str],
) -> str:
    def replace(match: re.Match[str]) -> str:
        name = match.group(1)
        replacement = environment.get(name)
        if not replacement:
            raise MediaPolicyError(
                f"Required environment variable {name} is not set."
            )
        return replacement

    return ENVIRONMENT_PLACEHOLDER.sub(replace, value)


class MediaPolicy:
    """Validate media paths and publish resolved roots for the bridge."""

    def __init__(
        self,
        allowed_roots: Sequence[Path],
        runtime_root: Path | None = None,
    ) -> None:
        if not allowed_roots:
            raise MediaPolicyError("At least one media root must be configured.")
        self._allowed_roots = tuple(root.resolve() for root in allowed_roots)
        self._runtime_root = (
            runtime_directory() if runtime_root is None else runtime_root
        )

    @classmethod
    def from_local_config(
        cls,
        *,
        config_path: Path | None = None,
        runtime_root: Path | None = None,
        environment: Mapping[str, str] | None = None,
    ) -> MediaPolicy:
        """Build a policy from the machine-local TOML configuration."""
        source = os.environ if environment is None else environment
        config = load_config(config_path)
        media = config.get("media")
        if not isinstance(media, dict):
            raise MediaPolicyError("Configuration section [media] is invalid.")
        roots = media.get("allowed_roots")
        if not isinstance(roots, list) or not all(
            isinstance(root, str) and root for root in roots
        ):
            raise MediaPolicyError(
                "media.allowed_roots must be a non-empty string array."
            )
        resolved_roots = [
                Path(_expand_environment(root, source))
                for root in roots
            ]
        generated_root = managed_media_root(source)
        if generated_root not in resolved_roots:
            resolved_roots.append(generated_root)
        return cls(
            resolved_roots,
            runtime_root,
        )

    def prepare_import(self, paths: Sequence[str]) -> list[str]:
        """Validate import files and publish the bridge-readable policy."""
        normalized = self.validate_files(paths)
        self._publish()
        return normalized

    def validate_files(self, paths: Sequence[str]) -> list[str]:
        """Validate local files without issuing or publishing a write request."""
        if not paths:
            raise MediaPolicyError("At least one media path is required.")

        normalized: list[str] = []
        for raw_path in paths:
            if not isinstance(raw_path, str) or not raw_path:
                raise MediaPolicyError("Every media path must be a string.")
            candidate = Path(raw_path)
            if not candidate.is_absolute():
                raise MediaPolicyError(
                    f"Media path must be absolute: {raw_path}"
                )
            resolved = candidate.resolve()
            if not resolved.is_file():
                raise MediaPolicyError(f"Media file does not exist: {resolved}")
            if not any(
                resolved.is_relative_to(root)
                for root in self._allowed_roots
            ):
                raise MediaPolicyError(
                    f"Media path is outside configured allowed roots: {resolved}"
                )
            normalized.append(str(resolved))
        return normalized

    @property
    def allowed_roots(self) -> tuple[Path, ...]:
        """Return the configured immutable scope without widening it."""
        return self._allowed_roots

    def _publish(self) -> None:
        policy_path = self._runtime_root / "state" / "media-policy.json"
        policy_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = policy_path.with_name(f"{policy_path.name}.tmp")
        payload: dict[str, Any] = {
            "policy_version": POLICY_VERSION,
            "allowed_roots": [str(root) for root in self._allowed_roots],
        }
        with temporary_path.open(
            "w",
            encoding="utf-8",
            newline="\n",
        ) as output:
            json.dump(payload, output, ensure_ascii=False, indent=2, sort_keys=True)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        temporary_path.replace(policy_path)
