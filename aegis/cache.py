from __future__ import annotations

from pathlib import Path

from aegis.models import Evidence, FileRecord
from aegis.utils import write_json

import json


class FileRecordCache:
    def __init__(self, cache_dir: Path) -> None:
        self.path = cache_dir / "file_records.json"

    def load(self) -> dict[str, FileRecord]:
        if not self.path.exists():
            return {}
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        records: dict[str, FileRecord] = {}
        for raw in data.get("files", []):
            try:
                evidence = [Evidence(**item) for item in raw.get("evidence", [])]
                record = FileRecord(
                    path=raw["path"],
                    language=raw["language"],
                    size=int(raw["size"]),
                    lines=int(raw["lines"]),
                    content_hash=raw["content_hash"],
                    cached=True,
                    imports=list(raw.get("imports", [])),
                    symbols=list(raw.get("symbols", [])),
                    interfaces=list(raw.get("interfaces", [])),
                    calls=list(raw.get("calls", [])),
                    evidence=evidence,
                )
            except (KeyError, TypeError, ValueError):
                continue
            records[record.path] = record
        return records

    def save(self, records: list[FileRecord]) -> None:
        write_json(self.path, {"files": records})
