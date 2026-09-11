"""Audio observation tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import (
    AudioInspectionRequest,
    AudioInspectionResponse,
    MasterAudioBindingResponse,
    MasterAudioBindRequest,
    MusicAnalysisRequest,
    MusicAnalysisResponse,
    ProductionDocument,
    ProductionDocumentVersion,
    TimedTextLockResponse,
    TimedTextLockUpdateRequest,
)
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry
from short_drama.subtitle_alignment import NativeSubtitleAlignmentRequest, NativeSubtitleAlignmentResponse
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)


class AudioToolService(Protocol):
    def align_current_native_subtitles(
        self, project_id: str, request: NativeSubtitleAlignmentRequest,
    ) -> NativeSubtitleAlignmentResponse: ...

    def bind_master_audio(
        self,
        project_id: str,
        request: MasterAudioBindRequest,
    ) -> MasterAudioBindingResponse: ...

    def update_timed_text_lock(
        self,
        project_id: str,
        request: TimedTextLockUpdateRequest,
    ) -> TimedTextLockResponse: ...

    def analyze_music(
        self,
        project_id: str,
        request: MusicAnalysisRequest,
    ) -> MusicAnalysisResponse: ...

    def inspect_current_manifest_audio(
        self,
        project_id: str,
        request: AudioInspectionRequest,
    ) -> AudioInspectionResponse: ...

    def update_character_voice_reference(
        self,
        project_id: str,
        character_id: str,
        request: VoiceReferenceUpdateRequest,
    ) -> VoiceReferenceMutationResponse: ...


class AudioInspectManifestInput(ToolModel):
    project_id: str
    request: AudioInspectionRequest


class AudioAlignNativeSubtitlesInput(ToolModel):
    project_id: str
    request: NativeSubtitleAlignmentRequest


class AudioVoiceReferenceUpdateInput(ToolModel):
    project_id: str
    character_id: str
    request: VoiceReferenceUpdateRequest


class AudioBindMasterInput(ToolModel):
    project_id: str
    request: MasterAudioBindRequest


class AudioTimedTextLockUpdateInput(ToolModel):
    project_id: str
    request: TimedTextLockUpdateRequest


class AudioAnalyzeMusicInput(ToolModel):
    project_id: str
    request: MusicAnalysisRequest


def _production_document_refs(
    project_id: str,
    document: ProductionDocument,
    version: ProductionDocumentVersion,
) -> tuple[ArtifactRef, ArtifactRef]:
    return (
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_document",
            artifact_id=document.id,
            version=version.version,
        ),
        ArtifactRef(
            project_id=project_id,
            artifact_type="production_document_version",
            artifact_id=version.id,
            version=version.version,
        ),
    )


def register_tools(registry: ToolRegistry, service: AudioToolService) -> None:
    def align_subtitles(request: AudioAlignNativeSubtitlesInput) -> ToolExecution[NativeSubtitleAlignmentResponse]:
        result = service.align_current_native_subtitles(request.project_id, request.request)
        return ToolExecution(output=result, artifact_refs=(ArtifactRef(
            project_id=request.project_id, artifact_type="timeline_manifest",
            artifact_id=result.manifest.id, version=result.manifest.version,
        ), ArtifactRef.model_validate(result.alignment_document_ref.model_dump()),
           ArtifactRef.model_validate(result.alignment_version_ref.model_dump())), warnings=tuple(result.warnings))

    registry.register(ToolSpec(
        id="audio.align_subtitles", version=1, category=ToolCategory.AUDIO,
        description=(
            "Align existing script subtitles to native Take audio using local ASR token timestamps. "
            "Creates a new TimelineManifest with corrected cue times and an observation report; "
            "video/audio windows and subtitle text stay unchanged. Review ASR differences and warnings. "
            "The versioned alignment document contains complete cue changes, ASR observations and media invariants; "
            "read it with artifact.get_production_document when handing off to another Worker. "
            "Use the returned manifest for checks and export with rebuild_manifest=false."
        ),
        effect=ToolEffect.EXTERNAL, idempotent=False,
        input_model=AudioAlignNativeSubtitlesInput, output_model=NativeSubtitleAlignmentResponse,
        handler=align_subtitles, tags=frozenset({"audio", "subtitles", "timing", "timeline"}),
    ))

    def bind_master(
        request: AudioBindMasterInput,
    ) -> ToolExecution[MasterAudioBindingResponse]:
        result = service.bind_master_audio(request.project_id, request.request)
        source_asset_ref = next(
            item
            for item in result.version.source_artifact_refs
            if item.artifact_type == "asset_version"
            and item.artifact_id == result.binding.asset_version_id
        )
        if source_asset_ref.version is None:
            raise ValueError("Master Audio source AssetVersion is not version-pinned")
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset_version",
                    artifact_id=result.binding.asset_version_id,
                    version=source_asset_ref.version,
                ),
                *_production_document_refs(
                    request.project_id,
                    result.document,
                    result.version,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="audio.bind_master",
            version=1,
            category=ToolCategory.AUDIO,
            description=(
                "Bind a selected window from one current usable audio asset as "
                "the project's versioned Master Audio."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AudioBindMasterInput,
            output_model=MasterAudioBindingResponse,
            handler=bind_master,
            tags=frozenset({"audio", "master-audio", "timeline", "handoff"}),
        )
    )

    def update_timed_text(
        request: AudioTimedTextLockUpdateInput,
    ) -> ToolExecution[TimedTextLockResponse]:
        result = service.update_timed_text_lock(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="audio.update_timed_text_lock",
            version=1,
            category=ToolCategory.AUDIO,
            description=(
                "Create or revise exact timed text anchored to the current Master Audio."
            ),
            effect=ToolEffect.WRITE,
            idempotent=False,
            input_model=AudioTimedTextLockUpdateInput,
            output_model=TimedTextLockResponse,
            handler=update_timed_text,
            tags=frozenset({"audio", "timed-text", "lyrics", "handoff"}),
        )
    )

    def analyze_music(
        request: AudioAnalyzeMusicInput,
    ) -> ToolExecution[MusicAnalysisResponse]:
        result = service.analyze_music(request.project_id, request.request)
        return ToolExecution(
            output=result,
            artifact_refs=_production_document_refs(
                request.project_id,
                result.document,
                result.version,
            ),
        )

    registry.register(
        ToolSpec(
            id="audio.analyze_music",
            version=1,
            category=ToolCategory.AUDIO,
            description=(
                "Measure the current Master Audio with the configured local analysis "
                "service and persist a versioned Audio Structure Map."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=AudioAnalyzeMusicInput,
            output_model=MusicAnalysisResponse,
            handler=analyze_music,
            tags=frozenset({"audio", "music-analysis", "structure", "handoff"}),
        )
    )

    def inspect(
        request: AudioInspectManifestInput,
    ) -> ToolExecution[AudioInspectionResponse]:
        result = service.inspect_current_manifest_audio(
            request.project_id,
            request.request,
        )
        return ToolExecution(
            output=result,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="audio_inspection",
                    artifact_id=result.report.id,
                ),
            ),
        )

    registry.register(
        ToolSpec(
            id="audio.inspect_manifest",
            version=1,
            category=ToolCategory.AUDIO,
            description=(
                "Inspect audio and subtitles from the saved Studio editorial timeline, including "
                "current trims, timing, mute, volume and speed, and persist a report tied to its "
                "editorial revision. Does not rebuild or replace edits. Uses the current legacy "
                "TimelineManifest only when no Studio edit has been saved. The Studio report's "
                "manifest_id is an inspection snapshot; saved Studio subtitle changes use "
                "timeline.read_editorial/save_editorial, not legacy manifest alignment."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=AudioInspectManifestInput,
            output_model=AudioInspectionResponse,
            handler=inspect,
            tags=frozenset({"audio", "inspection", "timeline"}),
        )
    )

    def update_voice_reference(
        request: AudioVoiceReferenceUpdateInput,
    ) -> ToolExecution[VoiceReferenceMutationResponse]:
        result = service.update_character_voice_reference(
            request.project_id,
            request.character_id,
            request.request,
        )
        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="character",
                artifact_id=request.character_id,
            )
        ]
        if result.asset is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset",
                    artifact_id=result.asset.id,
                )
            )
        if result.version is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="asset_version",
                    artifact_id=result.version.id,
                    version=result.version.version,
                )
            )
        return ToolExecution(output=result, artifact_refs=tuple(refs))

    registry.register(
        ToolSpec(
            id="audio.update_voice_reference",
            version=1,
            category=ToolCategory.AUDIO,
            description="Bind or clear the selected usable audio asset for one character.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=AudioVoiceReferenceUpdateInput,
            output_model=VoiceReferenceMutationResponse,
            handler=update_voice_reference,
            tags=frozenset({"audio", "character", "voice-reference"}),
        )
    )


__all__ = [
    "AudioAnalyzeMusicInput",
    "AudioBindMasterInput",
    "AudioInspectManifestInput",
    "AudioTimedTextLockUpdateInput",
    "AudioVoiceReferenceUpdateInput",
    "register_tools",
]
