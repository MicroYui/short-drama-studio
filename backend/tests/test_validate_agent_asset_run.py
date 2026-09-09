from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from typing import cast
from urllib.parse import urlsplit

import pytest

import validate_agent_storyboard_run
from validate_agent_asset_run import (
    AgentAssetApiClient,
    DEFAULT_OBJECTIVE,
    JsonObject,
    _probe_http_boundary_errors,
    _validate_asset_stage_result,
    main,
)
from validate_agent_storyboard_run import RawResponseRecorder


def _run_view(status: str) -> JsonObject:
    return {
        "snapshot": {
            "run": {
                "id": "agent-run-assets",
                "project_id": "project-1",
                "status": status,
            },
            "actions": [],
            "current_plan": {
                "id": "agent-plan-assets",
                "revision": 1,
                "summary": "完成核心资产计划并在环境允许时执行。",
                "goal_complete": status == "COMPLETED",
                "selected_method_skill_ids": ["drama_series"],
            },
        }
    }


def _board(*, ready: bool) -> JsonObject:
    return {
        "steps": ([{"id": "step-assets", "status": "READY"}] if ready else []),
        "work_orders": [],
        "historical_work_orders": [],
        "blockers": [],
        "open_questions": [],
        "next_actions": [],
        "artifact_refs": [],
        "current_focus": "核心资产",
    }


def _project(*, with_plan: bool, with_media: bool) -> JsonObject:
    documents: list[JsonObject] = [
        {
            "id": "spec-doc",
            "project_id": "project-1",
            "kind": "production.spec",
            "title": "Production Spec",
            "current_version_id": "spec-v1",
            "versions": [],
        },
        {
            "id": "storyboard-doc",
            "project_id": "project-1",
            "kind": "storyboard",
            "title": "Storyboard",
            "current_version_id": "storyboard-v2",
            "versions": [],
        },
    ]
    output_slots: list[JsonObject] = []
    if with_plan:
        documents.append(
            {
                "id": "asset-plan-doc",
                "project_id": "project-1",
                "kind": "asset.plan",
                "title": "Asset Plan",
                "current_version_id": "asset-plan-v1",
                "versions": [],
            }
        )
        output_slots.append(
            {
                "id": "asset.plan.current",
                "current_artifact_ref": {
                    "project_id": "project-1",
                    "artifact_type": "asset_plan",
                    "artifact_id": "asset-plan-doc",
                    "version": 1,
                },
            }
        )

    assets: list[JsonObject] = []
    reference_packs: list[JsonObject] = []
    reference_plan: list[JsonObject] = []
    if with_media:
        for asset_id, kind, version_id in (
            ("asset-character", "CHARACTER", "character-v1"),
            ("asset-location", "LOCATION", "location-v1"),
        ):
            assets.append(
                {
                    "id": asset_id,
                    "project_id": "project-1",
                    "kind": kind,
                    "name": asset_id,
                    "brief": {
                        "identity_anchor": asset_id,
                        "visual_attributes": ["stable"],
                    },
                    "current_version_id": version_id,
                    "approved_version_id": version_id,
                    "versions": [
                        {
                            "id": version_id,
                            "version": 1,
                            "media_type": "IMAGE",
                            "approval_status": "APPROVED",
                            "freshness": "CURRENT",
                        }
                    ],
                }
            )
            reference_packs.append(
                {
                    "id": f"pack-{asset_id}",
                    "project_id": "project-1",
                    "subject_asset_id": asset_id,
                    "name": asset_id,
                    "entries": [
                        {
                            "id": f"entry-{asset_id}",
                            "asset_version_id": version_id,
                            "role": "CANONICAL_MASTER",
                            "description": "approved anchor",
                        }
                    ],
                }
            )
        reference_plan = [
            {
                "shot_id": "shot-1",
                "bindings": [
                    {
                        "kind": "CHARACTER",
                        "target_id": "character-v1",
                        "purpose": "identity",
                    },
                    {
                        "kind": "LOCATION",
                        "target_id": "location-v1",
                        "purpose": "space",
                    },
                ],
                "requirements": [],
                "capability_valid": True,
                "validation_notes": [],
            }
        ]

    return {
        "document": {
            "project": {
                "id": "project-1",
                "current_script_version_id": "script-v4",
                "current_bible_version_id": "bible-v3",
                "current_manifest_id": None,
            },
            "script_versions": [],
            "bible_versions": [],
            "assets": assets,
            "shots": [{"id": "shot-1"}],
            "execution_plan": [],
            "reference_plan": reference_plan,
            "manifests": [],
            "reference_packs": reference_packs,
            "production_documents": documents,
            "production_spec_document_id": "spec-doc",
            "storyboard_document_id": "storyboard-doc",
            "asset_plan_document_id": "asset-plan-doc" if with_plan else None,
            "generation_plan_document_id": None,
            "audio_structure_document_id": None,
            "master_audio_document_id": None,
            "timed_text_document_id": None,
            "output_slots": output_slots,
        },
        "tasks": [],
        "takes": [],
        "exports": [],
    }


def _asset_plan_response(*, with_media: bool) -> JsonObject:
    character_completion: JsonObject = {}
    location_completion: JsonObject = {}
    if with_media:
        character_completion = {
            "completed_asset_id": "asset-character",
            "completed_asset_version_id": "character-v1",
        }
        location_completion = {
            "completed_asset_id": "asset-location",
            "completed_asset_version_id": "location-v1",
        }
    payload: JsonObject = {
        "project_id": "project-1",
        "script_version_id": "script-v4",
        "bible_version_id": "bible-v3",
        "production_spec_document_id": "spec-doc",
        "production_spec_version_id": "spec-v1",
        "storyboard_document_id": "storyboard-doc",
        "storyboard_version_id": "storyboard-v2",
        "items": [
            {
                "id": "asset-item-character",
                "disposition": "ANCHOR",
                "asset_kind": "CHARACTER",
                "name": "男高中生林一",
                "serves_shot_ids": ["shot-1"],
                "serves_clip_group_ids": ["clip-1"],
                "brief": {
                    "identity_anchor": "十八岁男高中生",
                    "visual_attributes": ["黑色碎发"],
                    "layout_profile": "DRAMA_CHARACTER_DUAL_ZONE_16_9",
                },
                "rationale": "主角跨镜头反复出现。",
                "work_items": [
                    {
                        "id": "work-character-master",
                        "operation": "GENERATE",
                        "output_role": "character_master",
                        "final_prompt": "生成 16:9 双区男高中生角色卡。",
                        "size": "1344x768",
                        "ordered_references": [],
                        "dependency_ids": [],
                    }
                ],
                **character_completion,
            },
            {
                "id": "asset-item-location",
                "disposition": "ANCHOR",
                "asset_kind": "LOCATION",
                "name": "学校食堂",
                "serves_shot_ids": ["shot-1"],
                "serves_clip_group_ids": ["clip-1"],
                "brief": {
                    "identity_anchor": "学校食堂打饭窗口",
                    "visual_attributes": ["不锈钢菜盆"],
                    "layout_profile": "SCENE_FOUR_DIRECTION_2X2",
                },
                "rationale": "整集多个镜头复用同一空间。",
                "work_items": [
                    {
                        "id": "work-location-master",
                        "operation": "GENERATE",
                        "output_role": "location_master",
                        "final_prompt": "生成食堂前后左右四向 2x2 环境卡。",
                        "size": "1344x768",
                        "ordered_references": [],
                        "dependency_ids": [],
                    }
                ],
                **location_completion,
            },
            {
                "id": "asset-item-crowd",
                "disposition": "TEXT_ONLY",
                "asset_kind": "REFERENCE",
                "name": "围观同学群像",
                "serves_shot_ids": ["shot-1"],
                "serves_clip_group_ids": ["clip-1"],
                "rationale": "一次性背景反应，不需要过度制卡。",
                "work_items": [],
            },
        ],
        "notes": [],
    }
    return {
        "document": {
            "id": "asset-plan-doc",
            "project_id": "project-1",
            "kind": "asset.plan",
            "title": "Asset Plan",
            "current_version_id": "asset-plan-v1",
            "versions": [],
        },
        "version": {
            "id": "asset-plan-v1",
            "version": 1,
            "payload": payload,
            "source_artifact_refs": [
                {
                    "project_id": "project-1",
                    "artifact_type": "script_version",
                    "artifact_id": "script-v4",
                    "version": 4,
                },
                {
                    "project_id": "project-1",
                    "artifact_type": "bible_version",
                    "artifact_id": "bible-v3",
                    "version": 3,
                },
                {
                    "project_id": "project-1",
                    "artifact_type": "production_spec",
                    "artifact_id": "spec-doc",
                    "version": 1,
                },
                {
                    "project_id": "project-1",
                    "artifact_type": "storyboard",
                    "artifact_id": "storyboard-doc",
                    "version": 2,
                },
            ],
        },
    }


def _empty_trace() -> JsonObject:
    return {"phases": []}


def test_default_objective_keeps_asset_work_agent_owned_and_stage_bounded() -> None:
    assert "AssetPlan" in DEFAULT_OBJECTIVE
    assert "自主判断资产深度" in DEFAULT_OBJECTIVE
    assert "可选 16:9 双区角色卡" in DEFAULT_OBJECTIVE
    assert "不得伪造图片" in DEFAULT_OBJECTIVE
    assert "不编译 GenerationPlan" in DEFAULT_OBJECTIVE


def test_asset_stage_validator_distinguishes_plan_and_media_closure() -> None:
    before = _project(with_plan=False, with_media=False)

    plan_only = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=before,
        after=_project(with_plan=True, with_media=False),
        asset_plan_response=_asset_plan_response(with_media=False),
        trace=_empty_trace(),
    )
    assert plan_only.errors == []
    assert plan_only.media_closed is False
    assert len(plan_only.media_gaps) == 2

    full = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=before,
        after=_project(with_plan=True, with_media=True),
        asset_plan_response=_asset_plan_response(with_media=True),
        trace=_empty_trace(),
    )
    assert full.errors == []
    assert full.media_closed is True
    assert full.completed_anchor_item_ids == [
        "asset-item-character",
        "asset-item-location",
    ]


def test_asset_stage_validator_does_not_treat_zero_anchors_as_media_closure() -> None:
    before = _project(with_plan=False, with_media=False)
    response = _asset_plan_response(with_media=False)
    payload = cast(JsonObject, cast(JsonObject, response["version"])["payload"])
    payload["items"] = [
        value
        for value in cast(list[object], payload["items"])
        if cast(JsonObject, value).get("disposition") != "ANCHOR"
    ]

    assessment = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=before,
        after=_project(with_plan=True, with_media=False),
        asset_plan_response=response,
        trace=_empty_trace(),
    )

    assert assessment.errors == []
    assert assessment.anchor_item_ids == []
    assert assessment.completed_anchor_item_ids == []
    assert assessment.media_closed is False


def test_unfulfilled_requirement_is_not_counted_as_a_shot_media_binding() -> None:
    before = _project(with_plan=False, with_media=False)
    after = _project(with_plan=True, with_media=True)
    document = cast(JsonObject, after["document"])
    reference_plan = cast(list[object], document["reference_plan"])
    plan = cast(JsonObject, reference_plan[0])
    plan["bindings"] = []
    plan["requirements"] = [
        {
            "id": "requires-character-profile",
            "subject_asset_id": "asset-character",
            "acceptable_roles": ["CHARACTER_PROFILE"],
            "preferred_angles": ["LEFT_PROFILE"],
            "preferred_framings": ["MEDIUM"],
            "required_tags": ["profile"],
            "purpose": "preserve the character profile",
            "required": True,
        }
    ]

    assessment = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=before,
        after=after,
        asset_plan_response=_asset_plan_response(with_media=True),
        trace=_empty_trace(),
    )

    assert assessment.media_closed is False
    assert any(
        "missing shot reference bindings" in gap for gap in assessment.media_gaps
    )


def test_asset_stage_validator_rejects_downstream_generation() -> None:
    before = _project(with_plan=False, with_media=False)
    after = _project(with_plan=True, with_media=False)
    after_document = cast(JsonObject, after["document"])
    after_document["generation_plan_document_id"] = "generation-plan-doc"
    response = _asset_plan_response(with_media=False)
    payload = cast(JsonObject, cast(JsonObject, response["version"])["payload"])
    payload["items"] = [
        value
        for value in cast(list[object], payload["items"])
        if cast(JsonObject, value).get("disposition") == "ANCHOR"
    ]
    trace: JsonObject = {
        "phases": [
            {
                "new_actions": [
                    {
                        "id": "action-video",
                        "status": "SUCCEEDED",
                        "tool_name": "video.enqueue",
                    }
                ],
                "specialist_tool_events": [],
                "work_order_updates": [],
            }
        ]
    }

    assessment = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=before,
        after=after,
        asset_plan_response=response,
        trace=trace,
    )

    assert "Asset-only run changed generation_plan_document_id" in assessment.errors
    assert any("video.enqueue" in error for error in assessment.errors)


@pytest.mark.parametrize("plate_state", ["MATCHED", "NONPREFERRED", "UNMATCHED", "MISSING_ENTRY", "STALE_ROOT"])
def test_text_plate_closure_uses_its_carrier_pack_and_exact_requirement(
    plate_state: str,
) -> None:
    after = _project(with_plan=True, with_media=True)
    document = cast(JsonObject, after["document"])
    assets = cast(list[JsonObject], document["assets"])
    for asset_id, kind, version_id in [
        ("asset-price-carrier", "PROP", "price-carrier-v1"),
        ("asset-pork-text", "REFERENCE", "pork-text-v1"),
    ]:
        assets.append({
            "id": asset_id,
            "kind": kind,
            "brief": {"identity_anchor": asset_id},
            "approved_version_id": version_id,
            "versions": [{
                "id": version_id,
                "media_type": "IMAGE",
                "approval_status": "APPROVED",
                "freshness": "CURRENT",
            }],
        })
    entries: list[JsonObject] = [{
        "asset_version_id": "price-carrier-v1",
        "role": "CANONICAL_MASTER",
        "lineage_root_version_id": "price-carrier-v1",
    }]
    if plate_state != "MISSING_ENTRY":
        entries.append({
            "asset_version_id": "pork-text-v1",
            "role": "PROP_STATE",
            "tags": ["pork-price"],
            "angle": "FRONT" if plate_state == "NONPREFERRED" else "NOT_APPLICABLE",
            "framing": "MEDIUM" if plate_state == "NONPREFERRED" else "DETAIL",
            "lineage_root_version_id": (
                "price-carrier-old" if plate_state == "STALE_ROOT" else None
            ),
        })
    cast(list[JsonObject], document["reference_packs"]).append({
        "subject_asset_id": "asset-price-carrier",
        "entries": entries,
    })
    shot_plan = cast(list[JsonObject], document["reference_plan"])[0]
    shot_plan["requirements"] = [{
        "subject_asset_id": "asset-price-carrier",
        "acceptable_roles": ["PROP_STATE"],
        "required_tags": ["greens-price" if plate_state == "UNMATCHED" else "pork-price"],
        "preferred_angles": ["NOT_APPLICABLE"],
        "preferred_framings": ["DETAIL"],
    }]
    response = _asset_plan_response(with_media=True)
    payload = cast(JsonObject, cast(JsonObject, response["version"])["payload"])
    cast(list[JsonObject], payload["items"]).append({
        "id": "asset-item-pork-text",
        "disposition": "ANCHOR",
        "asset_kind": "REFERENCE",
        "brief": {"identity_anchor": "红烧肉八元文字内容层"},
        "rationale": "跨镜头复用准确文字，与无字物理载体分开保存。",
        "serves_shot_ids": ["shot-1"],
        "completed_asset_id": "asset-pork-text",
        "completed_asset_version_id": "pork-text-v1",
        "work_items": [{
            "id": "work-pork-text",
            "final_prompt": "确定性排版红烧肉八元文字内容层。",
            "dependency_ids": [],
            "ordered_references": [],
        }],
    })

    assessment = _validate_asset_stage_result(
        run_view=_run_view("COMPLETED"),
        before=_project(with_plan=False, with_media=False),
        after=after,
        asset_plan_response=response,
        trace=_empty_trace(),
    )

    assert assessment.errors == []
    assert assessment.media_closed is (plate_state in {"MATCHED", "NONPREFERRED"})
    if plate_state in {"MATCHED", "NONPREFERRED"}:
        assert "asset-item-pork-text" in assessment.completed_anchor_item_ids
    else:
        assert "asset-item-pork-text" not in assessment.completed_anchor_item_ids
        assert any("asset-item-pork-text" in gap for gap in assessment.media_gaps)


def test_probe_http_boundary_allows_only_agent_run_mutations(tmp_path: Path) -> None:
    client = AgentAssetApiClient(
        "http://127.0.0.1:8000",
        timeout_seconds=None,
        recorder=RawResponseRecorder(tmp_path),
    )
    assert not hasattr(client, "create_asset")
    assert not hasattr(client, "add_reference_pack_entry")
    assert _probe_http_boundary_errors(
        [
            ("GET", "/projects/project-1"),
            ("POST", "/projects/project-1/agent-runs"),
            ("POST", "/agent-runs/run-1/advance"),
        ]
    ) == []
    assert _probe_http_boundary_errors(
        [("POST", "/projects/project-1/reference-packs/entries")]
    ) == [
        "Probe issued forbidden direct mutation POST "
        "/projects/project-1/reference-packs/entries"
    ]


class _FakeHttpResponse:
    def __init__(self, body: JsonObject) -> None:
        self.status = 200
        self._body = json.dumps(body, ensure_ascii=False).encode("utf-8")

    def __enter__(self) -> _FakeHttpResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def test_main_uses_only_public_agent_http_and_reports_plan_only(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    before = _project(with_plan=False, with_media=False)
    after = _project(with_plan=True, with_media=False)
    plan_response = _asset_plan_response(with_media=False)
    project_reads = 0
    observed: list[tuple[str, str]] = []

    def fake_urlopen(request: object, timeout: float | None = None) -> _FakeHttpResponse:
        nonlocal project_reads
        del timeout
        method = str(getattr(request, "method"))
        url = str(getattr(request, "full_url"))
        path = urlsplit(url).path.removeprefix("/api/short-drama")
        observed.append((method, path))
        if method == "GET" and path == "/projects/project-1":
            project_reads += 1
            return _FakeHttpResponse(before if project_reads == 1 else after)
        if method == "POST" and path == "/projects/project-1/agent-runs":
            return _FakeHttpResponse(_run_view("WAITING"))
        if method == "GET" and path == "/agent-runs/agent-run-assets/board":
            ready = not any(
                item == ("POST", "/agent-runs/agent-run-assets/advance")
                for item in observed
            )
            return _FakeHttpResponse(_board(ready=ready))
        if method == "POST" and path == "/agent-runs/agent-run-assets/advance":
            return _FakeHttpResponse(
                {
                    "run": _run_view("COMPLETED"),
                    "executed_action_ids": [],
                    "created_work_order_ids": [],
                    "advanced_work_order_ids": [],
                    "notes": [],
                }
            )
        if method == "GET" and path == "/agent-runs/agent-run-assets":
            return _FakeHttpResponse(_run_view("COMPLETED"))
        if method == "GET" and path == (
            "/projects/project-1/production-documents/asset-plan-doc"
        ):
            return _FakeHttpResponse(plan_response)
        raise AssertionError(f"unexpected request: {method} {path}")

    monkeypatch.setattr(validate_agent_storyboard_run, "urlopen", fake_urlopen)
    output_dir = tmp_path / "probe"
    exit_code = main(
        [
            "--project-id",
            "project-1",
            "--output-dir",
            str(output_dir),
            "--progress-heartbeat-seconds",
            "0.01",
        ]
    )

    assert exit_code == 0
    trace = json.loads((output_dir / "trace.json").read_text(encoding="utf-8"))
    assert trace["outcome"] == "PASSED_PLAN_ONLY"
    assert trace["validation"]["plan_closed"] is True
    assert trace["validation"]["media_closed"] is False
    assert (output_dir / "asset_plan.md").exists()
    assert _probe_http_boundary_errors(observed) == []
    assert {
        path
        for method, path in observed
        if method != "GET"
    } == {
        "/projects/project-1/agent-runs",
        "/agent-runs/agent-run-assets/advance",
    }
