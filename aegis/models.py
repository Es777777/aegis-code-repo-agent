from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Evidence:
    path: str
    line: int
    snippet: str
    confidence: float = 0.75
    source: str = "static-scan"


@dataclass
class Finding:
    agent: str
    title: str
    summary: str
    severity: str = "info"
    confidence: float = 0.75
    evidence: list[Evidence] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass
class FileRecord:
    path: str
    language: str
    size: int
    lines: int
    content_hash: str
    cached: bool = False
    imports: list[str] = field(default_factory=list)
    symbols: list[str] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    calls: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)


@dataclass
class RepoKnowledge:
    root: str
    repo_name: str
    files: list[FileRecord]
    frameworks: list[str]
    entrypoints: list[str]
    configs: list[str]
    changed_files: list[str]
    repo_map: list[str]
    dependency_graph: dict[str, list[str]]
    call_graph: dict[str, list[str]]
    interface_catalog: dict[str, list[str]]
    evidence_store: list[Evidence]
    stats: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AnalysisResult:
    knowledge: RepoKnowledge
    findings: list[Finding]
    output_dir: Path

    def to_dict(self) -> dict[str, Any]:
        return {
            "knowledge": self.knowledge.to_dict(),
            "findings": [asdict(item) for item in self.findings],
            "output_dir": str(self.output_dir),
        }
