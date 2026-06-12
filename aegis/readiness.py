from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aegis.manifest import REQUIRED_ANALYSIS_ARTIFACTS, verify_manifest_integrity
from aegis.models import AnalysisResult


@dataclass
class ReadinessCheck:
    name: str
    status: str
    message: str
    detail: dict[str, Any] = field(default_factory=dict)


class ReadinessAssessor:
    REQUIRED_ARTIFACTS = REQUIRED_ANALYSIS_ARTIFACTS

    def __init__(
        self,
        result: AnalysisResult,
        *,
        doctor_payload: dict[str, Any] | None = None,
        evaluation_payload: dict[str, Any] | None = None,
        qa_payload: dict[str, Any] | None = None,
        threshold: float = 0.75,
    ) -> None:
        self.result = result
        self.doctor_payload = doctor_payload
        self.evaluation_payload = evaluation_payload
        self.qa_payload = qa_payload
        self.threshold = threshold

    def run(self) -> dict[str, Any]:
        checks = [
            self._doctor_check(),
            self._artifact_check(),
            self._manifest_check(),
            self._knowledge_check(),
            self._codegraph_check(),
            self._rag_check(),
            self._qa_check(),
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
        check = verify_manifest_integrity(
            self.result.output_dir,
            repo_name=self.result.knowledge.repo_name,
            required_artifacts=self.REQUIRED_ARTIFACTS,
        )
        return ReadinessCheck(
            name="manifest",
            status="ok" if check["ok"] else "error",
            message=(
                "Manifest describes this analysis run and verifies required artifact hashes."
                if check["ok"]
                else "Manifest is missing required metadata, has stale artifact inventory, or targets a different repository."
            ),
            detail=check["detail"],
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
                "prompt_context_coverage": metrics.get("prompt_context_coverage"),
                "prompt_context_expected_path_coverage": metrics.get(
                    "prompt_context_expected_path_coverage"
                ),
                "complete_file_context_coverage": metrics.get("complete_file_context_coverage"),
                "complete_file_expected_path_coverage": metrics.get(
                    "complete_file_expected_path_coverage"
                ),
            },
        )

    def _qa_check(self) -> ReadinessCheck:
        if not self.qa_payload:
            return ReadinessCheck(
                name="qa",
                status="warning",
                message="QA smoke question was not run. Pass --ready-ask to verify ask artifacts.",
            )
        outputs = {
            "qa_answer.json": self.result.output_dir / "qa_answer.json",
            "context_pack.md": self.result.output_dir / "context_pack.md",
            "llm_prompt.md": self.result.output_dir / "llm_prompt.md",
        }
        missing_artifacts = [name for name, path in outputs.items() if not path.exists()]
        context_pack = self.qa_payload.get("context_pack", {})
        blocks = context_pack.get("blocks", []) if isinstance(context_pack, dict) else []
        source_paths = context_pack.get("source_paths", []) if isinstance(context_pack, dict) else []
        complete_file_paths = (
            context_pack.get("complete_file_paths", []) if isinstance(context_pack, dict) else []
        )
        missing_required = self.qa_payload.get("missing_required_context_paths", [])
        incomplete_required = self.qa_payload.get("incomplete_required_context_paths", [])
        unsatisfied_required = self.qa_payload.get("unsatisfied_required_context_paths", [])
        target_context_paths = self.qa_payload.get("target_context_paths", [])
        missing_target = self.qa_payload.get("missing_target_context_paths", [])
        incomplete_target = self.qa_payload.get("incomplete_target_context_paths", [])
        unsatisfied_target = self.qa_payload.get("unsatisfied_target_context_paths", [])
        target_satisfied = bool(self.qa_payload.get("target_context_satisfied"))
        satisfied = bool(self.qa_payload.get("required_context_satisfied"))
        source_context_satisfied = bool(self.qa_payload.get("source_context_satisfied"))
        complete_file_context_satisfied = bool(self.qa_payload.get("complete_file_context_satisfied"))
        context_safe_for_llm = bool(self.qa_payload.get("context_safe_for_llm"))
        ok = (
            not missing_artifacts
            and bool(blocks)
            and bool(source_paths)
            and bool(complete_file_paths)
            and satisfied
            and target_satisfied
            and source_context_satisfied
            and complete_file_context_satisfied
            and context_safe_for_llm
            and not missing_required
            and not incomplete_required
            and not unsatisfied_required
            and not missing_target
            and not incomplete_target
            and not unsatisfied_target
        )
        return ReadinessCheck(
            name="qa",
            status="ok" if ok else "error",
            message=(
                "QA smoke produced prompt-ready complete-file context."
                if ok
                else "QA smoke did not produce safe prompt-ready context."
            ),
            detail={
                "question": self.qa_payload.get("question"),
                "used_llm": self.qa_payload.get("used_llm"),
                "missing_artifacts": missing_artifacts,
                "source_paths": source_paths,
                "complete_file_paths": complete_file_paths,
                "target_context_paths": target_context_paths,
                "context_safe_for_llm": context_safe_for_llm,
                "llm_skip_reason": self.qa_payload.get("llm_skip_reason"),
                "required_context_satisfied": satisfied,
                "target_context_satisfied": target_satisfied,
                "source_context_satisfied": source_context_satisfied,
                "complete_file_context_satisfied": complete_file_context_satisfied,
                "missing_required_context_paths": missing_required,
                "incomplete_required_context_paths": incomplete_required,
                "unsatisfied_required_context_paths": unsatisfied_required,
                "missing_target_context_paths": missing_target,
                "incomplete_target_context_paths": incomplete_target,
                "unsatisfied_target_context_paths": unsatisfied_target,
            },
        )

    @staticmethod
    def _summary(checks: list[ReadinessCheck]) -> dict[str, Any]:
        return {
            "ok": [check.name for check in checks if check.status == "ok"],
            "warnings": [check.name for check in checks if check.status == "warning"],
            "errors": [check.name for check in checks if check.status == "error"],
        }
