"""Provider-neutral application services exposed to user-facing adapters."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, Protocol, TypeVar

from agent.animation_workflow import AnimationTemplateWorkflow
from agent.audio_workflow import AudioReportInspector, DialogueAudioWorkflow
from agent.baseline_edit import BaselineEditWorkflow
from agent.bridge_state import (
    DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    bridge_is_healthy,
    heartbeat_age_seconds,
    load_bridge_state,
)
from agent.broll import BrollWorkflow
from agent.color_workflow import ColorTreatmentWorkflow
from agent.finalized_audio_extraction import FinalizedAudioExtractor
from agent.finalized_audio_integration import FinalizedAudioIntegrator
from agent.finalized_render import FinalizedRenderPreparer
from agent.finalized_render_execution import FinalizedRenderExecutor
from agent.media import MediaPolicy
from agent.paths import transcription_models_directory
from agent.pause_compaction import PauseCompactionApplier, PauseCompactionPreviewer
from agent.pause_compaction_finalize import PauseCompactionFinalizer
from agent.picture_in_picture import PictureInPictureComposer
from agent.recipes import EditingRecipeRunner
from agent.rendering import (
    DEFAULT_RENDER_PROFILE,
    validate_render_job_id,
    validate_render_name,
    validate_render_profile,
    verify_render_output,
)
from agent.rough_cut import (
    RoughCutInspector,
    RoughCutPlanner,
    RoughCutReviewer,
)
from agent.rough_cut_apply import RoughCutApplier
from agent.subtitles import SubtitleGenerator
from agent.synchronized_link import SynchronizedScreenLinker
from agent.synchronized_pair import SynchronizedPairAssembler
from agent.take_selection import TakeSelectionWorkflow
from agent.take_sequence import TakeSequenceWorkflow
from agent.take_sequence_assembly import TakeSequenceAssemblyWorkflow
from agent.take_sequence_binding import TakeSequenceBindingWorkflow
from agent.take_sequence_media_import import TakeSequenceMediaImportWorkflow
from agent.take_sequence_media_import_apply import TakeSequenceMediaImporter
from agent.take_sequence_qc import TakeSequenceQcWorkflow
from agent.take_sequence_render import TakeSequenceRenderWorkflow
from agent.take_sequence_timeline_apply import TakeSequenceTimelineApplyWorkflow
from agent.take_sequence_timeline_mapping import TakeSequenceTimelineMappingWorkflow
from agent.visual_treatment import VisualTreatmentWorkflow
from providers.resolve import ResolveProviderClient
from providers.transcription import FasterWhisperTranscriber

MAX_COMMAND_TIMEOUT_SECONDS = 300.0
MAX_FRAME_VALUE = 2_147_483_647
MAX_TRACK_INDEX = 128
ApplicationResultT = TypeVar("ApplicationResultT")


class ResolveReader(Protocol):
    """Read-only Resolve operations required by the application layer."""

    def current_project(self, timeout_seconds: float = 30) -> dict[str, Any] | None:
        """Return the current project."""

    def timelines(self, timeout_seconds: float = 30) -> list[dict[str, Any]]:
        """Return timelines in the current project."""

    def current_timeline(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any] | None:
        """Return the current timeline."""

    def timeline_items(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded metadata for items in one timeline."""

    def media_pool_items(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded identity metadata for Media Pool items."""

    def editing_metadata(
        self,
        timeline_id: str,
        asset_ids: list[str],
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return placement-relevant media metadata and target track counts."""

    def subtitle_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return bounded subtitle tracks and native auto-caption availability."""

    def animation_template_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return installed animation template and documented method evidence."""

    def color_environment(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented color-version and node-graph metadata."""

    def create_subtitles_from_audio(
        self,
        timeline_id: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create fixed-policy native subtitles after project backup."""

    def workspace_snapshot(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return the fixed read-only workspace view."""

    def render_environment(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented render discovery information."""

    def stop_bridge(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Request clean shutdown of the persistent Resolve bridge."""

    def import_media(
        self,
        paths: list[str],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Import media files."""

    def close_project(
        self,
        expected_project_id: str,
        *,
        confirm_close: bool,
        timeout_seconds: float = 30,
        idempotency_key: str,
    ) -> dict[str, Any]:
        """Save, back up and close the exact current project."""

    def create_project(
        self,
        name: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a unique project, preserving existing projects."""

    def create_timeline(
        self,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create an empty timeline."""

    def ensure_timeline_tracks(
        self,
        timeline_id: str,
        video_track_count: int,
        audio_track_count: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ensure bounded minimum timeline track counts."""

    def duplicate_timeline(
        self,
        timeline_id: str,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Duplicate one existing timeline."""

    def set_current_timeline(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select one current timeline."""

    def append_clip(
        self,
        timeline_id: str,
        asset_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append an asset to a timeline."""

    def insert_title(
        self,
        timeline_id: str,
        title_name: str,
        timecode: str,
        *,
        confirm_insert: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one installed standard title at an exact timecode."""

    def insert_animation_template(
        self,
        timeline_id: str,
        template_id: str,
        timecode: str,
        *,
        confirm_insert: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one allowlisted packaged Fusion title."""

    def append_subtitle_file(
        self,
        timeline_id: str,
        asset_id: str,
        subtitle_path: str,
        import_idempotency_key: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append one receipt-bound SRT and report its placement anchor."""

    def insert_clip(
        self,
        timeline_id: str,
        asset_id: str,
        source_start_frame: int,
        source_end_frame: int,
        position_frames: int,
        track_type: str,
        track_index: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert a bounded source range on one timeline track."""

    def insert_clips(
        self,
        timeline_id: str,
        placements: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert bounded source ranges in one provider operation."""

    def set_clip_enabled(
        self,
        timeline_id: str,
        timeline_item_id: str,
        enabled: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set one timeline item's enabled state."""

    def set_clips_linked(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Link or unlink a bounded group of timeline items."""

    def set_clip_link_groups(
        self,
        timeline_id: str,
        groups: list[list[str]],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply bounded independent link groups in one operation."""

    def set_clip_transform(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        position_x: float | None = None,
        position_y: float | None = None,
        zoom: float | None = None,
        rotation_degrees: float | None = None,
        opacity_percent: float | None = None,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply one bounded provider-neutral video clip transform."""

    def set_clip_transforms(
        self,
        timeline_id: str,
        items: list[dict[str, Any]],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply bounded video transforms in one provider operation."""

    def apply_color_preset(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        preset_id: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply one fixed packaged color preset."""

    def delete_clip(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        confirm_delete: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Delete exactly one timeline item without ripple."""

    def add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str,
        note: str,
        duration: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a timeline marker."""

    def prepare_render_job(
        self,
        custom_name: str,
        *,
        timeline_id: str | None = None,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Prepare one fixed render job."""

    def render_job_status(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return the current status of one render job."""

    def start_render_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one agent-prepared render job."""


class MediaImportPolicy(Protocol):
    """Security policy applied before media paths reach a provider."""

    def prepare_import(self, paths: list[str]) -> list[str]:
        """Validate paths and publish the bridge policy."""

    def validate_files(self, paths: list[str]) -> list[str]:
        """Validate paths without publishing a Resolve write policy."""


class RoughCutPlanBuilder(Protocol):
    """Provider-neutral draft planner required by the application layer."""

    def create_plan(
        self,
        *,
        screen_file: str,
        webcam_file: str,
        screen_audio_file: str,
        webcam_audio_file: str,
        speech_audio_file: str,
        timeline_name: str,
        max_sync_offset_ms: int = 30_000,
        pause_threshold_dbfs: float = -40.0,
        min_pause_duration_ms: int = 700,
        preserve_context_ms: int = 120,
    ) -> dict[str, Any]:
        """Create a review-only rough-cut plan."""


class RoughCutPlanReviewer(Protocol):
    """Explicit local approval boundary for one stored rough-cut plan."""

    def approve(
        self,
        plan_id: str,
        *,
        confirm_review: bool,
    ) -> dict[str, Any]:
        """Persist one idempotent approval record."""


class RoughCutPlanInspector(Protocol):
    """Read-only access to validated local rough-cut artifacts."""

    def get_plan(self, plan_id: str) -> dict[str, Any]:
        """Return one draft and its effective approval state."""

    def list_plans(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded plan summaries."""


class RoughCutPlanApplier(Protocol):
    """Preview or apply an approved rough-cut plan to a copied timeline."""

    def preview(
        self,
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
    ) -> dict[str, Any]:
        """Return a write-free apply preview."""

    def apply(
        self,
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply the supported plan to the copied timeline."""


class SynchronizedPairWorkflow(Protocol):
    """Provider-neutral synchronized screen/webcam assembly boundary."""

    def assemble(
        self,
        *,
        timeline_name: str,
        screen_asset_id: str,
        webcam_asset_id: str,
        webcam_offset_ms: int,
        confirm_sync: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create and populate one synchronized V1/A1/V2 timeline."""


class PictureInPictureWorkflow(Protocol):
    """Provider-neutral synchronized webcam layout boundary."""

    def compose(
        self,
        *,
        synchronized_pair_receipt_id: str,
        size_percent: float,
        center_x_percent: float,
        center_y_percent: float,
        confirm_layout: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one normalized picture-in-picture webcam layout."""


class SynchronizedLinkWorkflow(Protocol):
    """Provider-neutral synchronized screen link boundary."""

    def link(
        self,
        *,
        synchronized_pair_receipt_id: str,
        confirm_link: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Link canonical screen video/audio items from one M38 receipt."""


class PauseCompactionWorkflow(Protocol):
    """Provider-neutral pause-removal rebuild preview boundary."""

    def preview(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return kept-range V1/A1/V2 placements without editing Resolve."""


class PauseCompactionApplyWorkflow(Protocol):
    """Provider-neutral confirmed pause-removal rebuild boundary."""

    def apply(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create one new timeline from approved kept ranges."""


class PauseCompactionFinalizationWorkflow(Protocol):
    """Provider-neutral M42 segment finalization boundary."""

    def finalize(
        self,
        *,
        pause_compaction_receipt_id: str,
        picture_in_picture_receipt_id: str,
        synchronized_link_receipt_id: str,
        confirm_finalize: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Propagate approved links and webcam layout to M42 segments."""


class FinalizedRenderPreparationWorkflow(Protocol):
    """Provider-neutral M44 finalized render preparation boundary."""

    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        profile: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare one render job bound to an applied M43 timeline."""


class FinalizedRenderExecutionWorkflow(Protocol):
    """Provider-neutral M45 render start and inspection boundary."""

    def start(
        self,
        *,
        preparation_receipt_id: str,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start one applied M44 render job exactly once."""

    def status(
        self,
        execution_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return live render and managed output validation."""


class FinalizedAudioExtractionWorkflow(Protocol):
    """Provider-neutral M46 finalized timeline audio extraction boundary."""

    def prepare(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare one fixed full-timeline PCM WAV job."""

    def start(
        self,
        extraction_receipt_id: str,
        *,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start one prepared M46 audio job exactly once."""

    def status(
        self,
        extraction_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return live audio render and strict PCM WAV validation."""


class FinalizedAudioIntegrationWorkflow(Protocol):
    """Provider-neutral M46 cleaned-audio integration boundary."""

    def apply(
        self,
        *,
        extraction_receipt_id: str,
        audio_report_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Insert validated processed audio and disable its source A1 items."""


class DialogueAudioProcessor(Protocol):
    """Provider-neutral M6 dialogue workflow."""

    def process(
        self,
        source_file: str,
        *,
        preset: str = "pcm-dialogue-level-v1",
    ) -> dict[str, Any]:
        """Create derived audio and a before/after report."""


class AudioReportReader(Protocol):
    """Read-only access to validated local audio reports."""

    def get_report(self, report_id: str) -> dict[str, Any]:
        """Return one canonical before/after report."""

    def list_reports(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded report summaries."""


class WorkflowOperationAuditor(Protocol):
    """Audit provider-neutral local operations without inspecting inputs."""

    def run(
        self,
        operation: str,
        callback: Callable[[], ApplicationResultT],
    ) -> ApplicationResultT:
        """Run one allowlisted local operation with lifecycle audit."""


class SubtitleGenerationWorkflow(Protocol):
    """Local transcription and provider subtitle-application workflow."""

    def generate(
        self,
        source_file: str,
        timeline_id: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Generate and apply one bounded subtitle artifact."""


class EditingRecipeWorkflow(Protocol):
    """Validated declarative editing recipe boundary."""

    def list_recipes(self) -> dict[str, Any]: ...

    def get_recipe(self, recipe_id: str) -> dict[str, Any]: ...

    def preview(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]: ...

    def run(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
        *,
        confirm_execute: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

class VisualTreatmentService(Protocol):
    """Bounded title and static reframing workflow boundary."""

    def preview(
        self,
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
    ) -> dict[str, Any]: ...

    def apply(
        self,
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class BaselineEditService(Protocol):
    """Baseline edit QA and deterministic final-render boundary."""

    def preview(
        self,
        *,
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def start(
        self,
        *,
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        custom_name: str,
        profile: str,
        confirm_render: bool,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class BrollService(Protocol):
    """Reviewed B-roll preview and duplicate-timeline apply boundary."""

    def preview(
        self,
        *,
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def apply(
        self,
        *,
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class AnimationTemplateService(Protocol):
    """Packaged M52 template catalogue, preview and apply boundary."""

    def list_templates(self) -> dict[str, Any]: ...

    def get_template(self, template_id: str) -> dict[str, Any]: ...

    def preview(
        self,
        *,
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def apply(
        self,
        *,
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class ColorTreatmentService(Protocol):
    """Packaged M53 color-preset catalogue and preview boundary."""

    def list_presets(self) -> dict[str, Any]: ...

    def get_preset(self, preset_id: str) -> dict[str, Any]: ...

    def preview(
        self,
        *,
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def apply(
        self,
        *,
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class TakeSelectionService(Protocol):
    """M54 bounded technical analysis and immutable review boundary."""

    def analyze(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
    ) -> dict[str, Any]: ...

    def analyze_scripted(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
        reference_text: str,
    ) -> dict[str, Any]: ...

    def get(self, selection_id: str) -> dict[str, Any]: ...

    def list(self, limit: int = 20) -> dict[str, Any]: ...

    def review(
        self,
        *,
        selection_id: str,
        decision: str,
        selected_candidate_id: str | None,
        note: str = "",
    ) -> dict[str, Any]: ...

    def get_review(self, selection_id: str) -> dict[str, Any]: ...


class TakeSequenceService(Protocol):
    """M54.4 approved provider-neutral sequence-plan boundary."""

    def compose(
        self,
        *,
        sequence_name: str,
        selection_ids: list[str],
    ) -> dict[str, Any]: ...

    def get(self, sequence_id: str) -> dict[str, Any]: ...

    def list(self, limit: int = 20) -> dict[str, Any]: ...


class TakeSequenceBindingService(Protocol):
    """M55.1 machine-local approved sequence source-binding boundary."""

    def bind(
        self,
        *,
        sequence_id: str,
        sources: list[dict[str, str | int]],
    ) -> dict[str, Any]: ...

    def get(self, binding_id: str) -> dict[str, Any]: ...

    def resolve_sources(self, binding_id: str) -> list[dict[str, Any]]: ...


class TakeSequenceAssemblyService(Protocol):
    """M55.2 provider-neutral read-only assembly-preview boundary."""

    def preview(
        self,
        *,
        binding_id: str,
        assembly_name: str,
    ) -> dict[str, Any]: ...


class TakeSequenceTimelineMappingService(Protocol):
    """M55.3 live target timeline frame-mapping preview boundary."""

    def preview(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class TakeSequenceMediaImportService(Protocol):
    """M55.4 read-only Media Pool import-preview boundary."""

    def preview(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class TakeSequenceMediaImportApplyService(Protocol):
    """M55.5 confirmed receipt-backed Media Pool import boundary."""

    def apply(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        expected_plan_id: str,
        confirm_import: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def get(self, receipt_id: str) -> dict[str, Any]: ...


class TakeSequenceTimelineApplyService(Protocol):
    """M55.6 duplicate-timeline sequence application boundary."""

    def preview(
        self,
        *,
        media_import_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def apply(
        self,
        *,
        media_import_receipt_id: str,
        target_timeline_name: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def get(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class TakeSequenceQcService(Protocol):
    """M55.7 structural QC and immutable human-review boundary."""

    def inspect(
        self,
        timeline_apply_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def get(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def review(
        self,
        *,
        report_id: str,
        decision: str,
        note: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def get_review(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class TakeSequenceRenderService(Protocol):
    """M55.8 approved render preview/start/status boundary."""

    def preview(
        self,
        *,
        qc_report_id: str,
        custom_name: str,
        profile: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def start(
        self,
        *,
        qc_report_id: str,
        custom_name: str,
        profile: str,
        expected_plan_id: str,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...

    def status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]: ...


class AgentApplication:
    """Coordinate core status and provider operations for external adapters."""

    def __init__(
        self,
        resolve: ResolveReader | None = None,
        state_loader: Callable[[], dict[str, Any]] = load_bridge_state,
        media_policy: MediaImportPolicy | None = None,
        rough_cut_planner: RoughCutPlanBuilder | None = None,
        rough_cut_reviewer: RoughCutPlanReviewer | None = None,
        rough_cut_inspector: RoughCutPlanInspector | None = None,
        rough_cut_applier: RoughCutPlanApplier | None = None,
        synchronized_pair_assembler: SynchronizedPairWorkflow | None = None,
        picture_in_picture_composer: PictureInPictureWorkflow | None = None,
        synchronized_screen_linker: SynchronizedLinkWorkflow | None = None,
        pause_compaction_previewer: PauseCompactionWorkflow | None = None,
        pause_compaction_applier: PauseCompactionApplyWorkflow | None = None,
        pause_compaction_finalizer: PauseCompactionFinalizationWorkflow | None = None,
        finalized_render_preparer: FinalizedRenderPreparationWorkflow | None = None,
        finalized_render_executor: FinalizedRenderExecutionWorkflow | None = None,
        finalized_audio_extractor: FinalizedAudioExtractionWorkflow | None = None,
        finalized_audio_integrator: FinalizedAudioIntegrationWorkflow | None = None,
        audio_processor: DialogueAudioProcessor | None = None,
        audio_report_inspector: AudioReportReader | None = None,
        subtitle_generator: SubtitleGenerationWorkflow | None = None,
        editing_recipe_runner: EditingRecipeWorkflow | None = None,
        visual_treatment_service: VisualTreatmentService | None = None,
        baseline_edit_service: BaselineEditService | None = None,
        broll_service: BrollService | None = None,
        animation_template_service: AnimationTemplateService | None = None,
        color_treatment_service: ColorTreatmentService | None = None,
        take_selection_service: TakeSelectionService | None = None,
        take_sequence_service: TakeSequenceService | None = None,
        take_sequence_binding_service: TakeSequenceBindingService | None = None,
        take_sequence_assembly_service: TakeSequenceAssemblyService | None = None,
        take_sequence_timeline_mapping_service: (
            TakeSequenceTimelineMappingService | None
        ) = None,
        take_sequence_media_import_service: (
            TakeSequenceMediaImportService | None
        ) = None,
        take_sequence_media_import_apply_service: (
            TakeSequenceMediaImportApplyService | None
        ) = None,
        take_sequence_timeline_apply_service: (
            TakeSequenceTimelineApplyService | None
        ) = None,
        take_sequence_qc_service: TakeSequenceQcService | None = None,
        take_sequence_render_service: TakeSequenceRenderService | None = None,
        workflow_audit: WorkflowOperationAuditor | None = None,
    ) -> None:
        self._resolve = ResolveProviderClient() if resolve is None else resolve
        self._state_loader = state_loader
        self._media_policy = media_policy
        self._rough_cut_planner = rough_cut_planner
        self._rough_cut_reviewer = rough_cut_reviewer
        self._rough_cut_inspector = rough_cut_inspector
        self._rough_cut_applier = rough_cut_applier
        self._synchronized_pair_assembler = synchronized_pair_assembler
        self._picture_in_picture_composer = picture_in_picture_composer
        self._synchronized_screen_linker = synchronized_screen_linker
        self._pause_compaction_previewer = pause_compaction_previewer
        self._pause_compaction_applier = pause_compaction_applier
        self._pause_compaction_finalizer = pause_compaction_finalizer
        self._finalized_render_preparer = finalized_render_preparer
        self._finalized_render_executor = finalized_render_executor
        self._finalized_audio_extractor = finalized_audio_extractor
        self._finalized_audio_integrator = finalized_audio_integrator
        self._audio_processor = audio_processor
        self._audio_report_inspector = audio_report_inspector
        self._subtitle_generator = subtitle_generator
        self._editing_recipe_runner = editing_recipe_runner
        self._visual_treatment = visual_treatment_service
        self._baseline_edit = baseline_edit_service
        self._broll = broll_service
        self._animation_templates = animation_template_service
        self._color_treatment = color_treatment_service
        self._take_selection = take_selection_service
        self._take_sequence = take_sequence_service
        self._take_sequence_binding = take_sequence_binding_service
        self._take_sequence_assembly = take_sequence_assembly_service
        self._take_sequence_timeline_mapping = (
            take_sequence_timeline_mapping_service
        )
        self._take_sequence_media_import = take_sequence_media_import_service
        self._take_sequence_media_import_apply = (
            take_sequence_media_import_apply_service
        )
        self._take_sequence_timeline_apply = take_sequence_timeline_apply_service
        self._take_sequence_qc = take_sequence_qc_service
        self._take_sequence_render = take_sequence_render_service
        self._workflow_audit = workflow_audit

    def status(
        self,
        max_age_seconds: int = DEFAULT_HEARTBEAT_MAX_AGE_SECONDS,
    ) -> dict[str, Any]:
        """Return cached bridge state with computed health information."""
        if max_age_seconds < 1:
            raise ValueError("max_age_seconds must be greater than zero.")

        state = self._state_loader()
        return {
            "healthy": bridge_is_healthy(
                state,
                max_age_seconds=max_age_seconds,
            ),
            "heartbeat_age_seconds": heartbeat_age_seconds(state),
            "bridge": state,
        }

    def resolve_get_project(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any] | None:
        """Return the current Resolve project through the provider."""
        return self._resolve.current_project(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_list_timelines(
        self,
        timeout_seconds: float = 30,
    ) -> list[dict[str, Any]]:
        """Return Resolve timelines through the provider."""
        return self._resolve.timelines(self._validated_timeout(timeout_seconds))

    def resolve_get_timeline(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any] | None:
        """Return the current Resolve timeline through the provider."""
        return self._resolve.current_timeline(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_list_timeline_items(
        self,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """List addressable video/audio items in one Resolve timeline."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        return self._resolve.timeline_items(
            timeline_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_list_media_pool_items(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """List addressable items in the current Resolve Media Pool."""
        return self._resolve.media_pool_items(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_get_editing_metadata(
        self,
        timeline_id: str,
        asset_ids: list[str],
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read documented source duration/FPS and target timeline tracks."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if (
            not 0 <= len(asset_ids) <= 100
            or len(set(asset_ids)) != len(asset_ids)
            or not all(
                isinstance(asset_id, str)
                and 1 <= len(asset_id) <= 128
                for asset_id in asset_ids
            )
        ):
            raise ValueError(
                "asset_ids must contain 0 to 100 unique identifiers of up to "
                "128 characters."
            )
        return self._resolve.editing_metadata(
            timeline_id,
            asset_ids,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_get_subtitle_environment(
        self,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read subtitle tracks and documented native API availability."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        return self._resolve.subtitle_environment(
            timeline_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_get_animation_template_environment(
        self,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read packaged template integrity and documented Fusion methods."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        return self._resolve.animation_template_environment(
            timeline_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_get_color_environment(
        self,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read documented color versions and node graphs for one timeline."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        return self._resolve.color_environment(
            timeline_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_create_subtitles_from_audio(
        self,
        timeline_id: str,
        *,
        confirm_create: bool = False,
        timeout_seconds: float = 300,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create and verify native captions for one canonical timeline."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if confirm_create is not True:
            raise ValueError("confirm_create must be true.")
        return self._resolve.create_subtitles_from_audio(
            timeline_id,
            confirm_create=True,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def generate_subtitles(
        self,
        source_file: str,
        timeline_id: str,
        *,
        confirm_apply: bool = False,
        timeout_seconds: float = 300,
    ) -> dict[str, Any]:
        """Transcribe locally and apply a generated SRT to one timeline."""
        generator = self._subtitle_generator
        if generator is None:
            policy = self._media_policy or MediaPolicy.from_local_config()
            generator = SubtitleGenerator(
                FasterWhisperTranscriber(transcription_models_directory()),
                self._resolve,
                policy,
            )
        return generator.generate(
            source_file,
            timeline_id,
            confirm_apply=confirm_apply,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_get_workspace_snapshot(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Collect the current Resolve workspace through one bridge command."""
        return self._resolve.workspace_snapshot(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_get_render_options(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return Resolve render options without modifying the project."""
        return self._resolve.render_environment(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_stop_bridge(
        self,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Request clean shutdown without changing the Resolve project."""
        return self._resolve.stop_bridge(
            self._validated_timeout(timeout_seconds)
        )

    def resolve_import_media(
        self,
        paths: list[str],
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Validate and import media through the Resolve provider."""
        policy = (
            MediaPolicy.from_local_config()
            if self._media_policy is None
            else self._media_policy
        )
        normalized = policy.prepare_import(paths)
        return self._resolve.import_media(
            normalized,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_close_project(
        self,
        expected_project_id: str,
        *,
        confirm_close: bool,
        idempotency_key: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Close a confirmed native identity with a required stable retry key."""
        if (
            not isinstance(expected_project_id, str)
            or not 1 <= len(expected_project_id) <= 128
            or expected_project_id != expected_project_id.strip()
            or any(ord(character) < 32 for character in expected_project_id)
            or confirm_close is not True
            or not isinstance(idempotency_key, str) or not idempotency_key.strip()
        ):
            raise ValueError(
                "Closure requires an exact ID, confirmation and stable key."
            )
        return self._resolve.close_project(
            expected_project_id, confirm_close=True,
            idempotency_key=idempotency_key,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_create_project(
        self,
        name: str,
        *,
        confirm_create: bool,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a confirmed project in the current Resolve library folder."""
        if (
            not isinstance(name, str)
            or not name.strip()
            or name != name.strip()
            or len(name) > 128
            or any(ord(character) < 32 for character in name)
        ):
            raise ValueError("Project name must contain 1 to 128 clean characters.")
        if confirm_create is not True:
            raise ValueError("Project creation requires confirm_create=true.")
        return self._resolve.create_project(
            name,
            confirm_create=True,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_create_timeline(
        self,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a Resolve timeline."""
        if not name.strip():
            raise ValueError("Timeline name must not be empty.")
        return self._resolve.create_timeline(
            name,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_append_clip(
        self,
        timeline_id: str,
        asset_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Append a media asset to a Resolve timeline."""
        if not timeline_id or not asset_id:
            raise ValueError("timeline_id and asset_id must not be empty.")
        return self._resolve.append_clip(
            timeline_id,
            asset_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_insert_title(
        self,
        timeline_id: str,
        title_name: str,
        timecode: str,
        *,
        confirm_insert: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one installed standard Resolve title after confirmation."""
        if not 1 <= len(timeline_id) <= 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if not 1 <= len(title_name) <= 128:
            raise ValueError("title_name must contain 1 to 128 characters.")
        if confirm_insert is not True:
            raise ValueError("confirm_insert must be true.")
        return self._resolve.insert_title(
            timeline_id,
            title_name,
            timecode,
            confirm_insert=True,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_insert_animation_template(
        self,
        timeline_id: str,
        template_id: str,
        timecode: str,
        *,
        confirm_insert: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one packaged M52 template after explicit confirmation."""
        if not 1 <= len(timeline_id) <= 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        if template_id != "accent-card-v1":
            raise ValueError("template_id is not an allowlisted animation template.")
        if confirm_insert is not True:
            raise ValueError("confirm_insert must be true.")
        return self._resolve.insert_animation_template(
            timeline_id,
            template_id,
            timecode,
            confirm_insert=True,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_ensure_timeline_tracks(
        self,
        timeline_id: str,
        video_track_count: int,
        audio_track_count: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Ensure the timeline has bounded minimum video/audio tracks."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        for field, value in (
            ("video_track_count", video_track_count),
            ("audio_track_count", audio_track_count),
        ):
            if (
                not isinstance(value, int)
                or isinstance(value, bool)
                or not 1 <= value <= 8
            ):
                raise ValueError(f"{field} must be between 1 and 8.")
        return self._resolve.ensure_timeline_tracks(
            timeline_id,
            video_track_count,
            audio_track_count,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_duplicate_timeline(
        self,
        timeline_id: str,
        name: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Duplicate one Resolve timeline after a project backup."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        if not name.strip() or len(name) > 128:
            raise ValueError(
                "Timeline name must contain 1 to 128 characters."
            )
        return self._resolve.duplicate_timeline(
            timeline_id,
            name,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_set_current_timeline(
        self,
        timeline_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Select one existing Resolve timeline."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        return self._resolve.set_current_timeline(
            timeline_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_insert_clip(
        self,
        timeline_id: str,
        asset_id: str,
        source_start_frame: int,
        source_end_frame: int,
        position_frames: int,
        track_type: str,
        track_index: int,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Insert one bounded media range into a Resolve timeline."""
        if not timeline_id or not asset_id:
            raise ValueError("timeline_id and asset_id must not be empty.")
        if (
            not 0 <= source_start_frame < source_end_frame <= MAX_FRAME_VALUE
        ):
            raise ValueError(
                "source frames must be ordered within the supported range."
            )
        if not 0 <= position_frames <= MAX_FRAME_VALUE:
            raise ValueError("position_frames must be a non-negative integer.")
        if track_type not in {"video", "audio"}:
            raise ValueError("track_type must be 'video' or 'audio'.")
        if not 1 <= track_index <= MAX_TRACK_INDEX:
            raise ValueError("track_index must be between 1 and 128.")
        return self._resolve.insert_clip(
            timeline_id,
            asset_id,
            source_start_frame,
            source_end_frame,
            position_frames,
            track_type,
            track_index,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_add_marker(
        self,
        timeline_id: str,
        frame: int,
        color: str,
        name: str = "",
        note: str = "",
        duration: int = 1,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Add a marker to a Resolve timeline."""
        if not timeline_id or not color:
            raise ValueError("timeline_id and color must not be empty.")
        if frame < 0 or duration < 1:
            raise ValueError("frame must be non-negative and duration positive.")
        return self._resolve.add_marker(
            timeline_id,
            frame,
            color,
            name,
            note,
            duration,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_set_clip_enabled(
        self,
        timeline_id: str,
        timeline_item_id: str,
        enabled: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Set one Resolve timeline item enabled state."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        if not timeline_item_id or len(timeline_item_id) > 128:
            raise ValueError(
                "timeline_item_id must contain 1 to 128 characters."
            )
        if not isinstance(enabled, bool):
            raise ValueError("enabled must be a boolean.")
        return self._resolve.set_clip_enabled(
            timeline_id,
            timeline_item_id,
            enabled,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_set_clips_linked(
        self,
        timeline_id: str,
        timeline_item_ids: list[str],
        linked: bool,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Link or unlink 2 to 16 unique timeline items after a backup."""
        if not timeline_id or len(timeline_id) > 128:
            raise ValueError(
                "timeline_id must contain 1 to 128 characters."
            )
        if (
            not isinstance(timeline_item_ids, list)
            or not 2 <= len(timeline_item_ids) <= 16
            or any(
                not isinstance(item_id, str)
                or not item_id
                or len(item_id) > 128
                for item_id in timeline_item_ids
            )
            or len(set(timeline_item_ids)) != len(timeline_item_ids)
        ):
            raise ValueError(
                "timeline_item_ids must contain 2 to 16 unique bounded IDs."
            )
        if not isinstance(linked, bool):
            raise ValueError("linked must be a boolean.")
        return self._resolve.set_clips_linked(
            timeline_id,
            timeline_item_ids,
            linked,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_set_clip_transform(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        position_x: float | None = None,
        position_y: float | None = None,
        zoom: float | None = None,
        rotation_degrees: float | None = None,
        opacity_percent: float | None = None,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Apply one bounded transform to a Resolve video timeline item."""
        for name, value in (
            ("timeline_id", timeline_id),
            ("timeline_item_id", timeline_item_id),
        ):
            if not value or len(value) > 128:
                raise ValueError(
                    f"{name} must contain 1 to 128 characters."
                )
        values = {
            "position_x": position_x,
            "position_y": position_y,
            "zoom": zoom,
            "rotation_degrees": rotation_degrees,
            "opacity_percent": opacity_percent,
        }
        if all(value is None for value in values.values()):
            raise ValueError("At least one transform value is required.")
        ranges = {
            "position_x": (-32_768.0, 32_768.0),
            "position_y": (-32_768.0, 32_768.0),
            "zoom": (0.0, 100.0),
            "rotation_degrees": (-360.0, 360.0),
            "opacity_percent": (0.0, 100.0),
        }
        for transform_name, transform_value in values.items():
            if transform_value is None:
                continue
            minimum, maximum = ranges[transform_name]
            if (
                isinstance(transform_value, bool)
                or not isinstance(transform_value, (int, float))
                or not math.isfinite(transform_value)
                or not minimum <= transform_value <= maximum
            ):
                raise ValueError(
                    f"{transform_name} must be a finite number from "
                    f"{minimum} to {maximum}."
                )
        return self._resolve.set_clip_transform(
            timeline_id,
            timeline_item_id,
            position_x=position_x,
            position_y=position_y,
            zoom=zoom,
            rotation_degrees=rotation_degrees,
            opacity_percent=opacity_percent,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_delete_clip(
        self,
        timeline_id: str,
        timeline_item_id: str,
        *,
        confirm_delete: bool = False,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Delete one timeline item only after explicit confirmation."""
        for field_name, value in (
            ("timeline_id", timeline_id),
            ("timeline_item_id", timeline_item_id),
        ):
            if not value or len(value) > 128:
                raise ValueError(
                    f"{field_name} must contain 1 to 128 characters."
                )
        if confirm_delete is not True:
            raise ValueError("confirm_delete must be true.")
        return self._resolve.delete_clip(
            timeline_id,
            timeline_item_id,
            confirm_delete=True,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_prepare_render_job(
        self,
        custom_name: str,
        *,
        timeline_id: str | None = None,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Prepare one fixed render job without starting rendering."""
        normalized_name = validate_render_name(custom_name)
        normalized_profile = validate_render_profile(profile)
        if timeline_id is not None and (
            not timeline_id or len(timeline_id) > 128
        ):
            raise ValueError("timeline_id must contain 1 to 128 characters.")
        return self._resolve.prepare_render_job(
            normalized_name,
            timeline_id=timeline_id,
            profile=normalized_profile.name,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_get_render_job_status(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return documented status for one Resolve render job."""
        normalized_job_id = validate_render_job_id(job_id)
        return self._resolve.render_job_status(
            normalized_job_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )

    def resolve_start_render_job(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Start one agent-prepared Resolve render job."""
        normalized_job_id = validate_render_job_id(job_id)
        return self._resolve.start_render_job(
            normalized_job_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
            idempotency_key=idempotency_key,
        )

    def resolve_verify_render_output(
        self,
        job_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify that one completed job produced a managed non-empty MP4."""
        normalized_job_id = validate_render_job_id(job_id)
        render_status = self._resolve.render_job_status(
            normalized_job_id,
            timeout_seconds=self._validated_timeout(timeout_seconds),
        )
        return verify_render_output(render_status)

    def create_rough_cut(
        self,
        *,
        screen_file: str,
        webcam_file: str,
        screen_audio_file: str,
        webcam_audio_file: str,
        speech_audio_file: str,
        timeline_name: str,
        max_sync_offset_ms: int = 30_000,
        pause_threshold_dbfs: float = -40.0,
        min_pause_duration_ms: int = 700,
        preserve_context_ms: int = 120,
    ) -> dict[str, Any]:
        """Validate sources and create an M5 draft without changing Resolve."""
        def execute() -> dict[str, Any]:
            policy = (
                MediaPolicy.from_local_config()
                if self._media_policy is None
                else self._media_policy
            )
            normalized = policy.validate_files(
                [
                    screen_file,
                    webcam_file,
                    screen_audio_file,
                    webcam_audio_file,
                    speech_audio_file,
                ]
            )
            planner = (
                RoughCutPlanner()
                if self._rough_cut_planner is None
                else self._rough_cut_planner
            )
            return planner.create_plan(
                screen_file=normalized[0],
                webcam_file=normalized[1],
                screen_audio_file=normalized[2],
                webcam_audio_file=normalized[3],
                speech_audio_file=normalized[4],
                timeline_name=timeline_name,
                max_sync_offset_ms=max_sync_offset_ms,
                pause_threshold_dbfs=pause_threshold_dbfs,
                min_pause_duration_ms=min_pause_duration_ms,
                preserve_context_ms=preserve_context_ms,
            )

        return self._run_local_workflow("create_rough_cut", execute)

    def approve_rough_cut(
        self,
        plan_id: str,
        *,
        confirm_review: bool,
    ) -> dict[str, Any]:
        """Record explicit review without applying the plan to Resolve."""
        def execute() -> dict[str, Any]:
            reviewer = (
                RoughCutReviewer()
                if self._rough_cut_reviewer is None
                else self._rough_cut_reviewer
            )
            return reviewer.approve(
                plan_id,
                confirm_review=confirm_review,
            )

        return self._run_local_workflow("approve_rough_cut", execute)

    def get_rough_cut_plan(self, plan_id: str) -> dict[str, Any]:
        """Return one validated draft and approval without changing either."""
        return self._run_local_workflow(
            "get_rough_cut_plan",
            lambda: (
                RoughCutInspector()
                if self._rough_cut_inspector is None
                else self._rough_cut_inspector
            ).get_plan(plan_id),
        )

    def list_rough_cut_plans(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded summaries for locally stored rough-cut plans."""
        return self._run_local_workflow(
            "list_rough_cut_plans",
            lambda: (
                RoughCutInspector()
                if self._rough_cut_inspector is None
                else self._rough_cut_inspector
            ).list_plans(limit),
        )

    def preview_rough_cut_apply(
        self,
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
    ) -> dict[str, Any]:
        """Preview M34 application without sending a Resolve command."""
        return self._run_local_workflow(
            "preview_rough_cut_apply",
            lambda: self._rough_cut_apply_service().preview(
                plan_id,
                source_timeline_id,
                target_timeline_name,
            ),
        )

    def apply_rough_cut(
        self,
        plan_id: str,
        source_timeline_id: str,
        target_timeline_name: str,
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply an approved and fully supported plan only to a timeline copy."""
        return self._run_local_workflow(
            "apply_rough_cut",
            lambda: self._rough_cut_apply_service().apply(
                plan_id,
                source_timeline_id,
                target_timeline_name,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def sync_screen_and_webcam(
        self,
        *,
        timeline_name: str,
        screen_asset_id: str,
        webcam_asset_id: str,
        webcam_offset_ms: int,
        confirm_sync: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create a timeline and place one synchronized screen/webcam pair."""
        return self._run_local_workflow(
            "sync_screen_and_webcam",
            lambda: self._synchronized_pair_service().assemble(
                timeline_name=timeline_name,
                screen_asset_id=screen_asset_id,
                webcam_asset_id=webcam_asset_id,
                webcam_offset_ms=webcam_offset_ms,
                confirm_sync=confirm_sync,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _synchronized_pair_service(self) -> SynchronizedPairWorkflow:
        if self._synchronized_pair_assembler is not None:
            return self._synchronized_pair_assembler
        return SynchronizedPairAssembler(
            gateway=self._resolve,
            capabilities=lambda: self.status()["bridge"].get(
                "capabilities", {}
            ),
        )

    def list_editing_recipes(self) -> dict[str, Any]:
        """List packaged declarative editing recipes without Resolve writes."""
        return self._run_local_workflow(
            "list_editing_recipes",
            self._editing_recipe_service().list_recipes,
        )

    def get_editing_recipe(self, recipe_id: str) -> dict[str, Any]:
        """Return one validated packaged editing recipe."""
        return self._run_local_workflow(
            "get_editing_recipe",
            lambda: self._editing_recipe_service().get_recipe(recipe_id),
        )

    def preview_editing_recipe(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
    ) -> dict[str, Any]:
        """Preview normalized inputs, steps and capability gates."""
        return self._run_local_workflow(
            "preview_editing_recipe",
            lambda: self._editing_recipe_service().preview(recipe_id, inputs),
        )

    def run_editing_recipe(
        self,
        recipe_id: str,
        inputs: dict[str, Any],
        *,
        confirm_execute: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Execute one exact packaged recipe with durable step replay."""
        return self._run_local_workflow(
            "run_editing_recipe",
            lambda: self._editing_recipe_service().run(
                recipe_id,
                inputs,
                confirm_execute=confirm_execute,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _editing_recipe_service(self) -> EditingRecipeWorkflow:
        if self._editing_recipe_runner is not None:
            return self._editing_recipe_runner

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return EditingRecipeRunner(actions=self, capabilities=capabilities)

    def preview_visual_treatment(
        self,
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Preview exact M49 title and static reframing operations."""
        return self._run_local_workflow(
            "preview_visual_treatment",
            lambda: self._visual_treatment_service().preview(
                timeline_id, transforms, titles
            ),
        )

    def apply_visual_treatment(
        self,
        timeline_id: str,
        transforms: list[dict[str, Any]],
        titles: list[dict[str, Any]],
        *,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply a confirmed M49 visual treatment with durable replay."""
        return self._run_local_workflow(
            "apply_visual_treatment",
            lambda: self._visual_treatment_service().apply(
                timeline_id,
                transforms,
                titles,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _visual_treatment_service(self) -> VisualTreatmentService:
        if self._visual_treatment is not None:
            return self._visual_treatment

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return VisualTreatmentWorkflow(
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def preview_baseline_edit(
        self,
        *,
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Run read-only M50 receipt and live timeline QA."""
        return self._run_local_workflow(
            "preview_baseline_edit",
            lambda: self._baseline_edit_service().preview(
                finalization_receipt_id=finalization_receipt_id,
                audio_integration_receipt_id=audio_integration_receipt_id,
                subtitle_receipt_id=subtitle_receipt_id,
                visual_treatment_receipt_id=visual_treatment_receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def start_baseline_render(
        self,
        *,
        finalization_receipt_id: str,
        audio_integration_receipt_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        confirm_render: bool,
        subtitle_receipt_id: str | None = None,
        visual_treatment_receipt_id: str | None = None,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start one deterministic render only after successful M50 QA."""
        return self._run_local_workflow(
            "start_baseline_render",
            lambda: self._baseline_edit_service().start(
                finalization_receipt_id=finalization_receipt_id,
                audio_integration_receipt_id=audio_integration_receipt_id,
                subtitle_receipt_id=subtitle_receipt_id,
                visual_treatment_receipt_id=visual_treatment_receipt_id,
                custom_name=custom_name,
                profile=profile,
                confirm_render=confirm_render,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_baseline_render_status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Re-run QA and inspect the exact M50 managed render."""
        return self._run_local_workflow(
            "get_baseline_render_status",
            lambda: self._baseline_edit_service().status(
                receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _baseline_edit_service(self) -> BaselineEditService:
        if self._baseline_edit is not None:
            return self._baseline_edit
        return BaselineEditWorkflow(
            gateway=self._resolve,
            render_preparer=self._finalized_render_preparation_service(),
            render_executor=self._finalized_render_execution_service(),
        )

    def preview_broll_plan(
        self,
        *,
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Build one read-only M51 B-roll review plan."""
        return self._run_local_workflow(
            "preview_broll_plan",
            lambda: self._broll_service().preview(
                baseline_edit_receipt_id=baseline_edit_receipt_id,
                target_timeline_name=target_timeline_name,
                placements=placements,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def apply_broll_plan(
        self,
        *,
        baseline_edit_receipt_id: str,
        target_timeline_name: str,
        placements: list[dict[str, Any]],
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one exact reviewed M51 plan to a duplicate timeline."""
        return self._run_local_workflow(
            "apply_broll_plan",
            lambda: self._broll_service().apply(
                baseline_edit_receipt_id=baseline_edit_receipt_id,
                target_timeline_name=target_timeline_name,
                placements=placements,
                expected_plan_id=expected_plan_id,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_broll_status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Read one M51 receipt and verify its live target items."""
        return self._run_local_workflow(
            "get_broll_status",
            lambda: self._broll_service().status(
                receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _broll_service(self) -> BrollService:
        if self._broll is not None:
            return self._broll
        return BrollWorkflow(
            gateway=self._resolve,
            baseline=self._baseline_edit_service(),
            capabilities=lambda: self.status()["bridge"].get("capabilities", {}),
        )

    def list_animation_templates(self) -> dict[str, Any]:
        """List immutable packaged M52 templates without Resolve access."""
        return self._run_local_workflow(
            "list_animation_templates",
            self._animation_template_service().list_templates,
        )

    def get_animation_template(self, template_id: str) -> dict[str, Any]:
        """Return one immutable packaged M52 template manifest."""
        return self._run_local_workflow(
            "get_animation_template",
            lambda: self._animation_template_service().get_template(template_id),
        )

    def preview_animation_template(
        self,
        *,
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Build one read-only M52 template application plan."""
        return self._run_local_workflow(
            "preview_animation_template",
            lambda: self._animation_template_service().preview(
                broll_receipt_id=broll_receipt_id,
                target_timeline_name=target_timeline_name,
                template_id=template_id,
                timecode=timecode,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def apply_animation_template(
        self,
        *,
        broll_receipt_id: str,
        target_timeline_name: str,
        template_id: str,
        timecode: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one exact reviewed M52 plan to a duplicate timeline."""
        return self._run_local_workflow(
            "apply_animation_template",
            lambda: self._animation_template_service().apply(
                broll_receipt_id=broll_receipt_id,
                target_timeline_name=target_timeline_name,
                template_id=template_id,
                timecode=timecode,
                expected_plan_id=expected_plan_id,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _animation_template_service(self) -> AnimationTemplateService:
        if self._animation_templates is not None:
            return self._animation_templates
        return AnimationTemplateWorkflow(
            gateway=self._resolve,
            broll=self._broll_service(),
            capabilities=lambda: self.status()["bridge"].get("capabilities", {}),
        )

    def list_color_presets(self) -> dict[str, Any]:
        """List immutable packaged M53 CDL presets without Resolve access."""
        return self._run_local_workflow(
            "list_color_presets",
            self._color_treatment_service().list_presets,
        )

    def get_color_preset(self, preset_id: str) -> dict[str, Any]:
        """Return one immutable packaged M53 CDL preset."""
        return self._run_local_workflow(
            "get_color_preset",
            lambda: self._color_treatment_service().get_preset(preset_id),
        )

    def preview_color_treatment(
        self,
        *,
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Build one read-only M53 color-treatment plan."""
        return self._run_local_workflow(
            "preview_color_treatment",
            lambda: self._color_treatment_service().preview(
                animation_receipt_id=animation_receipt_id,
                target_timeline_name=target_timeline_name,
                preset_id=preset_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def apply_color_treatment(
        self,
        *,
        animation_receipt_id: str,
        target_timeline_name: str,
        preset_id: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one exact reviewed M53 plan to a duplicate timeline."""
        return self._run_local_workflow(
            "apply_color_treatment",
            lambda: self._color_treatment_service().apply(
                animation_receipt_id=animation_receipt_id,
                target_timeline_name=target_timeline_name,
                preset_id=preset_id,
                expected_plan_id=expected_plan_id,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _color_treatment_service(self) -> ColorTreatmentService:
        if self._color_treatment is not None:
            return self._color_treatment
        return ColorTreatmentWorkflow(
            gateway=self._resolve,
            capabilities=lambda: self.status()["bridge"].get("capabilities", {}),
        )

    def analyze_take_candidates(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
    ) -> dict[str, Any]:
        """Analyze bounded local candidates and create a pending review."""
        return self._run_local_workflow(
            "analyze_take_candidates",
            lambda: self._take_selection_service().analyze(
                selection_name=selection_name,
                candidates=candidates,
            ),
        )

    def analyze_scripted_take_candidates(
        self,
        *,
        selection_name: str,
        candidates: list[dict[str, str | float]],
        reference_text: str,
    ) -> dict[str, Any]:
        """Rank bounded dialogue takes against one expected script."""
        return self._run_local_workflow(
            "analyze_scripted_take_candidates",
            lambda: self._take_selection_service().analyze_scripted(
                selection_name=selection_name,
                candidates=candidates,
                reference_text=reference_text,
            ),
        )

    def get_take_selection(self, selection_id: str) -> dict[str, Any]:
        """Return one stored M54 technical selection."""
        return self._run_local_workflow(
            "get_take_selection",
            lambda: self._take_selection_service().get(selection_id),
        )

    def list_take_selections(self, limit: int = 20) -> dict[str, Any]:
        """List bounded M54 technical selection summaries."""
        return self._run_local_workflow(
            "list_take_selections",
            lambda: self._take_selection_service().list(limit),
        )

    def review_take_selection(
        self,
        *,
        selection_id: str,
        decision: str,
        selected_candidate_id: str | None = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Approve or reject one recommendation without editing Resolve."""
        return self._run_local_workflow(
            "review_take_selection",
            lambda: self._take_selection_service().review(
                selection_id=selection_id,
                decision=decision,
                selected_candidate_id=selected_candidate_id,
                note=note,
            ),
        )

    def get_take_selection_review(self, selection_id: str) -> dict[str, Any]:
        """Return one immutable M54 human review record."""
        return self._run_local_workflow(
            "get_take_selection_review",
            lambda: self._take_selection_service().get_review(selection_id),
        )

    def compose_take_sequence(
        self,
        *,
        sequence_name: str,
        selection_ids: list[str],
    ) -> dict[str, Any]:
        """Compose an ordered handoff from approved take selections."""
        return self._run_local_workflow(
            "compose_take_sequence",
            lambda: self._take_sequence_service().compose(
                sequence_name=sequence_name,
                selection_ids=selection_ids,
            ),
        )

    def get_take_sequence(self, sequence_id: str) -> dict[str, Any]:
        """Return one persisted approved take sequence."""
        return self._run_local_workflow(
            "get_take_sequence",
            lambda: self._take_sequence_service().get(sequence_id),
        )

    def list_take_sequences(self, limit: int = 20) -> dict[str, Any]:
        """List bounded approved take-sequence summaries."""
        return self._run_local_workflow(
            "list_take_sequences",
            lambda: self._take_sequence_service().list(limit),
        )

    def bind_take_sequence_sources(
        self,
        *,
        sequence_id: str,
        sources: list[dict[str, str | int]],
    ) -> dict[str, Any]:
        """Bind approved sequence entries to exact allowlisted local files."""
        return self._run_local_workflow(
            "bind_take_sequence_sources",
            lambda: self._take_sequence_binding_service().bind(
                sequence_id=sequence_id,
                sources=sources,
            ),
        )

    def get_take_sequence_binding(self, binding_id: str) -> dict[str, Any]:
        """Return one redacted M55.1 local source-binding receipt."""
        return self._run_local_workflow(
            "get_take_sequence_binding",
            lambda: self._take_sequence_binding_service().get(binding_id),
        )

    def preview_take_sequence_assembly(
        self,
        *,
        binding_id: str,
        assembly_name: str,
    ) -> dict[str, Any]:
        """Calculate a path-redacted sequential M55.2 assembly preview."""
        return self._run_local_workflow(
            "preview_take_sequence_assembly",
            lambda: self._take_sequence_assembly_service().preview(
                binding_id=binding_id,
                assembly_name=assembly_name,
            ),
        )

    def preview_take_sequence_timeline_mapping(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Map one M55.2 assembly to verified live target timeline frames."""
        return self._run_local_workflow(
            "preview_take_sequence_timeline_mapping",
            lambda: self._take_sequence_timeline_mapping_service().preview(
                binding_id=binding_id,
                assembly_name=assembly_name,
                timeline_id=timeline_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def preview_take_sequence_media_import(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Plan conservative Media Pool imports without modifying Resolve."""
        return self._run_local_workflow(
            "preview_take_sequence_media_import",
            lambda: self._take_sequence_media_import_service().preview(
                binding_id=binding_id,
                assembly_name=assembly_name,
                timeline_id=timeline_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def apply_take_sequence_media_import(
        self,
        *,
        binding_id: str,
        assembly_name: str,
        timeline_id: str,
        expected_plan_id: str,
        confirm_import: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Import one exact reviewed M55.4 source batch and persist a receipt."""
        return self._run_local_workflow(
            "apply_take_sequence_media_import",
            lambda: self._take_sequence_media_import_apply_service().apply(
                binding_id=binding_id,
                assembly_name=assembly_name,
                timeline_id=timeline_id,
                expected_plan_id=expected_plan_id,
                confirm_import=confirm_import,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_take_sequence_media_import(self, receipt_id: str) -> dict[str, Any]:
        """Return one path-redacted M55.5 confirmed import receipt."""
        return self._run_local_workflow(
            "get_take_sequence_media_import",
            lambda: self._take_sequence_media_import_apply_service().get(receipt_id),
        )

    def preview_take_sequence_timeline_apply(
        self,
        *,
        media_import_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview exact M55.6 V1/A1 insertion into a duplicate timeline."""
        return self._run_local_workflow(
            "preview_take_sequence_timeline_apply",
            lambda: self._take_sequence_timeline_apply_service().preview(
                media_import_receipt_id=media_import_receipt_id,
                target_timeline_name=target_timeline_name,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def apply_take_sequence_timeline(
        self,
        *,
        media_import_receipt_id: str,
        target_timeline_name: str,
        expected_plan_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply one reviewed M55.6 plan only to a duplicate timeline."""
        return self._run_local_workflow(
            "apply_take_sequence_timeline",
            lambda: self._take_sequence_timeline_apply_service().apply(
                media_import_receipt_id=media_import_receipt_id,
                target_timeline_name=target_timeline_name,
                expected_plan_id=expected_plan_id,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_take_sequence_timeline_apply(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return one M55.6 receipt with fresh source/target readback."""
        return self._run_local_workflow(
            "get_take_sequence_timeline_apply",
            lambda: self._take_sequence_timeline_apply_service().get(
                receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def inspect_take_sequence_qc(
        self,
        timeline_apply_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Build an M55.7 structural QC report after fresh live readback."""
        return self._run_local_workflow(
            "inspect_take_sequence_qc",
            lambda: self._take_sequence_qc_service().inspect(
                timeline_apply_receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_take_sequence_qc(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return an M55.7 report after fresh live evidence verification."""
        return self._run_local_workflow(
            "get_take_sequence_qc",
            lambda: self._take_sequence_qc_service().get(
                report_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def review_take_sequence_qc(
        self,
        *,
        report_id: str,
        decision: str,
        note: str = "",
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Approve or reject one M55.7 report without modifying Resolve."""
        return self._run_local_workflow(
            "review_take_sequence_qc",
            lambda: self._take_sequence_qc_service().review(
                report_id=report_id,
                decision=decision,
                note=note,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_take_sequence_qc_review(
        self,
        report_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Return an immutable M55.7 review after live revalidation."""
        return self._run_local_workflow(
            "get_take_sequence_qc_review",
            lambda: self._take_sequence_qc_service().get_review(
                report_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def preview_take_sequence_render(
        self,
        *,
        qc_report_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview an M55.8 render for one human-approved QC report."""
        return self._run_local_workflow(
            "preview_take_sequence_render",
            lambda: self._take_sequence_render_service().preview(
                qc_report_id=qc_report_id,
                custom_name=custom_name,
                profile=profile,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def start_take_sequence_render(
        self,
        *,
        qc_report_id: str,
        custom_name: str,
        expected_plan_id: str,
        confirm_render: bool,
        profile: str = DEFAULT_RENDER_PROFILE,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare and start one exact M55.8 managed render."""
        return self._run_local_workflow(
            "start_take_sequence_render",
            lambda: self._take_sequence_render_service().start(
                qc_report_id=qc_report_id,
                custom_name=custom_name,
                profile=profile,
                expected_plan_id=expected_plan_id,
                confirm_render=confirm_render,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_take_sequence_render_status(
        self,
        receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Verify an M55.8 live job and its managed MP4 output."""
        return self._run_local_workflow(
            "get_take_sequence_render_status",
            lambda: self._take_sequence_render_service().status(
                receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _take_selection_service(self) -> TakeSelectionService:
        if self._take_selection is not None:
            return self._take_selection
        return TakeSelectionWorkflow(
            media_policy=(
                MediaPolicy.from_local_config()
                if self._media_policy is None
                else self._media_policy
            ),
            transcriber=FasterWhisperTranscriber(
                transcription_models_directory()
            ),
        )

    def _take_sequence_service(self) -> TakeSequenceService:
        if self._take_sequence is not None:
            return self._take_sequence
        return TakeSequenceWorkflow(self._take_selection_service())

    def _take_sequence_binding_service(self) -> TakeSequenceBindingService:
        if self._take_sequence_binding is not None:
            return self._take_sequence_binding
        policy = (
            MediaPolicy.from_local_config()
            if self._media_policy is None
            else self._media_policy
        )
        return TakeSequenceBindingWorkflow(
            self._take_sequence_service(),
            policy,
        )

    def _take_sequence_assembly_service(self) -> TakeSequenceAssemblyService:
        if self._take_sequence_assembly is not None:
            return self._take_sequence_assembly
        return TakeSequenceAssemblyWorkflow(
            self._take_sequence_binding_service(),
        )

    def _take_sequence_timeline_mapping_service(
        self,
    ) -> TakeSequenceTimelineMappingService:
        if self._take_sequence_timeline_mapping is not None:
            return self._take_sequence_timeline_mapping
        return TakeSequenceTimelineMappingWorkflow(
            self._take_sequence_assembly_service(),
            self._resolve,
        )

    def _take_sequence_media_import_service(
        self,
    ) -> TakeSequenceMediaImportService:
        if self._take_sequence_media_import is not None:
            return self._take_sequence_media_import
        return TakeSequenceMediaImportWorkflow(
            self._take_sequence_timeline_mapping_service(),
            self._resolve,
        )

    def _take_sequence_media_import_apply_service(
        self,
    ) -> TakeSequenceMediaImportApplyService:
        if self._take_sequence_media_import_apply is not None:
            return self._take_sequence_media_import_apply
        return TakeSequenceMediaImporter(
            self._take_sequence_media_import_service(),
            self._take_sequence_timeline_mapping_service(),
            self._take_sequence_binding_service(),
            self._resolve,
        )

    def _take_sequence_timeline_apply_service(
        self,
    ) -> TakeSequenceTimelineApplyService:
        if self._take_sequence_timeline_apply is not None:
            return self._take_sequence_timeline_apply

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return TakeSequenceTimelineApplyWorkflow(
            imports=self._take_sequence_media_import_apply_service(),
            mapping=self._take_sequence_timeline_mapping_service(),
            bindings=self._take_sequence_binding_service(),
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def _take_sequence_qc_service(self) -> TakeSequenceQcService:
        if self._take_sequence_qc is not None:
            return self._take_sequence_qc
        return TakeSequenceQcWorkflow(
            self._take_sequence_timeline_apply_service()
        )

    def _take_sequence_render_service(self) -> TakeSequenceRenderService:
        if self._take_sequence_render is not None:
            return self._take_sequence_render

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return TakeSequenceRenderWorkflow(
            qc=self._take_sequence_qc_service(),
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def compose_webcam_picture_in_picture(
        self,
        *,
        synchronized_pair_receipt_id: str,
        size_percent: float = 25.0,
        center_x_percent: float = 82.0,
        center_y_percent: float = 82.0,
        confirm_layout: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Apply a normalized layout to the webcam item created by M38."""
        return self._run_local_workflow(
            "compose_webcam_picture_in_picture",
            lambda: self._picture_in_picture_service().compose(
                synchronized_pair_receipt_id=synchronized_pair_receipt_id,
                size_percent=size_percent,
                center_x_percent=center_x_percent,
                center_y_percent=center_y_percent,
                confirm_layout=confirm_layout,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _picture_in_picture_service(self) -> PictureInPictureWorkflow:
        if self._picture_in_picture_composer is not None:
            return self._picture_in_picture_composer
        return PictureInPictureComposer(
            gateway=self._resolve,
            capabilities=lambda: self.status()["bridge"].get(
                "capabilities", {}
            ),
        )

    def link_synchronized_screen_pair(
        self,
        *,
        synchronized_pair_receipt_id: str,
        confirm_link: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Link the screen video/audio items created by M38."""
        return self._run_local_workflow(
            "link_synchronized_screen_pair",
            lambda: self._synchronized_link_service().link(
                synchronized_pair_receipt_id=synchronized_pair_receipt_id,
                confirm_link=confirm_link,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _synchronized_link_service(self) -> SynchronizedLinkWorkflow:
        if self._synchronized_screen_linker is not None:
            return self._synchronized_screen_linker
        return SynchronizedScreenLinker(
            gateway=self._resolve,
            capabilities=lambda: self.status()["bridge"].get(
                "capabilities", {}
            ),
        )

    def preview_synchronized_pause_compaction(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Preview rebuilding a synchronized timeline from kept ranges."""
        return self._run_local_workflow(
            "preview_synchronized_pause_compaction",
            lambda: self._pause_compaction_service().preview(
                plan_id=plan_id,
                synchronized_pair_receipt_id=synchronized_pair_receipt_id,
                target_timeline_name=target_timeline_name,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _pause_compaction_service(self) -> PauseCompactionWorkflow:
        if self._pause_compaction_previewer is not None:
            return self._pause_compaction_previewer
        return PauseCompactionPreviewer(
            gateway=self._resolve,
            capabilities=self.status()["bridge"].get("capabilities", {}),
            inspector=(
                None
                if self._rough_cut_inspector is None
                else self._rough_cut_inspector
            ),
        )

    def apply_synchronized_pause_compaction(
        self,
        *,
        plan_id: str,
        synchronized_pair_receipt_id: str,
        target_timeline_name: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Create a new compacted synchronized timeline after confirmation."""
        return self._run_local_workflow(
            "apply_synchronized_pause_compaction",
            lambda: self._pause_compaction_apply_service().apply(
                plan_id=plan_id,
                synchronized_pair_receipt_id=synchronized_pair_receipt_id,
                target_timeline_name=target_timeline_name,
                confirm_apply=confirm_apply,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _pause_compaction_apply_service(self) -> PauseCompactionApplyWorkflow:
        if self._pause_compaction_applier is not None:
            return self._pause_compaction_applier
        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        previewer = PauseCompactionPreviewer(
            gateway=self._resolve,
            capabilities=capabilities(),
            inspector=(
                None
                if self._rough_cut_inspector is None
                else self._rough_cut_inspector
            ),
        )
        return PauseCompactionApplier(
            gateway=self._resolve,
            capabilities=capabilities,
            previewer=previewer,
        )

    def finalize_synchronized_pause_compaction(
        self,
        *,
        pause_compaction_receipt_id: str,
        picture_in_picture_receipt_id: str,
        synchronized_link_receipt_id: str,
        confirm_finalize: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Propagate approved link and layout state to compacted segments."""
        return self._run_local_workflow(
            "finalize_synchronized_pause_compaction",
            lambda: self._pause_compaction_finalization_service().finalize(
                pause_compaction_receipt_id=pause_compaction_receipt_id,
                picture_in_picture_receipt_id=picture_in_picture_receipt_id,
                synchronized_link_receipt_id=synchronized_link_receipt_id,
                confirm_finalize=confirm_finalize,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _pause_compaction_finalization_service(
        self,
    ) -> PauseCompactionFinalizationWorkflow:
        if self._pause_compaction_finalizer is not None:
            return self._pause_compaction_finalizer

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return PauseCompactionFinalizer(
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def prepare_finalized_timeline_render(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        profile: str = DEFAULT_RENDER_PROFILE,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare a fixed render job for one applied M43 timeline."""
        return self._run_local_workflow(
            "prepare_finalized_timeline_render",
            lambda: self._finalized_render_preparation_service().prepare(
                finalization_receipt_id=finalization_receipt_id,
                custom_name=custom_name,
                profile=profile,
                confirm_prepare=confirm_prepare,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _finalized_render_preparation_service(
        self,
    ) -> FinalizedRenderPreparationWorkflow:
        if self._finalized_render_preparer is not None:
            return self._finalized_render_preparer

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return FinalizedRenderPreparer(
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def start_finalized_timeline_render(
        self,
        *,
        preparation_receipt_id: str,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start exactly one render job prepared by M44."""
        return self._run_local_workflow(
            "start_finalized_timeline_render",
            lambda: self._finalized_render_execution_service().start(
                preparation_receipt_id=preparation_receipt_id,
                confirm_render=confirm_render,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_finalized_timeline_render_status(
        self,
        execution_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect one M45 render and verify its managed MP4 output."""
        return self._run_local_workflow(
            "get_finalized_timeline_render_status",
            lambda: self._finalized_render_execution_service().status(
                execution_receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _finalized_render_execution_service(
        self,
    ) -> FinalizedRenderExecutionWorkflow:
        if self._finalized_render_executor is not None:
            return self._finalized_render_executor

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return FinalizedRenderExecutor(
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def prepare_finalized_timeline_audio(
        self,
        *,
        finalization_receipt_id: str,
        custom_name: str,
        confirm_prepare: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Prepare one fixed Audio Only job for an applied M43 timeline."""
        return self._run_local_workflow(
            "prepare_finalized_timeline_audio",
            lambda: self._finalized_audio_extraction_service().prepare(
                finalization_receipt_id=finalization_receipt_id,
                custom_name=custom_name,
                confirm_prepare=confirm_prepare,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def start_finalized_timeline_audio(
        self,
        extraction_receipt_id: str,
        *,
        confirm_render: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Start one exact M46 Audio Only job."""
        return self._run_local_workflow(
            "start_finalized_timeline_audio",
            lambda: self._finalized_audio_extraction_service().start(
                extraction_receipt_id,
                confirm_render=confirm_render,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def get_finalized_timeline_audio_status(
        self,
        extraction_receipt_id: str,
        *,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Inspect one M46 audio job and verify its managed PCM WAV."""
        return self._run_local_workflow(
            "get_finalized_timeline_audio_status",
            lambda: self._finalized_audio_extraction_service().status(
                extraction_receipt_id,
                timeout_seconds=self._validated_timeout(timeout_seconds),
            ),
        )

    def _finalized_audio_extraction_service(
        self,
    ) -> FinalizedAudioExtractionWorkflow:
        if self._finalized_audio_extractor is not None:
            return self._finalized_audio_extractor

        def capabilities() -> dict[str, Any]:
            discovered = self.status()["bridge"].get("capabilities", {})
            return discovered if isinstance(discovered, dict) else {}

        return FinalizedAudioExtractor(
            gateway=self._resolve,
            capabilities=capabilities,
        )

    def apply_finalized_timeline_audio(
        self,
        *,
        extraction_receipt_id: str,
        audio_report_id: str,
        confirm_apply: bool,
        timeout_seconds: float = 30,
    ) -> dict[str, Any]:
        """Insert validated M46 audio on A2 and disable its source A1 items."""
        timeout = self._validated_timeout(timeout_seconds)
        return self._run_local_workflow(
            "apply_finalized_timeline_audio",
            lambda: self._finalized_audio_integration_service().apply(
                extraction_receipt_id=extraction_receipt_id,
                audio_report_id=audio_report_id,
                confirm_apply=confirm_apply,
                timeout_seconds=timeout,
            ),
        )

    def _finalized_audio_integration_service(
        self,
    ) -> FinalizedAudioIntegrationWorkflow:
        if self._finalized_audio_integrator is not None:
            return self._finalized_audio_integrator
        return FinalizedAudioIntegrator(
            gateway=self._resolve,
            extraction_status=(
                lambda receipt_id, *, timeout_seconds: (
                    self._finalized_audio_extraction_service().status(
                        receipt_id,
                        timeout_seconds=timeout_seconds,
                    )
                )
            ),
        )

    def _rough_cut_apply_service(self) -> RoughCutPlanApplier:
        if self._rough_cut_applier is not None:
            return self._rough_cut_applier
        return RoughCutApplier(
            capabilities=lambda: self.status()["bridge"].get("capabilities", {}),
            duplicate_timeline=lambda source_id, name, key, timeout_seconds: (
                self.resolve_duplicate_timeline(
                    source_id,
                    name,
                    timeout_seconds=timeout_seconds,
                    idempotency_key=key,
                )
            ),
        )

    def clean_dialogue_audio(
        self,
        source_file: str,
        *,
        preset: str = "pcm-dialogue-level-v1",
    ) -> dict[str, Any]:
        """Create a derived WAV and report without calling Resolve."""
        def execute() -> dict[str, Any]:
            policy = (
                MediaPolicy.from_local_config()
                if self._media_policy is None
                else self._media_policy
            )
            normalized = policy.validate_files([source_file])
            processor = (
                DialogueAudioWorkflow()
                if self._audio_processor is None
                else self._audio_processor
            )
            return processor.process(normalized[0], preset=preset)

        return self._run_local_workflow("clean_dialogue_audio", execute)

    def get_audio_report(self, report_id: str) -> dict[str, Any]:
        """Return one validated report without processing media."""
        return self._run_local_workflow(
            "get_audio_report",
            lambda: (
                AudioReportInspector()
                if self._audio_report_inspector is None
                else self._audio_report_inspector
            ).get_report(report_id),
        )

    def list_audio_reports(self, limit: int = 100) -> dict[str, Any]:
        """Return bounded summaries for locally stored audio reports."""
        return self._run_local_workflow(
            "list_audio_reports",
            lambda: (
                AudioReportInspector()
                if self._audio_report_inspector is None
                else self._audio_report_inspector
            ).list_reports(limit),
        )

    def _run_local_workflow(
        self,
        operation: str,
        callback: Callable[[], ApplicationResultT],
    ) -> ApplicationResultT:
        if self._workflow_audit is None:
            return callback()
        return self._workflow_audit.run(operation, callback)

    @staticmethod
    def _validated_timeout(timeout_seconds: float) -> float:
        if not 0 < timeout_seconds <= MAX_COMMAND_TIMEOUT_SECONDS:
            raise ValueError(
                "timeout_seconds must be greater than zero and no more than 300."
            )
        return timeout_seconds
