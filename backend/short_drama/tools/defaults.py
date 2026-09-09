"""Registration of the existing short-drama capabilities as typed tools."""

from __future__ import annotations

from typing import TYPE_CHECKING

from short_drama.tools.agent import AgentDelegator
from short_drama.tools.agent import register_tools as register_agent_tools
from short_drama.tools.artifact import register_tools as register_artifact_tools
from short_drama.tools.audio import register_tools as register_audio_tools
from short_drama.tools.change import register_tools as register_change_tools
from short_drama.tools.image import register_tools as register_image_tools
from short_drama.tools.planning import register_tools as register_planning_tools
from short_drama.tools.project import register_tools as register_project_tools
from short_drama.tools.qa import register_tools as register_qa_tools
from short_drama.tools.registry import ToolRegistry
from short_drama.tools.route import register_tools as register_route_tools
from short_drama.tools.script import register_tools as register_script_tools
from short_drama.tools.shot import register_tools as register_shot_tools
from short_drama.tools.task import register_tools as register_task_tools
from short_drama.tools.timeline import register_tools as register_timeline_tools
from short_drama.tools.video import register_tools as register_video_tools
from short_drama.tools.studio import register_tools as register_studio_tools

if TYPE_CHECKING:
    from short_drama.service import ShortDramaService


def build_default_tool_registry(
    service: ShortDramaService,
    *,
    registry: ToolRegistry | None = None,
    agent_delegator: AgentDelegator | None = None,
) -> ToolRegistry:
    """Wrap current domain methods without moving or duplicating their logic."""

    target = registry if registry is not None else ToolRegistry()
    register_project_tools(target, service)
    register_planning_tools(target, service)
    register_artifact_tools(target, service)
    register_script_tools(target, service)
    register_shot_tools(target, service)
    register_image_tools(target, service)
    register_video_tools(target, service)
    register_audio_tools(target, service)
    register_timeline_tools(target, service)
    register_qa_tools(target, service)
    register_task_tools(target, service)
    register_route_tools(target, service)
    register_change_tools(target, service)
    register_studio_tools(target, service)
    if agent_delegator is not None:
        register_agent_tools(target, agent_delegator)
    return target
