"""Merge concurrent current-content edits without a user-facing version system."""

from __future__ import annotations

from copy import deepcopy
from typing import cast

from pydantic import JsonValue


def _keyed(items: list[JsonValue]) -> dict[str, JsonValue] | None:
    keyed: dict[str, JsonValue] = {}
    for item in items:
        if not isinstance(item, dict):
            return None
        key = next((str(item[field]) for field in ("id", "shot_id", "character_id") if isinstance(item.get(field), str)), None)
        if key is None or key in keyed:
            return None
        keyed[key] = item
    return keyed


def merge_current_content(
    base: JsonValue, proposed: JsonValue, saved: JsonValue,
    *, path: str = "", conflicts: list[str] | None = None,
) -> JsonValue:
    """Apply only changed fields; a conflicting later saved edit wins.

    Object arrays use their stable domain IDs. Untouched values are read from
    current storage, so a long model call cannot replace unrelated user edits.
    """
    if path == "" and isinstance(base, dict) and isinstance(proposed, dict) and isinstance(saved, dict):
        old_project, current_project = base.get("project"), saved.get("project")
        if isinstance(old_project, dict) and isinstance(current_project, dict) and old_project.get("current_script_version_id") != current_project.get("current_script_version_id"):
            proposed_scripts, saved_scripts = proposed.get("script_versions"), saved.get("script_versions")
            if isinstance(proposed_scripts, list) and isinstance(saved_scripts, list):
                saved_by_id = _keyed(saved_scripts) or {}
                protected: list[JsonValue] = []
                for item in proposed_scripts:
                    if isinstance(item, dict) and isinstance(item.get("id"), str):
                        key = str(item["id"])
                        existing = saved_by_id.get(key)
                        if existing is not None and key != current_project.get("current_script_version_id") and item != existing:
                            if conflicts is not None:
                                conflicts.append(f"/script_versions/{key}")
                            item = existing
                    protected.append(item)
                proposed = {**proposed, "script_versions": protected}
    if proposed == base or proposed == saved:
        return deepcopy(saved)
    if saved == base:
        return deepcopy(proposed)
    if isinstance(base, dict) and isinstance(proposed, dict) and isinstance(saved, dict):
        result: dict[str, JsonValue] = {}
        for key in dict.fromkeys([*saved, *proposed, *base]):
            location = f"{path}/{key}"
            if key not in proposed:
                if key in saved and (key not in base or saved[key] != base[key]):
                    result[key] = deepcopy(saved[key])
                    if key in base and conflicts is not None:
                        conflicts.append(location)
            elif key not in saved:
                if key not in base:
                    result[key] = deepcopy(proposed[key])
                elif proposed[key] != base[key] and conflicts is not None:
                    conflicts.append(location)
            else:
                result[key] = merge_current_content(base.get(key), proposed[key], saved[key], path=location, conflicts=conflicts)
        return result
    if isinstance(base, list) and isinstance(proposed, list) and isinstance(saved, list):
        base_map, proposed_map, saved_map = _keyed(base), _keyed(proposed), _keyed(saved)
        if base_map is not None and proposed_map is not None and saved_map is not None:
            merged = cast(dict[str, JsonValue], merge_current_content(base_map, proposed_map, saved_map, path=path, conflicts=conflicts))
            # Honor a new order when it does not compete with a saved reorder.
            order = list(proposed_map) if list(saved_map) == list(base_map) else list(saved_map)
            return [merged[key] for key in dict.fromkeys([*order, *merged]) if key in merged]
    if conflicts is not None:
        conflicts.append(path)
    return deepcopy(saved)
