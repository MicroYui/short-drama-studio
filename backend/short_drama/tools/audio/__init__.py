"""Audio observation tool adapters."""

from __future__ import annotations

from typing import Protocol

from short_drama.models import AudioInspectionRequest, AudioInspectionResponse
from short_drama.tools.contracts import (
    ArtifactRef,
    ToolCategory,
    ToolEffect,
    ToolExecution,
    ToolModel,
    ToolSpec,
)
from short_drama.tools.registry import ToolRegistry
from short_drama.voice_models import (
    VoiceReferenceMutationResponse,
    VoiceReferenceUpdateRequest,
)


class AudioToolService(Protocol):
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


class AudioVoiceReferenceUpdateInput(ToolModel):
    project_id: str
    character_id: str
    request: VoiceReferenceUpdateRequest


def register_tools(registry: ToolRegistry, service: AudioToolService) -> None:
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
            description="Inspect the current timeline audio and persist its report.",
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
            description="Bind or clear an existing approved audio asset for one character.",
            effect=ToolEffect.WRITE,
            idempotent=True,
            input_model=AudioVoiceReferenceUpdateInput,
            output_model=VoiceReferenceMutationResponse,
            handler=update_voice_reference,
            tags=frozenset({"audio", "character", "voice-reference"}),
        )
    )


__all__ = [
    "AudioInspectManifestInput",
    "AudioVoiceReferenceUpdateInput",
    "register_tools",
]
