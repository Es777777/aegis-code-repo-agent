from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from aegis.models import AnalysisResult


@dataclass
class ReadinessCheck:
    name: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


class ReadinessAssessor:
    REQUIRED_ARTIFACTS = [
        "knowledge.json",
        "findings.json",
        "rag_index.json",
        "report.md",
        "report.html",
        "architecture.mmd",
        "manifest.json",
    ]

    def __init__(
        self,
        result: AnalysisResult,
        *,
        doctor_payload: dict[str, Any] | None = None,
        evaluation_payload: dict[str, Any] | None = None,
        threshold: float = 0.75,
    ) -> None:
        self.result = result
        self.doctor_payload = doctor_payload
        self.evaluation_payload = evaluation_payload
        self.threshold = threshold

    def run(self) -> dict[str, Any]:
        checks = [
            self._doctor_check(),
            self._artifact_check(),
            self._manifest_check(),
            self._knowledge_check(),
            self._codegraph_check(),
            self._rag_check(),
            self._evaluation_check(),
        ]
        errors = sum(1 for check in checks if check.status == "error")
        warnings = sum(1 for check in checks if check.status == "warning")
        return {
            "passed": errors == 0,
            "threshold": self.threshold,
            "errors": errors,
            "warnings": warnings,
            "checks": [asdict(check) for check in checks],
            "summary": self._summary(checks),
        }

    def _doctor_check(self) -> ReadinessCheck:
        if not self.doctor_payload:
            return ReadinessCheck(
                name="doctor",
                status="warning",
                message="Doctor payload was not provided.",
            )
        return ReadinessCheck(
            name="doctor",
            status="ok" if self.doctor_payload.get("passed") else "error",
            message=(
                "Environment checks passed."
                if self.doctor_payload.get("passed")
                else "Environment checks failed."
            ),
            detail={
                "errors": self.doctor_payload.get("errors", 0),
                "warnings": self.doctor_payload.get("warnings", 0),
            },
        )

    def _artifact_check(self) -> ReadinessCheck:
        missing = [
            name
            for name in self.REQUIRED_ARTIFACTS
            if not (self.result.output_dir / name).exists()
        ]
        return ReadinessCheck(
            name="artifacts",
            status="ok" if not missing else "error",
            message=(
                "All required analysis artifacts exist."
                if not missing
                else "Required analysis artifacts are missing."
            ),
            detail={
                "output_dir": str(self.result.output_dir),
                "required": self.REQUIRED_ARTIFACTS,
                "missing": missing,
            },
        )

    def _manifest_check(self) -> ReadinessCheck:
        path = self.result.output_dir / "manifest.json"
        if not path.exists():
            return ReadinessCheck(
                name="manifest",
                status="error",
                message="manifest.json is missing.",
                detail={"path": str(path)},
            )
        try:
            manifest = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError) as exc:
            return ReadinessCheck(
                name="manifest",
                status="error",
                message="manifest.json is not readable JSON.",
                detail={"path": str(path), "error": str(exc)},
            )
        repo = manifest.get("repo", {})
        ok = (
            manifest.get("schema_version") == "1.0"
            and repo.get("name") == self.result.knowledge.repo_name
            and "artifacts" in manifest
        )
        return ReadinessCheck(
            name="manifest",
            status="ok" if ok else "error",
            message=(
                "Manifest describes this analysis run."
                if ok
                else "Manifest is missing required metadata or targets a different repository."
            ),
            detail={
                "schema_version": manifest.get("schema_version"),
                "repo": repo.get("name"),
                "generated_at": manifest.get("generated_at"),
            },
        )

    def _knowledge_check(self) -> ReadinessCheck:
        stats = self.result.knowledge.stats
        file_count = int(stats.get("file_count", 0))
        total_lines = int(stats.get("total_lines", 0))
        ok = file_count > 0 and total_lines > 0
        return ReadinessCheck(
            name="knowledge",
            status="ok" if ok else "error",
            message=(
                "Repository knowledge contains scanned files and lines."
                if ok
                else "Repository knowledge is empty."
            ),
            detail={"file_count": file_count, "total_lines": total_lines},
        )

    def _codegraph_check(self) -> ReadinessCheck:
        stats = self.result.knowledge.code_graph.stats
        node_count = int(stats.get("node_count", 0))
        edge_count = int(stats.get("edge_count", 0))
        ok = node_count > 0 and edge_count > 0
        return ReadinessCheck(
            name="codegraph",
            status="ok" if ok else "error",
            message=(
                "CodeGraph contains nodes and edges."
                if ok
                else "CodeGraph is empty or incomplete."
            ),
            detail={"node_count": node_count, "edge_count": edge_count},
        )

    def _rag_check(self) -> ReadinessCheck:
        rag_stats = self.result.knowledge.stats.get("rag", {})
        chunk_count = int(rag_stats.get("chunk_count", 0)) if isinstance(rag_stats, dict) else 0
        chunk_kinds = rag_stats.get("chunk_kinds", {}) if isinstance(rag_stats, dict) else {}
        source_chunks = int(chunk_kinds.get("source", 0)) if isinstance(chunk_kinds, dict) else 0
        ok = chunk_count > 0 and source_chunks > 0
        return ReadinessCheck(
            name="rag",
            status="ok" if ok else "error",
            message=(
                "RAG index includes source context chunks."
                if ok
                else "RAG index does not include source context chunks."
            ),
            detail={"chunk_count": chunk_count, "source_chunks": source_chunks},
        )

    def _evaluation_check(self) -> ReadinessCheck:
        if not self.evaluation_payload:
            return ReadinessCheck(
                name="evaluation",
                status="warning",
                message="Evaluation was not run.",
            )
        metrics = self.evaluation_payload.get("metrics", {})
        total_cases = int(metrics.get("rag_cases", 0)) + int(metrics.get("trace_cases", 0))
        score = float(metrics.get("overall_score", 0.0))
        if total_cases <= 0:
            return ReadinessCheck(
                name="evaluation",
                status="warning",
                message="Evaluation suite has no cases for this repository.",
                detail={"overall_score": score, "cases": total_cases},
            )
        return ReadinessCheck(
            name="evaluation",
            status="ok" if score >= self.threshold else "error",
            message=(
                "Evaluation score meets readiness threshold."
                if score >= self.threshold
                else "Evaluation score is below readiness threshold."
            ),
            detail={
                "suite": self.evaluation_payload.get("suite"),
                "overall_score": score,
                "threshold": self.threshold,
                "cases": total_cases,
                "rag_recall": metrics.get("rag_recall"),
                "trace_success_rate": metrics.get("trace_success_rate"),
                "source_context_coverage": metrics.get("source_context_coverage"),
            },
        )

    @staticmethod
    def _summary(checks: list[ReadinessCheck]) -> dict[str, Any]:
        return {
            "ok": [check.name for check in checks if check.status == "ok"],
            "warnings": [check.name for check in checks if check.status == "warning"],
            "errors": [check.name for check in checks if check.status == "error"],
        }
