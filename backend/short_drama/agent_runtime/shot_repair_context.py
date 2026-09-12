"""Small failed-request relations needed to correct a compound media save."""

from pydantic import JsonValue


def attempted_shot_media_relations(tool_name: str, arguments: dict[str, JsonValue]) -> dict[str, JsonValue]:
    if tool_name != "shot.save_current":
        return {}
    request = arguments.get("request")
    if not isinstance(request, dict):
        return {}
    shot, registration = request.get("shot"), arguments.get("media_registration")
    relationships: dict[str, JsonValue] = {}
    if isinstance(registration, dict):
        entries = registration.get("reference_entries")
        if isinstance(entries, list):
            relationships["reference_entries"] = [{"request_index": index, **{
                key: entry[key] for key in ("subject_asset_id", "asset_version_id", "role", "lineage_root_version_id")
                if key in entry
            }} for index, entry in enumerate(entries) if isinstance(entry, dict)]
    if isinstance(shot, dict):
        cues: list[JsonValue] = []

        def collect(owner: dict[str, JsonValue], path: str) -> None:
            refs = owner.get("references")
            for index, cue in enumerate(refs if isinstance(refs, list) else []):
                if isinstance(cue, dict):
                    cues.append({"path": f"{path}/references/{index}", **{
                        key: cue[key] for key in ("id", "alias", "subject_asset_id", "asset_version_id", "usage",
                            "field", "text_start", "text_end", "text_anchor") if key in cue
                    }})

        collect(shot, "/request/shot")
        beats = shot.get("timeline_beats")
        for index, beat in enumerate(beats if isinstance(beats, list) else []):
            if isinstance(beat, dict):
                collect(beat, f"/request/shot/timeline_beats/{index}")
        if cues:
            relationships["cues"] = cues
    if relationships:
        relationships["note"] = "Exact relations from this failed request, not saved content. Correct the reported relation while preserving other intended bindings; full prose remains readable by attempt_ref."
    return relationships
