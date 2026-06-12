from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
from typing import Any

from aegis.models import AnalysisResult
from aegis.utils import write_json


SUMMARY_ARTIFACTS = [
    "knowledge.json",
    "findings.json",
    "rag_index.json",
    "report.md",
    "report.html",
    "architecture.mmd",
    "manifest.json",
    "evaluation.json",
    "impact.json",
    "readiness.json",
    "qa_answer.json",
    "context_pack.md",
    "llm_prompt.md",
    "run_summary.json",
]


def write_run_summary(
    result: AnalysisResult,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = build_run_summary(result, payload=payload)
    summary_path = result.output_dir / "run_summary.json"
    write_json(summary_path, summary)
    for _ in range(3):
        current_size = summary_path.stat().st_size if summary_path.exists() else 0
        artifact = summary["artifacts"]["run_summary.json"]
        if artifact.get("exists") is True and artifact.get("size") == current_size:
            break
        artifact["exists"] = summary_path.exists()
        artifact["size"] = current_size
        write_json(summary_path, summary)
    return summary


def build_run_summary(
    result: AnalysisResult,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    qa = _payload_or_artifact(payload, "qa", result.output_dir / "qa_answer.json")
    evaluation = _payload_or_artifact(payload, "evaluation", result.output_dir / "evaluation.json")
    readiness = _payload_or_artifact(payload, "readiness", result.output_dir / "readiness.json")
    impact = _payload_or_artifact(payload, "impact", result.output_dir / "impact.json")
    quality_gate = payload.get("quality_gate")
    if not isinstance(quality_gate, dict):
        quality_gate = {}

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": {
            "name": result.knowledge.repo_name,
            "root": result.knowledge.root,
            "output_dir": str(result.output_dir),
        },
        "status": _overall_status(qa, evaluation, readiness, quality_gate),
        "stats": _stats(result),
        "artifacts": _artifact_status(result.output_dir),
        "qa": _qa_summary(qa),
        "evaluation": _evaluation_summary(evaluation, quality_gate),
        "readiness": _readiness_summary(readiness),
        "impact": _impact_summary(impact),
        "next_actions": _next_actions(qa, evaluation, readiness, quality_gate),
    }


def _payload_or_artifact(
    payload: dict[str, Any],
    key: str,
    path: Path,
) -> dict[str, Any]:
    value = payload.get(key)
    if isinstance(value, dict):
        return value
    return _read_json_object(path)


def _read_json_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


def _stats(result: AnalysisResult) -> dict[str, Any]:
    knowledge = result.knowledge
    code_graph = knowledge.code_graph.stats
    rag = knowledge.stats.get("rag", {})
    return {
        "file_count": knowledge.stats.get("file_count", 0),
        "total_lines": knowledge.stats.get("total_lines", 0),
        "languages": knowledge.stats.get("languages", {}),
        "frameworks": knowledge.frameworks,
        "entrypoints": knowledge.entrypoints,
        "configs": knowledge.configs,
        "findings_count": len(result.findings),
        "code_graph": {
            "node_count": code_graph.get("node_count", 0),
            "edge_count": code_graph.get("edge_count", 0),
            "node_kinds": code_graph.get("node_kinds", {}),
            "edge_kinds": code_graph.get("edge_kinds", {}),
        },
        "rag": rag if isinstance(rag, dict) else {},
    }


def _artifact_status(output_dir: Path) -> dict[str, Any]:
    artifacts: dict[str, Any] = {}
    for name in SUMMARY_ARTIFACTS:
        path = output_dir / name
        artifacts[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        }
    return artifacts


def _qa_summary(qa: dict[str, Any]) -> dict[str, Any]:
    if not qa:
        return {"available": False}
    context_pack = qa.get("context_pack", {})
    if not isinstance(context_pack, dict):
        context_pack = {}
    return {
        "available": True,
        "question": qa.get("question"),
        "used_llm": bool(qa.get("used_llm")),
        "context_safe_for_llm": bool(qa.get("context_safe_for_llm")),
        "llm_skip_reason": qa.get("llm_skip_reason") or "",
        "required_context_satisfied": bool(qa.get("required_context_satisfied")),
        "target_context_satisfied": bool(qa.get("target_context_satisfied")),
        "source_context_satisfied": bool(qa.get("source_context_satisfied")),
        "complete_file_context_satisfied": bool(qa.get("complete_file_context_satisfied")),
        "source_paths": list(context_pack.get("source_paths", [])),
        "complete_file_paths": list(context_pack.get("complete_file_paths", [])),
        "target_context_paths": list(qa.get("target_context_paths", [])),
        "missing_required_context_paths": list(qa.get("missing_required_context_paths", [])),
        "missing_target_context_paths": list(qa.get("missing_target_context_paths", [])),
        "required_context_budget_chars": context_pack.get("required_context_budget_chars", 0),
        "target_context_budget_chars": context_pack.get("target_context_budget_chars", 0),
    }


def _evaluation_summary(
    evaluation: dict[str, Any],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    if not evaluation:
        return {"available": False}
    metrics = evaluation.get("metrics", {})
    if not isinstance(metrics, dict):
        metrics = {}
    return {
        "available": True,
        "suite": evaluation.get("suite"),
        "overall_score": metrics.get("overall_score"),
        "rag_recall": metrics.get("rag_recall"),
        "trace_success_rate": metrics.get("trace_success_rate"),
        "prompt_context_coverage": metrics.get("prompt_context_coverage"),
        "complete_file_context_coverage": metrics.get("complete_file_context_coverage"),
        "prompt_context_expected_path_coverage": metrics.get(
            "prompt_context_expected_path_coverage"
        ),
        "complete_file_expected_path_coverage": metrics.get(
            "complete_file_expected_path_coverage"
        ),
        "quality_gate": {
            "available": bool(quality_gate),
            "passed": quality_gate.get("passed"),
            "threshold": quality_gate.get("threshold"),
            "score": quality_gate.get("score"),
        },
    }


def _readiness_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    if not readiness:
        return {"available": False}
    return {
        "available": True,
        "passed": bool(readiness.get("passed")),
        "errors": readiness.get("errors", 0),
        "warnings": readiness.get("warnings", 0),
        "summary": readiness.get("summary", {}),
    }


def _impact_summary(impact: dict[str, Any]) -> dict[str, Any]:
    if not impact:
        return {"available": False}
    return {
        "available": True,
        "source": impact.get("source"),
        "depth": impact.get("depth"),
        "input_paths": list(impact.get("input_paths", [])),
        "affected_files": list(impact.get("affected_files", [])),
        "affected_symbols_count": len(impact.get("affected_symbols", [])),
        "nodes_count": len(impact.get("nodes", [])),
    }


def _overall_status(
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
) -> str:
    if readiness:
        return "ready" if readiness.get("passed") else "needs_attention"
    if quality_gate and quality_gate.get("passed") is False:
        return "needs_attention"
    if qa and not qa.get("context_safe_for_llm"):
        return "needs_attention"
    if evaluation:
        return "evaluated"
    if qa:
        return "qa_checked"
    return "analyzed"


def _next_actions(
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
) -> list[str]:
    actions: list[str] = []
    if not qa:
        actions.append("Run --ask or --ready-ask to verify prompt-ready RAG context.")
    elif not qa.get("context_safe_for_llm"):
        actions.append("Increase --context-chars or add --context-file until QA context is safe for LLM use.")
    if not evaluation:
        actions.append("Run --eval or --ready to measure RAG and CodeGraph quality.")
    elif quality_gate and quality_gate.get("passed") is False:
        actions.append("Inspect evaluation.json and improve failing RAG or CodeGraph cases.")
    if not readiness:
        actions.append("Run --ready --ready-ask to produce the competition readiness gate.")
    elif not readiness.get("passed"):
        actions.append("Inspect readiness.json checks and fix all error-status checks.")
    if not actions:
        actions.append("Ready for demo or downstream agent consumption.")
    return actions
