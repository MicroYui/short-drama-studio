"""Run the Phase 20 language-only Episode Reviewer on persisted real episodes."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from short_drama.models import EpisodeReviewRequest
from short_drama.service import ShortDramaService


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app-data-dir", type=Path, required=True)
    parser.add_argument(
        "--workspace-root",
        type=Path,
        default=Path.home()
        / "Library/Application Support/ShortDramaStudio/outputs/short-drama-projects",
    )
    parser.add_argument("--project-id", action="append", required=True)
    parser.add_argument("--max-actions", type=int, default=32)
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    app_data_dir = args.app_data_dir.expanduser().resolve()
    service = ShortDramaService(
        database_path=app_data_dir / "short-drama.sqlite3",
        workspace_root=args.workspace_root.expanduser().resolve(),
    )
    service.validate_language_provider_credentials()
    projects: list[dict[str, object]] = []
    for project_id in args.project_id:
        response = service.review_current_episode(
            project_id,
            EpisodeReviewRequest(
                max_actions=args.max_actions,
                force_new=True,
            ),
        )
        projects.append(
            {
                "project_id": project_id,
                "project_name": response.project.document.project.name,
                "report": response.report.model_dump(mode="json"),
            }
        )
    result = {
        "probe": "phase20-real-episode-review",
        "provider_usage": "language-only; no image or H3 generation",
        "projects": projects,
    }
    output_path = app_data_dir / "phase20-episode-review-probe.json"
    output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(json.dumps({**result, "output_path": str(output_path)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
