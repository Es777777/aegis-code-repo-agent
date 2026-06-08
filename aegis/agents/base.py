from __future__ import annotations

from abc import ABC, abstractmethod

from aegis.models import Evidence, Finding, FileRecord, RepoKnowledge


class BaseAgent(ABC):
    name = "BaseAgent"

    @abstractmethod
    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        raise NotImplementedError

    def finding(
        self,
        title: str,
        summary: str,
        *,
        severity: str = "info",
        evidence: list[Evidence] | None = None,
        confidence: float = 0.75,
        tags: list[str] | None = None,
    ) -> Finding:
        return Finding(
            agent=self.name,
            title=title,
            summary=summary,
            severity=severity,
            confidence=confidence,
            evidence=evidence or [],
            tags=tags or [],
        )


def records_by_path(knowledge: RepoKnowledge) -> dict[str, FileRecord]:
    return {record.path: record for record in knowledge.files}


def evidence_from_records(records: list[FileRecord], limit: int = 5) -> list[Evidence]:
    evidence: list[Evidence] = []
    for record in records:
        evidence.extend(record.evidence)
        if len(evidence) >= limit:
            break
    if evidence:
        return evidence[:limit]
    return [
        Evidence(
            path=record.path,
            line=1,
            snippet=f"{record.language} file with {record.lines} lines",
            confidence=0.55,
            source="file-metadata",
        )
        for record in records[:limit]
    ]


def top_records(knowledge: RepoKnowledge, paths: list[str], limit: int = 5) -> list[FileRecord]:
    lookup = records_by_path(knowledge)
    return [lookup[path] for path in paths[:limit] if path in lookup]
