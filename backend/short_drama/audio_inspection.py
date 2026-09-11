"""Provider-neutral audio, dialogue, subtitle, and voice-continuity inspection tools."""

from __future__ import annotations

import math
import subprocess
import unicodedata
import wave
from dataclasses import dataclass
from functools import lru_cache
from itertools import combinations
from pathlib import Path
from statistics import median
from typing import Any, Protocol, cast, runtime_checkable
from uuid import uuid4

import imageio_ffmpeg
import numpy as np

from short_drama.media import MediaGenerationError
from short_drama.models import (
    AudioClipInspection,
    AudioContinuityAssessment,
    AudioInspectionReport,
    AudioInspectionRequest,
    AudioSignalMetrics,
    AudioTimeRange,
    DialogueAudioCheck,
    ProjectDocument,
    QaFailureCategory,
    QaIssue,
    ScriptLine,
    SemanticReviewStatus,
    SpeakerVoiceContinuityCheck,
    SubtitleCue,
    TechnicalCheck,
    TimelineAudioClip,
    TimelineManifest,
)


class AudioSemanticError(RuntimeError):
    """Raised when a configured transcription or speaker model cannot run."""


@dataclass(frozen=True)
class AudioSemanticResult:
    transcript_text: str
    voice_embedding: tuple[float, ...] | None = None
    detected_language: str | None = None
    confidence: float | None = None


@dataclass(frozen=True)
class TimedAudioCharacter:
    text: str
    start_seconds: float
    end_seconds: float


@dataclass(frozen=True)
class TimedAudioTranscript:
    transcript_text: str
    characters: tuple[TimedAudioCharacter, ...]


@runtime_checkable
class AudioTimingService(Protocol):
    def transcribe_timed(self, *, audio_path: Path, language: str) -> TimedAudioTranscript: ...


@dataclass(frozen=True)
class _SpeakerBinding:
    """Explicit script-to-Bible identity used only for acoustic aggregation."""

    key: str
    display_name: str
    voice_identity: str | None


class AudioSemanticService(Protocol):
    provider: str
    transcription_model: str
    speaker_model: str | None

    def analyze(
        self,
        *,
        audio_path: Path,
        language: str,
        include_speaker_embedding: bool,
    ) -> AudioSemanticResult:
        """Transcribe one dialogue window and optionally return a speaker embedding."""

        ...


class TransformersAudioSemanticService:
    """Lazy local Whisper + WavLM implementation of the provider-neutral interface."""

    provider = "local-transformers"

    def __init__(
        self,
        *,
        transcription_model: str = "openai/whisper-small",
        speaker_model: str | None = "microsoft/wavlm-base-plus-sv",
        device: str = "auto",
    ) -> None:
        self.transcription_model = transcription_model
        self.speaker_model = speaker_model
        self._requested_device = device
        self._resolved_device: str | None = None
        self._transcription_processor: Any | None = None
        self._transcription_network: Any | None = None
        self._speaker_processor: Any | None = None
        self._speaker_network: Any | None = None

    def analyze(
        self,
        *,
        audio_path: Path,
        language: str,
        include_speaker_embedding: bool,
    ) -> AudioSemanticResult:
        audio, sample_rate = _read_pcm_wav(audio_path)
        if sample_rate != 16000:
            raise AudioSemanticError(
                f"Expected 16 kHz semantic input, got {sample_rate} Hz"
            )
        if audio.size == 0 or _rms_dbfs(audio) <= -65.0:
            return AudioSemanticResult(transcript_text="")
        try:
            transcript = self._transcribe(audio, language)
            embedding = (
                self._speaker_embedding(audio)
                if include_speaker_embedding
                else None
            )
        except Exception as exc:  # model backends expose several runtime exception types
            raise AudioSemanticError(str(exc)) from exc
        return AudioSemanticResult(
            transcript_text=transcript.strip(),
            voice_embedding=embedding,
            detected_language=language,
        )

    def _device(self) -> str:
        if self._resolved_device is not None:
            return self._resolved_device
        if self._requested_device != "auto":
            self._resolved_device = self._requested_device
            return self._resolved_device
        import torch

        self._resolved_device = (
            "mps" if torch.backends.mps.is_available() else "cpu"
        )
        return self._resolved_device

    def _load_transcriber(self) -> tuple[Any, Any]:
        if self._transcription_processor is not None and self._transcription_network is not None:
            return self._transcription_processor, self._transcription_network
        from transformers import AutoProcessor, WhisperForConditionalGeneration

        processor = cast(
            Any,
            AutoProcessor.from_pretrained(  # pyright: ignore[reportUnknownMemberType]
                self.transcription_model
            ),
        )
        network = cast(
            Any,
            WhisperForConditionalGeneration.from_pretrained(  # pyright: ignore[reportUnknownMemberType]
                self.transcription_model
            ),
        )
        network.to(self._device())
        network.eval()
        self._transcription_processor = processor
        self._transcription_network = network
        return processor, network

    def _load_speaker_model(self) -> tuple[Any, Any] | None:
        if self.speaker_model is None:
            return None
        if self._speaker_processor is not None and self._speaker_network is not None:
            return self._speaker_processor, self._speaker_network
        from transformers import AutoFeatureExtractor, WavLMForXVector

        processor = cast(
            Any,
            AutoFeatureExtractor.from_pretrained(  # pyright: ignore[reportUnknownMemberType]
                self.speaker_model
            ),
        )
        network = cast(
            Any,
            WavLMForXVector.from_pretrained(  # pyright: ignore[reportUnknownMemberType]
                self.speaker_model
            ),
        )
        network.to(self._device())
        network.eval()
        self._speaker_processor = processor
        self._speaker_network = network
        return processor, network

    def _transcribe(self, audio: np.ndarray, language: str) -> str:
        import torch

        processor, network = self._load_transcriber()
        inputs = processor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
            return_attention_mask=True,
        )
        input_features = inputs.input_features.to(self._device())
        attention_mask = inputs.attention_mask.to(self._device())
        whisper_language = "zh" if language.lower().startswith("zh") else language
        with torch.inference_mode():
            generated = network.generate(
                input_features,
                attention_mask=attention_mask,
                language=whisper_language,
                task="transcribe",
                max_new_tokens=256,
            )
        decoded = processor.batch_decode(generated, skip_special_tokens=True)
        return str(decoded[0]) if decoded else ""

    def transcribe_timed(self, *, audio_path: Path, language: str) -> TimedAudioTranscript:
        """Use Whisper attention timestamps, which retain leading silence offsets.

        Predicted segment timestamp tokens can reset after a long silent opening;
        they are not used as the subtitle clock. Keep the original ASR text and
        normalized character positions for a separate script alignment step.
        """
        import torch

        audio, sample_rate = _read_pcm_wav(audio_path)
        if sample_rate != 16000 or audio.size == 0:
            raise AudioSemanticError("Timed transcription requires non-empty 16 kHz audio")
        processor, network = self._load_transcriber()
        inputs = processor(audio, sampling_rate=16000, return_tensors="pt", return_attention_mask=True)
        with torch.inference_mode():
            generated = network.generate(
                inputs.input_features.to(self._device()), attention_mask=inputs.attention_mask.to(self._device()),
                language="zh" if language.lower().startswith("zh") else language, task="transcribe",
                return_timestamps=True, return_token_timestamps=True, return_dict_in_generate=True,
                # Reserve decoder positions for Whisper's language/task prefix,
                # matching the existing short native-audio transcription path.
                max_new_tokens=256,
            )
        token_ids = cast(list[int], generated["sequences"][0].tolist())
        timestamps = cast(list[float], generated["token_timestamps"][0].tolist())
        timestamp_begin = int(processor.tokenizer.convert_tokens_to_ids("<|0.00|>"))
        special_ids = set(cast(list[int], processor.tokenizer.all_special_ids))
        characters: list[TimedAudioCharacter] = []
        pending: list[int] = []
        pending_start = 0.0
        duration = audio.size / sample_rate
        for index, token_id in enumerate(token_ids):
            if token_id in special_ids or token_id >= timestamp_begin:
                continue
            if not pending:
                pending_start = timestamps[index]
            pending.append(token_id)
            text = str(processor.decode(pending, skip_special_tokens=True))
            if "\ufffd" in text:
                continue
            normalized = normalize_dialogue_text(text)
            end = min(duration, timestamps[index + 1] if index + 1 < len(timestamps) else duration)
            end = max(pending_start, end)
            for char_index, character in enumerate(normalized):
                characters.append(TimedAudioCharacter(
                    text=character,
                    start_seconds=pending_start + (end - pending_start) * char_index / len(normalized),
                    end_seconds=pending_start + (end - pending_start) * (char_index + 1) / len(normalized),
                ))
            pending.clear()
        return TimedAudioTranscript(
            transcript_text=str(processor.batch_decode(generated["sequences"], skip_special_tokens=True)[0]),
            characters=tuple(characters),
        )

    def _speaker_embedding(self, audio: np.ndarray) -> tuple[float, ...] | None:
        loaded = self._load_speaker_model()
        if loaded is None or audio.size < 8000:
            return None
        import torch

        processor, network = loaded
        inputs = processor(
            audio,
            sampling_rate=16000,
            return_tensors="pt",
            padding=True,
        )
        tensor_inputs = {
            key: value.to(self._device())
            for key, value in inputs.items()
        }
        with torch.inference_mode():
            output = network(**tensor_inputs)
        embedding = output.embeddings[0].detach().float().cpu().numpy()
        norm = float(np.linalg.norm(embedding))
        if norm <= 1e-8:
            return None
        normalized = embedding / norm
        return tuple(float(value) for value in normalized.tolist())


class AudioInspector:
    """Extracts reusable audio evidence and optionally invokes a semantic service."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    def inspect_manifest(
        self,
        *,
        document: ProjectDocument,
        manifest: TimelineManifest,
        request: AudioInspectionRequest,
        semantic_service: AudioSemanticService | None,
        created_at: str,
    ) -> AudioInspectionReport:
        report_id = f"audio-inspection-{uuid4().hex}"
        report_dir = self.workspace_root / document.project.id / "inspections" / report_id
        clip_dir = report_dir / "clips"
        dialogue_dir = report_dir / "dialogue"
        clip_dir.mkdir(parents=True, exist_ok=True)
        dialogue_dir.mkdir(parents=True, exist_ok=True)

        script = next(
            (
                version
                for version in document.script_versions
                if version.id == manifest.script_version_id
            ),
            None,
        )
        if script is None and any(cue.script_line_id for cue in manifest.subtitle_cues):
            raise MediaGenerationError(
                f"Manifest script version is missing: {manifest.script_version_id}"
            )
        line_by_id = {line.id: line for line in script.lines} if script else {}
        clip_inspections: list[AudioClipInspection] = []
        issues: list[QaIssue] = []
        warnings: list[str] = []
        metrics_by_clip_id: dict[str, AudioSignalMetrics] = {}

        for sequence, clip in enumerate(
            sorted(manifest.audio_clips, key=lambda item: item.start_seconds),
            start=1,
        ):
            clip_label = _clip_shot_id(clip)
            inspected = self._inspect_clip(
                clip=clip,
                destination=clip_dir / f"{sequence:02d}-{clip_label}.wav",
                expects_dialogue=any(
                    cue.script_line_id in line_by_id
                    and cue.start_seconds < clip.start_seconds + clip.duration_seconds
                    and cue.end_seconds > clip.start_seconds
                    for cue in manifest.subtitle_cues
                ),
            )
            clip_inspections.append(inspected)
            issues.extend(inspected.issues)
            if inspected.metrics is not None:
                metrics_by_clip_id[clip.id] = inspected.metrics

        dialogue_checks: list[DialogueAudioCheck] = []
        embeddings: dict[str, list[tuple[str, tuple[float, ...]]]] = {}
        speaker_bindings: dict[str, _SpeakerBinding] = {}
        semantic_failures = 0
        semantic_attempts = 0
        unmapped_cues = 0

        for sequence, cue in enumerate(
            sorted(manifest.subtitle_cues, key=lambda item: item.start_seconds),
            start=1,
        ):
            if cue.script_line_id is None:
                clip = _clip_for_cue(
                    manifest.audio_clips,
                    cue.start_seconds,
                    cue.end_seconds,
                )
                if clip is None:
                    unmapped_cues += 1
                    issues.append(
                        QaIssue(
                            code="AUDIO_TIMED_TEXT_OUTSIDE_CLIP",
                            severity="HIGH",
                            message=(
                                f"Timed-text cue {cue.id} is not covered by a timeline "
                                "audio clip."
                            ),
                            repair_layer="TIMELINE_SUBTITLE",
                            category=QaFailureCategory.DIALOGUE_AUDIO,
                        )
                    )
                continue
            line = line_by_id.get(cue.script_line_id)
            if line is None:
                unmapped_cues += 1
                issue = QaIssue(
                    code="AUDIO_UNKNOWN_SCRIPT_LINE",
                    severity="HIGH",
                    message=(
                        f"Subtitle cue {cue.id} references unknown line {cue.script_line_id}."
                    ),
                    repair_layer="SCRIPT_TIMELINE",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
                issues.append(issue)
                continue
            speaker_binding = _speaker_binding(document, line)
            speaker_bindings[line.id] = speaker_binding
            clip = _clip_for_cue(manifest.audio_clips, cue.start_seconds, cue.end_seconds)
            if clip is None:
                unmapped_cues += 1
                issue = QaIssue(
                    code="AUDIO_CUE_OUTSIDE_CLIP",
                    severity="HIGH",
                    message=f"Subtitle cue {cue.id} is not covered by a timeline audio clip.",
                    repair_layer="TIMELINE_SUBTITLE",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
                issues.append(issue)
                dialogue_checks.append(
                    _unavailable_dialogue_check(
                        cue=cue,
                        line=line,
                        shot_id="UNKNOWN",
                        take_id="UNKNOWN",
                        status=SemanticReviewStatus.UNAVAILABLE,
                        issue=issue,
                    )
                )
                continue
            check, embedding, failure = self._inspect_dialogue(
                document=document,
                line=line,
                cue=cue,
                clip=clip,
                destination=dialogue_dir / f"{sequence:02d}-{line.id}.wav",
                request=request,
                semantic_service=semantic_service,
                clip_metrics=metrics_by_clip_id.get(clip.id),
            )
            dialogue_checks.append(check)
            issues.extend(check.issues)
            if request.semantic_review:
                semantic_attempts += 1
                semantic_failures += int(failure)
            if embedding is not None:
                embeddings.setdefault(speaker_binding.key, []).append((line.id, embedding))

        speaker_checks, speaker_issues = _speaker_continuity_checks(
            dialogue_checks=dialogue_checks,
            embeddings=embeddings,
            speaker_bindings=speaker_bindings,
            enabled=request.speaker_continuity,
        )
        issues.extend(speaker_issues)

        if not request.semantic_review:
            semantic_status = SemanticReviewStatus.NOT_REQUESTED
        elif semantic_service is None:
            semantic_status = SemanticReviewStatus.UNAVAILABLE
            warnings.append(
                "No audio semantic service is configured; signal, subtitle, and duration checks "
                "completed, while transcript and voice identity remain UNKNOWN."
            )
        elif semantic_attempts > 0 and semantic_failures == semantic_attempts:
            semantic_status = SemanticReviewStatus.FAILED
            warnings.append(
                "Every dialogue semantic check failed; technical audio evidence remains usable."
            )
        elif semantic_failures > 0:
            semantic_status = SemanticReviewStatus.FAILED
            warnings.append(
                f"{semantic_failures}/{semantic_attempts} dialogue semantic checks failed."
            )
        else:
            semantic_status = SemanticReviewStatus.COMPLETE

        technical_checks = _manifest_technical_checks(
            manifest=manifest,
            clip_inspections=clip_inspections,
            dialogue_checks=dialogue_checks,
            unmapped_cues=unmapped_cues,
        )
        failed_technical = sum(not item.passed for item in technical_checks)
        exact_dialogue = sum(item.exact_match is True for item in dialogue_checks)
        reviewed_voice = sum(
            item.assessment == AudioContinuityAssessment.REVIEW
            for item in speaker_checks
        )
        summary = (
            f"Inspected {len(clip_inspections)} timeline audio clips and "
            f"{len(dialogue_checks)} locked dialogue cues; "
            f"technical failures={failed_technical}, exact transcripts="
            f"{exact_dialogue}/{len(dialogue_checks)}, voice-continuity reviews="
            f"{reviewed_voice}. Findings are evidence for the Director Agent and remain "
            "user-overridable."
        )
        return AudioInspectionReport(
            id=report_id,
            project_id=document.project.id,
            manifest_id=manifest.id,
            source_script_version_id=script.id if script else manifest.script_version_id,
            semantic_status=semantic_status,
            semantic_provider=(
                semantic_service.provider if semantic_service is not None else None
            ),
            transcription_model=(
                semantic_service.transcription_model
                if semantic_service is not None
                else None
            ),
            speaker_model=(
                semantic_service.speaker_model
                if semantic_service is not None
                else None
            ),
            clip_inspections=clip_inspections,
            dialogue_checks=dialogue_checks,
            speaker_checks=speaker_checks,
            technical_checks=technical_checks,
            issues=issues,
            summary=summary,
            warnings=warnings,
            created_at=created_at,
        )

    def _inspect_clip(
        self,
        *,
        clip: TimelineAudioClip,
        destination: Path,
        expects_dialogue: bool,
    ) -> AudioClipInspection:
        checks: list[TechnicalCheck] = []
        issues: list[QaIssue] = []
        try:
            extract_audio_window(
                source=Path(clip.media_path).expanduser().resolve(),
                destination=destination,
                start_seconds=clip.source_in_seconds,
                duration_seconds=clip.duration_seconds,
            )
            metrics = measure_audio_signal(destination)
        except MediaGenerationError as exc:
            issue = QaIssue(
                code="AUDIO_STREAM_UNAVAILABLE",
                severity="HIGH",
                message=f"{clip.shot_id} audio could not be extracted: {exc}",
                repair_layer="PROVIDER_RETRY",
                category=QaFailureCategory.DIALOGUE_AUDIO,
            )
            checks.append(
                TechnicalCheck(name="audio-decodable", passed=False, detail=str(exc))
            )
            issues.append(issue)
            return AudioClipInspection(
                clip_id=clip.id,
                shot_id=_clip_shot_id(clip),
                take_id=_clip_take_id(clip),
                media_path=clip.media_path,
                timeline_start_seconds=clip.start_seconds,
                expected_duration_seconds=clip.duration_seconds,
                technical_checks=checks,
                issues=issues,
            )

        checks.append(
            TechnicalCheck(
                name="audio-decodable",
                passed=True,
                detail=(
                    f"{metrics.duration_seconds:.3f}s, {metrics.sample_rate_hz} Hz, "
                    f"{metrics.channels} channel(s)"
                ),
            )
        )
        tolerance = max(0.18, clip.duration_seconds * 0.04)
        duration_ok = abs(metrics.duration_seconds - clip.duration_seconds) <= tolerance
        checks.append(
            TechnicalCheck(
                name="audio-duration-match",
                passed=duration_ok,
                detail=(
                    f"manifest={clip.duration_seconds:.3f}s, decoded="
                    f"{metrics.duration_seconds:.3f}s, tolerance={tolerance:.3f}s"
                ),
            )
        )
        if not duration_ok:
            issues.append(
                QaIssue(
                    code="AUDIO_DURATION_MISMATCH",
                    severity="MEDIUM",
                    message=(
                        f"{clip.shot_id} decoded audio duration differs from its timeline clip."
                    ),
                    repair_layer="TIMELINE_AUDIO",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        clipping_ok = metrics.clipped_sample_ratio <= 0.001
        checks.append(
            TechnicalCheck(
                name="audio-clipping",
                passed=clipping_ok,
                detail=f"clipped sample ratio={metrics.clipped_sample_ratio:.6f}",
            )
        )
        if not clipping_ok:
            issues.append(
                QaIssue(
                    code="AUDIO_CLIPPING",
                    severity="HIGH",
                    message=f"{clip.shot_id} contains a material amount of clipped audio.",
                    repair_layer="AUDIO_MIX",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        silence_ok = not expects_dialogue or metrics.silent_window_ratio < 0.92
        checks.append(
            TechnicalCheck(
                name="dialogue-clip-not-silent",
                passed=silence_ok,
                detail=f"silent window ratio={metrics.silent_window_ratio:.3f}",
            )
        )
        if not silence_ok:
            issues.append(
                QaIssue(
                    code="DIALOGUE_AUDIO_MOSTLY_SILENT",
                    severity="HIGH",
                    message=f"{clip.shot_id} is expected to contain dialogue but is mostly silent.",
                    repair_layer="DIRECTOR_PROMPT_OR_AUDIO_REPLACEMENT",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        return AudioClipInspection(
            clip_id=clip.id,
            shot_id=_clip_shot_id(clip),
            take_id=_clip_take_id(clip),
            media_path=clip.media_path,
            timeline_start_seconds=clip.start_seconds,
            expected_duration_seconds=clip.duration_seconds,
            extracted_audio_path=str(destination.resolve()),
            metrics=metrics,
            technical_checks=checks,
            issues=issues,
        )

    def _inspect_dialogue(
        self,
        *,
        document: ProjectDocument,
        line: ScriptLine,
        cue: SubtitleCue,
        clip: TimelineAudioClip,
        destination: Path,
        request: AudioInspectionRequest,
        semantic_service: AudioSemanticService | None,
        clip_metrics: AudioSignalMetrics | None,
    ) -> tuple[DialogueAudioCheck, tuple[float, ...] | None, bool]:
        issues: list[QaIssue] = []
        local_start = max(
            0.0,
            cue.start_seconds - clip.start_seconds - request.dialogue_padding_seconds,
        )
        local_end = min(
            clip.duration_seconds,
            cue.end_seconds - clip.start_seconds + request.dialogue_padding_seconds,
        )
        duration = max(0.05, local_end - local_start)
        try:
            extract_audio_window(
                source=Path(clip.media_path).expanduser().resolve(),
                destination=destination,
                start_seconds=clip.source_in_seconds + local_start,
                duration_seconds=duration,
            )
            segment_metrics = measure_audio_signal(destination)
        except MediaGenerationError as exc:
            issue = QaIssue(
                code="DIALOGUE_WINDOW_UNAVAILABLE",
                severity="HIGH",
                message=f"Could not extract dialogue line {line.id}: {exc}",
                repair_layer="TIMELINE_AUDIO",
                category=QaFailureCategory.DIALOGUE_AUDIO,
            )
            issues.append(issue)
            return (
                _unavailable_dialogue_check(
                    cue=cue,
                    line=line,
                    shot_id=_clip_shot_id(clip),
                    take_id=_clip_take_id(clip),
                    status=SemanticReviewStatus.FAILED,
                    issue=issue,
                ),
                None,
                True,
            )

        cue_duration = max(0.001, cue.end_seconds - cue.start_seconds)
        normalized_expected = normalize_dialogue_text(line.spoken_text)
        characters_per_second = len(normalized_expected) / cue_duration
        if characters_per_second > 12.0:
            issues.append(
                QaIssue(
                    code="SUBTITLE_READING_SPEED_HIGH",
                    severity="MEDIUM",
                    message=(
                        f"{line.id} subtitle requires {characters_per_second:.1f} "
                        "characters/second."
                    ),
                    repair_layer="TIMELINE_SUBTITLE",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        if cue.speaker != line.speaker:
            issues.append(
                QaIssue(
                    code="SUBTITLE_SPEAKER_MISMATCH",
                    severity="HIGH",
                    message=(
                        f"Cue {cue.id} names speaker '{cue.speaker}', while the locked script "
                        f"names '{line.speaker}'."
                    ),
                    repair_layer="SCRIPT_TIMELINE",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        speech_coverage = _speech_coverage(segment_metrics)
        if speech_coverage < 0.12:
            issues.append(
                QaIssue(
                    code="DIALOGUE_WINDOW_MOSTLY_SILENT",
                    severity="HIGH",
                    message=f"{line.id} subtitle window contains little detected speech energy.",
                    repair_layer="DIRECTOR_PROMPT_OR_AUDIO_REPLACEMENT",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )

        status = SemanticReviewStatus.NOT_REQUESTED
        result: AudioSemanticResult | None = None
        semantic_failed = False
        if request.semantic_review:
            if semantic_service is None:
                status = SemanticReviewStatus.UNAVAILABLE
            else:
                try:
                    result = semantic_service.analyze(
                        audio_path=destination,
                        language=document.project.language,
                        include_speaker_embedding=request.speaker_continuity,
                    )
                    status = SemanticReviewStatus.COMPLETE
                except AudioSemanticError as exc:
                    status = SemanticReviewStatus.FAILED
                    semantic_failed = True
                    issues.append(
                        QaIssue(
                            code="AUDIO_SEMANTIC_REVIEW_FAILED",
                            severity="LOW",
                            message=f"{line.id} semantic audio analysis failed: {exc}",
                            repair_layer="AUDIO_QA_RETRY",
                            category=QaFailureCategory.DIALOGUE_AUDIO,
                        )
                    )

        transcript = result.transcript_text if result is not None else None
        normalized_transcript = (
            normalize_dialogue_text(transcript) if transcript is not None else None
        )
        exact_match: bool | None = None
        character_error_rate: float | None = None
        if normalized_transcript is not None:
            exact_match = normalized_transcript == normalized_expected
            character_error_rate = _character_error_rate(
                normalized_expected,
                normalized_transcript,
            )
            if not exact_match:
                issues.append(
                    QaIssue(
                        code="DIALOGUE_TRANSCRIPT_MISMATCH",
                        severity="UNKNOWN",
                        message=(
                            f"{line.id} 的转写与锁定台词不同；语义影响尚未判断。"
                            "请导演 Agent 结合以下原始证据判断含义和成片后果："
                            f"script_line_id={line.id!r}, cue_id={cue.id!r}, "
                            f"shot_id={clip.shot_id!r}, take_id={clip.take_id!r}, "
                            f"speaker_character_id={line.speaker_character_id!r}, "
                            f"speaker_display={line.speaker!r}, "
                            f"expected_text={line.spoken_text!r}, "
                            f"transcript_text={transcript!r}, "
                            f"character_error_rate={character_error_rate:.3f}."
                        ),
                        repair_layer="DIRECTOR_SEMANTIC_REVIEW",
                        category=QaFailureCategory.DIALOGUE_AUDIO,
                    )
                )
        if clip_metrics is None:
            issues.append(
                QaIssue(
                    code="AUDIO_CLIP_METRICS_UNAVAILABLE",
                    severity="LOW",
                    message=f"Full-clip metrics are unavailable for {line.id}.",
                    repair_layer="AUDIO_QA_RETRY",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        return (
            DialogueAudioCheck(
                cue_id=cue.id,
                script_line_id=line.id,
                shot_id=_clip_shot_id(clip),
                take_id=_clip_take_id(clip),
                expected_speaker=line.speaker,
                expected_text=line.spoken_text,
                cue_start_seconds=cue.start_seconds,
                cue_end_seconds=cue.end_seconds,
                extracted_audio_path=str(destination.resolve()),
                transcription_status=status,
                transcript_text=transcript,
                normalized_expected=normalized_expected,
                normalized_transcript=normalized_transcript,
                exact_match=exact_match,
                character_error_rate=character_error_rate,
                speech_coverage_ratio=speech_coverage,
                provider=(semantic_service.provider if semantic_service is not None else None),
                model=(
                    semantic_service.transcription_model
                    if semantic_service is not None
                    else None
                ),
                confidence=result.confidence if result is not None else None,
                issues=issues,
            ),
            result.voice_embedding if result is not None else None,
            semantic_failed,
        )


def _run_ffmpeg(args: list[str]) -> None:
    result = subprocess.run(
        [
            imageio_ffmpeg.get_ffmpeg_exe(),
            "-hide_banner",
            "-loglevel",
            "error",
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or result.stdout.strip() or "unknown ffmpeg failure"
        raise MediaGenerationError(detail)


def extract_audio_window(
    *,
    source: Path,
    destination: Path,
    start_seconds: float,
    duration_seconds: float,
) -> None:
    if not source.is_file() or source.stat().st_size == 0:
        raise MediaGenerationError(f"Media does not exist or is empty: {source}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run_ffmpeg(
        [
            "-y",
            "-i",
            str(source),
            "-ss",
            f"{start_seconds:.6f}",
            "-t",
            f"{duration_seconds:.6f}",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(destination),
        ]
    )
    if not destination.is_file() or destination.stat().st_size <= 44:
        raise MediaGenerationError(f"Audio extraction produced no PCM data: {source}")


def _read_pcm_wav(path: Path) -> tuple[np.ndarray, int]:
    try:
        with wave.open(str(path), "rb") as source:
            channels = source.getnchannels()
            sample_width = source.getsampwidth()
            sample_rate = source.getframerate()
            frames = source.readframes(source.getnframes())
    except (wave.Error, OSError) as exc:
        raise MediaGenerationError(f"Could not read PCM WAV {path}: {exc}") from exc
    if sample_width != 2:
        raise MediaGenerationError(f"Expected PCM s16le, got {sample_width * 8}-bit audio")
    raw = np.frombuffer(frames, dtype="<i2").astype(np.float32)
    if channels > 1 and raw.size:
        raw = raw.reshape((-1, channels)).mean(axis=1)
    return raw / 32768.0, sample_rate


def measure_audio_signal(path: Path) -> AudioSignalMetrics:
    audio, sample_rate = _read_pcm_wav(path)
    duration = audio.size / sample_rate
    if audio.size == 0:
        return AudioSignalMetrics(
            duration_seconds=0.0,
            sample_rate_hz=sample_rate,
            channels=1,
            peak_dbfs=-120.0,
            rms_dbfs=-120.0,
            clipped_sample_ratio=0.0,
            silent_window_ratio=1.0,
            leading_silence_seconds=0.0,
            trailing_silence_seconds=0.0,
        )
    peak = float(np.max(np.abs(audio)))
    clipped = float(np.mean(np.abs(audio) >= 0.999))
    frame_size = max(1, round(sample_rate * 0.02))
    frame_count = math.ceil(audio.size / frame_size)
    frame_db: list[float] = []
    for index in range(frame_count):
        frame = audio[index * frame_size : (index + 1) * frame_size]
        frame_db.append(_rms_dbfs(frame))
    silent_ratio = sum(value <= -45.0 for value in frame_db) / len(frame_db)
    upper_quartile = float(np.percentile(np.asarray(frame_db), 75))
    speech_threshold = max(-42.0, min(-24.0, upper_quartile - 12.0))
    speech_mask = [value > speech_threshold for value in frame_db]
    intervals = _mask_to_intervals(
        speech_mask,
        frame_seconds=frame_size / sample_rate,
        total_duration=duration,
    )
    leading = intervals[0].start_seconds if intervals else duration
    trailing = duration - intervals[-1].end_seconds if intervals else duration
    return AudioSignalMetrics(
        duration_seconds=round(duration, 6),
        sample_rate_hz=sample_rate,
        channels=1,
        peak_dbfs=round(_dbfs(peak), 3),
        rms_dbfs=round(_rms_dbfs(audio), 3),
        clipped_sample_ratio=round(clipped, 8),
        silent_window_ratio=round(silent_ratio, 6),
        leading_silence_seconds=round(max(0.0, leading), 3),
        trailing_silence_seconds=round(max(0.0, trailing), 3),
        speech_intervals=intervals,
    )


def _mask_to_intervals(
    mask: list[bool],
    *,
    frame_seconds: float,
    total_duration: float,
) -> list[AudioTimeRange]:
    raw: list[tuple[int, int]] = []
    start: int | None = None
    for index, active in enumerate([*mask, False]):
        if active and start is None:
            start = index
        elif not active and start is not None:
            raw.append((start, index))
            start = None
    merged: list[tuple[int, int]] = []
    max_gap = max(1, round(0.12 / frame_seconds))
    for begin, end in raw:
        if merged and begin - merged[-1][1] <= max_gap:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((begin, end))
    result: list[AudioTimeRange] = []
    for begin, end in merged:
        start_seconds = begin * frame_seconds
        end_seconds = min(total_duration, end * frame_seconds)
        if end_seconds - start_seconds < 0.12:
            continue
        result.append(
            AudioTimeRange(
                start_seconds=round(start_seconds, 3),
                end_seconds=round(end_seconds, 3),
            )
        )
    return result


def _speech_coverage(metrics: AudioSignalMetrics) -> float:
    if metrics.duration_seconds <= 0:
        return 0.0
    voiced = sum(
        item.end_seconds - item.start_seconds for item in metrics.speech_intervals
    )
    return round(min(1.0, max(0.0, voiced / metrics.duration_seconds)), 6)


def _dbfs(amplitude: float) -> float:
    if amplitude <= 1e-6:
        return -120.0
    return 20.0 * math.log10(amplitude)


def _rms_dbfs(audio: np.ndarray) -> float:
    if audio.size == 0:
        return -120.0
    rms = float(math.sqrt(float(np.mean(np.square(audio, dtype=np.float64)))))
    return _dbfs(rms)


@lru_cache(maxsize=1)
def _traditional_to_simplified_converter() -> Any:
    from opencc import OpenCC  # pyright: ignore[reportMissingTypeStubs]

    return cast(Any, OpenCC)("t2s")


def normalize_dialogue_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    simplified = str(_traditional_to_simplified_converter().convert(normalized))
    return "".join(character for character in simplified if character.isalnum())


def _character_error_rate(expected: str, actual: str) -> float:
    if not expected:
        return 0.0 if not actual else float(len(actual))
    previous = list(range(len(actual) + 1))
    for expected_index, expected_character in enumerate(expected, start=1):
        current = [expected_index]
        for actual_index, actual_character in enumerate(actual, start=1):
            current.append(
                min(
                    current[-1] + 1,
                    previous[actual_index] + 1,
                    previous[actual_index - 1]
                    + int(expected_character != actual_character),
                )
            )
        previous = current
    return previous[-1] / len(expected)


def continuous_audio_groups(clips: list[TimelineAudioClip]) -> list[TimelineAudioClip]:
    """Join adjacent windows of the same source for speech that crosses a cut."""
    groups: list[TimelineAudioClip] = []
    for clip in sorted(clips, key=lambda item: item.start_seconds):
        previous = groups[-1] if groups else None
        if (previous is not None and previous.media_path == clip.media_path
            and previous.take_id == clip.take_id and previous.asset_version_id == clip.asset_version_id
            and previous.role == clip.role and previous.gain_db == clip.gain_db
            and abs(previous.start_seconds + previous.duration_seconds - clip.start_seconds) < 0.001
            and abs(previous.source_in_seconds + previous.duration_seconds - clip.source_in_seconds) < 0.001):
            duration = previous.duration_seconds + clip.duration_seconds
            groups[-1] = previous.model_copy(update={
                "duration_seconds": duration,
                "source_out_seconds": previous.source_in_seconds + duration,
            })
        else:
            groups.append(clip.model_copy(deep=True))
    return groups


def _clip_for_cue(
    clips: list[TimelineAudioClip],
    cue_start: float,
    cue_end: float,
) -> TimelineAudioClip | None:
    epsilon = 0.05
    matches = [
        clip
        for clip in clips
        if cue_start >= clip.start_seconds - epsilon
        and cue_end <= clip.start_seconds + clip.duration_seconds + epsilon
    ]
    if not matches:
        matches = [
            clip for clip in continuous_audio_groups(clips)
            if cue_start >= clip.start_seconds - epsilon
            and cue_end <= clip.start_seconds + clip.duration_seconds + epsilon
        ]
    if not matches:
        return None
    return min(matches, key=lambda item: item.duration_seconds)


def _clip_shot_id(clip: TimelineAudioClip) -> str:
    return clip.shot_id or "MASTER_AUDIO"


def _clip_take_id(clip: TimelineAudioClip) -> str:
    return clip.take_id or clip.asset_version_id or "MASTER_AUDIO"


def _speaker_binding(
    document: ProjectDocument,
    line: ScriptLine,
) -> _SpeakerBinding:
    if line.speaker_character_id is None:
        return _SpeakerBinding(
            key=f"script-line:{line.id}",
            display_name=line.speaker,
            voice_identity=None,
        )
    bible = next(
        (
            item
            for item in document.bible_versions
            if item.id == document.project.current_bible_version_id
        ),
        document.bible_versions[-1],
    )
    character = next(
        (
            item
            for item in bible.characters
            if item.id == line.speaker_character_id
        ),
        None,
    )
    return _SpeakerBinding(
        key=line.speaker_character_id,
        display_name=(character.name if character is not None else line.speaker),
        voice_identity=(
            character.voice_identity if character is not None else None
        ),
    )


def _cosine(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    return max(-1.0, min(1.0, sum(a * b for a, b in zip(left, right, strict=True))))


def _speaker_continuity_checks(
    *,
    dialogue_checks: list[DialogueAudioCheck],
    embeddings: dict[str, list[tuple[str, tuple[float, ...]]]],
    speaker_bindings: dict[str, _SpeakerBinding],
    enabled: bool,
) -> tuple[list[SpeakerVoiceContinuityCheck], list[QaIssue]]:
    lines_by_speaker: dict[str, list[str]] = {}
    binding_by_key: dict[str, _SpeakerBinding] = {}
    for check in dialogue_checks:
        binding = speaker_bindings.get(
            check.script_line_id,
            _SpeakerBinding(
                key=f"script-line:{check.script_line_id}",
                display_name=check.expected_speaker,
                voice_identity=None,
            ),
        )
        binding_by_key[binding.key] = binding
        lines_by_speaker.setdefault(binding.key, []).append(check.script_line_id)
    all_embeddings = [
        (speaker, line_id, embedding)
        for speaker, items in embeddings.items()
        for line_id, embedding in items
    ]
    results: list[SpeakerVoiceContinuityCheck] = []
    issues: list[QaIssue] = []
    for speaker_key, line_ids in lines_by_speaker.items():
        binding = binding_by_key[speaker_key]
        samples = embeddings.get(speaker_key, [])
        same = [
            _cosine(left[1], right[1])
            for left, right in combinations(samples, 2)
        ]
        other = [
            _cosine(embedding, other_embedding)
            for _line_id, embedding in samples
            for other_speaker, _other_line_id, other_embedding in all_embeddings
            if other_speaker != speaker_key
        ]
        minimum = min(same) if same else None
        middle = median(same) if same else None
        maximum_other = max(other) if other else None
        if not enabled or len(line_ids) < 2 or len(samples) < 2:
            assessment = AudioContinuityAssessment.UNKNOWN
            detail = (
                "At least two semantic voice samples for this expected speaker are required "
                "for cross-shot continuity assessment."
            )
        elif minimum is not None and (
            minimum < 0.55
            or (
                maximum_other is not None
                and minimum <= maximum_other + 0.03
            )
        ):
            assessment = AudioContinuityAssessment.REVIEW
            detail = (
                "Same-speaker acoustic similarity is weak or not clearly separated from "
                "other expected speakers. This is a diagnostic, not biometric identity proof."
            )
            issues.append(
                QaIssue(
                    code="VOICE_CONTINUITY_REVIEW",
                    severity="MEDIUM",
                    message=(
                        f"Expected speaker '{binding.display_name}' has inconsistent "
                        "cross-shot voice evidence."
                    ),
                    repair_layer="DIRECTOR_ROUTE_OR_REFERENCE_AUDIO",
                    category=QaFailureCategory.DIALOGUE_AUDIO,
                )
            )
        else:
            assessment = AudioContinuityAssessment.PASSED
            detail = (
                "Repeated samples for this expected speaker are acoustically consistent and "
                "separated from the other sampled speakers."
            )
        results.append(
            SpeakerVoiceContinuityCheck(
                speaker=binding.display_name,
                voice_identity=binding.voice_identity,
                script_line_ids=list(dict.fromkeys(line_ids)),
                comparison_count=len(same),
                minimum_same_speaker_similarity=(
                    round(minimum, 6) if minimum is not None else None
                ),
                median_same_speaker_similarity=(
                    round(float(middle), 6) if middle is not None else None
                ),
                maximum_other_speaker_similarity=(
                    round(maximum_other, 6) if maximum_other is not None else None
                ),
                assessment=assessment,
                detail=detail,
            )
        )
    return results, issues


def _manifest_technical_checks(
    *,
    manifest: TimelineManifest,
    clip_inspections: list[AudioClipInspection],
    dialogue_checks: list[DialogueAudioCheck],
    unmapped_cues: int,
) -> list[TechnicalCheck]:
    all_decodable = bool(clip_inspections) and all(
        item.metrics is not None for item in clip_inspections
    )
    bounds_ok = all(
        0.0 <= cue.start_seconds <= cue.end_seconds <= manifest.duration_seconds + 0.05
        for cue in manifest.subtitle_cues
    )
    ordered = sorted(manifest.subtitle_cues, key=lambda item: item.start_seconds)
    overlap_count = sum(
        current.start_seconds < previous.end_seconds - 0.02
        for previous, current in zip(ordered, ordered[1:])
    )
    return [
        TechnicalCheck(
            name="timeline-audio-decodable",
            passed=all_decodable,
            detail=f"decoded {sum(item.metrics is not None for item in clip_inspections)}/{len(clip_inspections)} clips",
        ),
        TechnicalCheck(
            name="subtitle-cues-inside-manifest",
            passed=bounds_ok,
            detail=f"{len(manifest.subtitle_cues)} cue(s), manifest={manifest.duration_seconds:.3f}s",
        ),
        TechnicalCheck(
            name="subtitle-cues-map-to-audio",
            passed=unmapped_cues == 0,
            detail=f"unmapped cues={unmapped_cues}",
        ),
        TechnicalCheck(
            name="subtitle-cues-non-overlapping",
            passed=overlap_count == 0,
            detail=f"overlaps={overlap_count}",
        ),
    ]


def _unavailable_dialogue_check(
    *,
    cue: SubtitleCue,
    line: ScriptLine,
    shot_id: str,
    take_id: str,
    status: SemanticReviewStatus,
    issue: QaIssue,
) -> DialogueAudioCheck:
    return DialogueAudioCheck(
        cue_id=cue.id,
        script_line_id=line.id,
        shot_id=shot_id,
        take_id=take_id,
        expected_speaker=line.speaker,
        expected_text=line.spoken_text,
        cue_start_seconds=cue.start_seconds,
        cue_end_seconds=cue.end_seconds,
        transcription_status=status,
        normalized_expected=normalize_dialogue_text(line.spoken_text),
        issues=[issue],
    )
