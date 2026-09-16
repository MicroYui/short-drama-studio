"""Progress and cooperative cancellation for local package IO."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import IO


class PackageCancelled(Exception):
    pass


@dataclass
class PackageProgress:
    on_update: Callable[["PackageProgress"], None] | None = None
    check_cancelled: Callable[[], None] | None = None
    stage: str = "queued"
    message: str = "等待开始"
    processed_files: int = 0
    total_files: int | None = None
    processed_bytes: int = 0
    total_bytes: int | None = None

    def check(self) -> None:
        if self.check_cancelled is not None:
            self.check_cancelled()

    def report(self) -> None:
        self.check()
        if self.on_update is not None:
            self.on_update(self)

    def begin(self, stage: str, message: str, *, total_files: int | None = None,
              total_bytes: int | None = None) -> None:
        self.stage, self.message = stage, message
        # Keep the completed IO counters visible during the final commit and in
        # the persisted result. Other phases explicitly start new counters.
        if stage != "committing":
            self.processed_files = self.processed_bytes = 0
            self.total_files, self.total_bytes = total_files, total_bytes
        self.report()

    def advance(self, *, files: int = 0, bytes_count: int = 0) -> None:
        self.processed_files += files
        self.processed_bytes += bytes_count
        self.report()

    def copy(self, source: IO[bytes], destination: IO[bytes]) -> None:
        while True:
            self.check()
            chunk = source.read(1024 * 1024)
            if not chunk:
                break
            destination.write(chunk)
            self.advance(bytes_count=len(chunk))
