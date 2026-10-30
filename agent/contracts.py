"""Canonical JSON Schema validation for external agent messages."""

from __future__ import annotations

import json
from functools import lru_cache
from importlib import resources
from typing import Any, Literal

from jsonschema import Draft202012Validator, FormatChecker

ContractName = Literal[
    "command",
    "response",
    "capability",
    "workflow-capabilities",
    "rough-cut-plan",
    "rough-cut-approval",
    "rough-cut-apply-result",
    "synchronized-pair-result",
    "picture-in-picture-result",
    "synchronized-link-result",
    "pause-compaction-preview",
    "pause-compaction-result",
    "pause-compaction-finalization",
    "finalized-render-preparation",
    "finalized-render-execution",
    "finalized-audio-extraction",
    "finalized-audio-integration",
    "subtitle-generation",
    "editing-recipe",
    "editing-recipe-run",
    "visual-treatment-result",
    "baseline-edit-qa",
    "baseline-edit-run",
    "baseline-edit-status",
    "broll-preview",
    "broll-result",
    "animation-template",
    "animation-template-preview",
    "animation-template-result",
    "color-preset",
    "color-treatment-preview",
    "color-treatment-result",
    "take-selection",
    "take-selection-review",
    "take-sequence",
    "take-sequence-binding",
    "take-sequence-assembly-preview",
    "take-sequence-timeline-preview",
    "take-sequence-media-import-preview",
    "take-sequence-media-import-result",
    "take-sequence-timeline-apply-preview",
    "take-sequence-timeline-apply-result",
    "take-sequence-qc",
    "take-sequence-qc-review",
    "take-sequence-render-preview",
    "take-sequence-render-result",
    "take-sequence-render-status",
    "audio-report",
    "diagnostics-bundle",
    "audit-record",
    "workflow-audit-record",
]


class ContractValidationError(ValueError):
    """Raised when a protocol payload violates its canonical schema."""


@lru_cache(maxsize=32)
def _validator(contract_name: ContractName) -> Draft202012Validator:
    resource = resources.files("contracts").joinpath(f"{contract_name}.schema.json")
    with resource.open("r", encoding="utf-8") as contract_file:
        schema = json.load(contract_file)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def validate_contract(
    contract_name: ContractName,
    payload: Any,
) -> None:
    """Validate a payload and raise one concise actionable error."""
    errors = sorted(
        _validator(contract_name).iter_errors(payload),
        key=lambda error: [str(part) for part in error.absolute_path],
    )
    if not errors:
        return

    error = errors[0]
    path = ".".join(str(part) for part in error.absolute_path)
    location = path or "<root>"
    raise ContractValidationError(
        f"{contract_name} contract violation at {location}: {error.message}"
    )
