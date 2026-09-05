"""Persisted content-planning tool adapters."""

from __future__ import annotations

from typing import Protocol

from pydantic import Field

from short_drama.models import (
    PlanningRunResponse,
    ProductionDocumentArtifactRef,
    ScriptRevisionOrigin,
    StoryVerificationResolutionDecision,
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


class PlanningToolService(Protocol):
    def get_planning_run(self, run_id: str) -> PlanningRunResponse: ...

    def advance_content_planning_job(
        self,
        job_id: str,
        *,
        owner_agent_run_id: str,
    ) -> PlanningRunResponse: ...

    def start_script_revision(
        self,
        project_id: str,
        *,
        owner_agent_run_id: str,
        instruction: str,
        base_script_version_id: str,
        revision_origin: ScriptRevisionOrigin = ScriptRevisionOrigin.USER_FEEDBACK,
        evidence_refs: list[ProductionDocumentArtifactRef] | None = None,
    ) -> PlanningRunResponse: ...

    def resolve_story_verification(
        self,
        project_id: str,
        *,
        run_id: str,
        owner_agent_run_id: str,
        decision: StoryVerificationResolutionDecision,
        rationale: str,
    ) -> PlanningRunResponse: ...


class PlanningAdvanceInput(ToolModel):
    project_id: str
    run_id: str
    owner_agent_run_id: str


class PlanningStartScriptRevisionInput(ToolModel):
    project_id: str
    owner_agent_run_id: str
    instruction: str
    base_script_version_id: str
    revision_origin: ScriptRevisionOrigin = ScriptRevisionOrigin.USER_FEEDBACK
    evidence_refs: list[ProductionDocumentArtifactRef] = Field(
        default_factory=list[ProductionDocumentArtifactRef]
    )


class PlanningResolveStoryVerificationInput(ToolModel):
    project_id: str
    run_id: str
    owner_agent_run_id: str
    decision: StoryVerificationResolutionDecision
    rationale: str = Field(min_length=1, max_length=2400)


def register_tools(registry: ToolRegistry, service: PlanningToolService) -> None:
    def start_script_revision(
        request: PlanningStartScriptRevisionInput,
    ) -> ToolExecution[PlanningRunResponse]:
        response = service.start_script_revision(
            request.project_id,
            owner_agent_run_id=request.owner_agent_run_id,
            instruction=request.instruction,
            base_script_version_id=request.base_script_version_id,
            revision_origin=request.revision_origin,
            evidence_refs=request.evidence_refs,
        )
        return ToolExecution(
            output=response,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="content_planning_job",
                    artifact_id=response.run.id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                ),
            ),
            warnings=tuple(dict.fromkeys(response.run.warnings)),
        )

    registry.register(
        ToolSpec(
            id="planning.start_script_revision",
            version=2,
            category=ToolCategory.PLANNING,
            description=(
                "从当前精确 ScriptVersion 启动可恢复的剧本修订。"
                "USER_FEEDBACK 表示用户的权威修改意见；"
                "PRODUCTION_CONSTRAINT 表示分镜时长等制片契约与当前"
                "剧本冲突，必须携带可精确解析的版本化证据。"
                "Leader 仍基于完整语义决定少数场修订、全场逐场修订"
                "或结构重构。"
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=PlanningStartScriptRevisionInput,
            output_model=PlanningRunResponse,
            handler=start_script_revision,
            tags=frozenset(
                {
                    "planning",
                    "script-revision",
                    "agent-runtime",
                    "resumable",
                }
            ),
        )
    )

    def resolve_story_verification(
        request: PlanningResolveStoryVerificationInput,
    ) -> ToolExecution[PlanningRunResponse]:
        response = service.resolve_story_verification(
            request.project_id,
            run_id=request.run_id,
            owner_agent_run_id=request.owner_agent_run_id,
            decision=request.decision,
            rationale=request.rationale,
        )
        return ToolExecution(
            output=response,
            artifact_refs=(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="content_planning_job",
                    artifact_id=request.run_id,
                ),
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                ),
            ),
            warnings=tuple(dict.fromkeys(response.run.warnings)),
        )

    registry.register(
        ToolSpec(
            id="planning.resolve_story_verification",
            version=1,
            category=ToolCategory.PLANNING,
            description=(
                "由所属 AgentRun Leader 处理已持久化的 Script/Revision "
                "Verifier handoff。Leader 需阅读精确 report/verification，"
                "再语义选择继续聚焦修订、返回 Story Architect，"
                "或接受明确取舍并说明 rationale。该 Tool 不会将"
                " rejected/UNRESOLVED 结果伪造为 accepted，也不会自动排 Writer。"
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=PlanningResolveStoryVerificationInput,
            output_model=PlanningRunResponse,
            handler=resolve_story_verification,
            tags=frozenset(
                {
                    "planning",
                    "story-verification",
                    "leader-resolution",
                    "agent-runtime",
                    "resumable",
                }
            ),
        )
    )

    def advance_planning(
        request: PlanningAdvanceInput,
    ) -> ToolExecution[PlanningRunResponse]:
        current = service.get_planning_run(request.run_id)
        if current.run.project_id != request.project_id:
            raise ValueError(
                "Content planning job does not belong to the requested project"
            )
        if current.run.owner_agent_run_id != request.owner_agent_run_id:
            raise ValueError(
                "Content planning job does not belong to the requesting AgentRun"
            )

        response = service.advance_content_planning_job(
            request.run_id,
            owner_agent_run_id=request.owner_agent_run_id,
        )
        if response.run.project_id != request.project_id:
            raise ValueError(
                "Advanced content planning job no longer belongs to the requested project"
            )

        refs = [
            ArtifactRef(
                project_id=request.project_id,
                artifact_type="content_planning_job",
                artifact_id=request.run_id,
            )
        ]
        if response.project is not None:
            refs.append(
                ArtifactRef(
                    project_id=request.project_id,
                    artifact_type="project",
                    artifact_id=request.project_id,
                )
            )
        return ToolExecution(
            output=response,
            artifact_refs=tuple(refs),
            warnings=tuple(dict.fromkeys(response.run.warnings)),
        )

    registry.register(
        ToolSpec(
            id="planning.advance",
            version=1,
            category=ToolCategory.PLANNING,
            description=(
                "Advance one bounded unit of an AgentRun-owned Content Planning Job "
                "and return its persisted cursor."
            ),
            effect=ToolEffect.EXTERNAL,
            idempotent=False,
            input_model=PlanningAdvanceInput,
            output_model=PlanningRunResponse,
            handler=advance_planning,
            tags=frozenset(
                {"planning", "content-planning-job", "agent-runtime", "resumable"}
            ),
        )
    )


__all__ = [
    "PlanningAdvanceInput",
    "PlanningResolveStoryVerificationInput",
    "PlanningStartScriptRevisionInput",
    "register_tools",
]
