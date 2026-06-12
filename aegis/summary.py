from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import re
from typing import Callable
from typing import Any

from aegis.artifacts import ArtifactLoadError, load_rag_index
from aegis.manifest import ARTIFACT_CONTRACTS, reuse_readiness_by_command
from aegis.models import AnalysisResult
from aegis.utils import file_sha256, write_json


HANDOFF_CARD_SCHEMA_VERSION = "1.0"


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
    "handoff_card.json",
]


def write_run_summary(
    result: AnalysisResult,
    *,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    summary = build_run_summary(result, payload=payload)
    handoff_card = build_handoff_card(result, payload=payload, summary=summary)
    summary_path = result.output_dir / "run_summary.json"
    handoff_path = result.output_dir / "handoff_card.json"
    write_json(summary_path, summary)
    write_json(handoff_path, handoff_card)
    for _ in range(3):
        current_size = summary_path.stat().st_size if summary_path.exists() else 0
        handoff_size = handoff_path.stat().st_size if handoff_path.exists() else 0
        artifact = summary["artifacts"]["run_summary.json"]
        handoff_artifact = summary["artifacts"]["handoff_card.json"]
        if (
            artifact.get("exists") is True
            and artifact.get("size") == current_size
            and handoff_artifact.get("exists") is True
            and handoff_artifact.get("size") == handoff_size
        ):
            break
        artifact["exists"] = summary_path.exists()
        artifact["size"] = current_size
        handoff_artifact["exists"] = handoff_path.exists()
        handoff_artifact["size"] = handoff_size
        write_json(summary_path, summary)
        write_json(handoff_path, build_handoff_card(result, payload=payload, summary=summary))
    return summary


def stabilize_manifest_and_summary(
    result: AnalysisResult,
    *,
    manifest_builder: Callable[[], dict[str, Any]],
    payload: dict[str, Any] | None = None,
    max_rounds: int = 6,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest_path = result.output_dir / "manifest.json"
    summary_path = result.output_dir / "run_summary.json"
    handoff_path = result.output_dir / "handoff_card.json"
    previous_state: tuple[str, str, str] | None = None
    manifest: dict[str, Any] = {}
    summary: dict[str, Any] = {}
    for _ in range(max_rounds):
        manifest = manifest_builder()
        write_json(manifest_path, manifest)
        summary = write_run_summary(result, payload=payload)
        if not manifest_path.exists() or not summary_path.exists() or not handoff_path.exists():
            break
        state = (
            file_sha256(manifest_path),
            file_sha256(summary_path),
            file_sha256(handoff_path),
        )
        if state == previous_state:
            break
        previous_state = state
    return manifest, summary


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
        "schema_version": HANDOFF_CARD_SCHEMA_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": {
            "name": result.knowledge.repo_name,
            "root": result.knowledge.root,
            "output_dir": str(result.output_dir),
        },
        "status": _overall_status(qa, evaluation, readiness, quality_gate),
        "stats": _stats(result),
        "artifacts": _artifact_status(result.output_dir),
        "artifact_contracts": _artifact_contract_summary(result.output_dir),
        "orchestration": _orchestration_summary(result.output_dir, qa, evaluation, readiness, quality_gate),
        "qa": _qa_summary(qa),
        "evaluation": _evaluation_summary(evaluation, quality_gate),
        "readiness": _readiness_summary(readiness),
        "impact": _impact_summary(impact),
        "next_actions": _next_actions(qa, evaluation, readiness, quality_gate),
    }


def build_handoff_card(
    result: AnalysisResult,
    *,
    payload: dict[str, Any] | None = None,
    summary: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = payload or {}
    summary = summary or build_run_summary(result, payload=payload)
    qa = _payload_or_artifact(payload, "qa", result.output_dir / "qa_answer.json")
    evaluation = _payload_or_artifact(payload, "evaluation", result.output_dir / "evaluation.json")
    readiness = _payload_or_artifact(payload, "readiness", result.output_dir / "readiness.json")
    impact = _payload_or_artifact(payload, "impact", result.output_dir / "impact.json")
    orchestration = summary.get("orchestration", {})
    primary_task = _handoff_primary_task(summary, qa)
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": {
            "name": result.knowledge.repo_name,
            "root": result.knowledge.root,
            "output_dir": str(result.output_dir),
        },
        "status": summary.get("status"),
        "objective": _handoff_objective(summary),
        "recommended_action": {
            "command": orchestration.get("recommended_command"),
            "command_line": orchestration.get("recommended_command_line"),
            "reason": orchestration.get("why_recommended"),
            "repair_ready": orchestration.get("repair_ready"),
        },
        "primary_task": primary_task,
        "qa": _handoff_qa_summary(qa),
        "evaluation": _handoff_evaluation_summary(evaluation),
        "readiness": _handoff_readiness_summary(readiness),
        "impact": _handoff_impact_summary(impact),
        "reuse": {
            "can_reuse_for": orchestration.get("can_reuse_for", []),
            "blocked_by": orchestration.get("blocked_by", {}),
        },
        "artifacts": {
            "run_summary": str(result.output_dir / "run_summary.json"),
            "qa_answer": str(result.output_dir / "qa_answer.json"),
            "context_pack": str(result.output_dir / "context_pack.md"),
            "llm_prompt": str(result.output_dir / "llm_prompt.md"),
            "evaluation": str(result.output_dir / "evaluation.json"),
            "impact": str(result.output_dir / "impact.json"),
            "readiness": str(result.output_dir / "readiness.json"),
            "handoff_card": str(result.output_dir / "handoff_card.json"),
        },
        "next_actions": summary.get("next_actions", []),
    }


def verify_handoff_card(payload: Any) -> dict[str, Any]:
    detail: dict[str, Any] = {
        "schema_version": None,
        "missing_fields": [],
        "invalid_fields": [],
        "warnings": [],
    }
    if not isinstance(payload, dict):
        detail["invalid_fields"].append("root")
        return {"ok": False, "detail": detail}

    detail["schema_version"] = payload.get("schema_version")
    required_top_level = [
        "schema_version",
        "generated_at",
        "repo",
        "status",
        "objective",
        "recommended_action",
        "primary_task",
        "qa",
        "evaluation",
        "readiness",
        "impact",
        "reuse",
        "artifacts",
        "next_actions",
    ]
    for key in required_top_level:
        if key not in payload:
            detail["missing_fields"].append(key)

    repo = payload.get("repo")
    if not isinstance(repo, dict):
        detail["invalid_fields"].append("repo")
    else:
        for key in ["name", "root", "output_dir"]:
            if not repo.get(key):
                detail["missing_fields"].append(f"repo.{key}")

    recommended_action = payload.get("recommended_action")
    if not isinstance(recommended_action, dict):
        detail["invalid_fields"].append("recommended_action")
    else:
        if "command" not in recommended_action:
            detail["missing_fields"].append("recommended_action.command")
        if "reason" not in recommended_action:
            detail["missing_fields"].append("recommended_action.reason")

    primary_task = payload.get("primary_task")
    if not isinstance(primary_task, dict):
        detail["invalid_fields"].append("primary_task")
    else:
        for key in ["source", "id", "summary", "recommended_command"]:
            if key not in primary_task:
                detail["missing_fields"].append(f"primary_task.{key}")
        if primary_task.get("source") not in {"qa", "repair_plan", "summary"}:
            detail["invalid_fields"].append("primary_task.source")
        if "recommended_args" in primary_task and not isinstance(
            primary_task.get("recommended_args"), list
        ):
            detail["invalid_fields"].append("primary_task.recommended_args")
        if "recommended_command_line" in primary_task and not isinstance(
            primary_task.get("recommended_command_line"), str
        ):
            detail["invalid_fields"].append("primary_task.recommended_command_line")

    qa = payload.get("qa")
    if not isinstance(qa, dict):
        detail["invalid_fields"].append("qa")
    elif qa.get("available") and "question" not in qa:
        detail["missing_fields"].append("qa.question")

    reuse = payload.get("reuse")
    if not isinstance(reuse, dict):
        detail["invalid_fields"].append("reuse")
    else:
        if not isinstance(reuse.get("can_reuse_for", []), list):
            detail["invalid_fields"].append("reuse.can_reuse_for")
        if not isinstance(reuse.get("blocked_by", {}), dict):
            detail["invalid_fields"].append("reuse.blocked_by")

    artifacts = payload.get("artifacts")
    if not isinstance(artifacts, dict):
        detail["invalid_fields"].append("artifacts")
    else:
        for key in [
            "run_summary",
            "qa_answer",
            "context_pack",
            "llm_prompt",
            "evaluation",
            "impact",
            "readiness",
            "handoff_card",
        ]:
            if key not in artifacts:
                detail["missing_fields"].append(f"artifacts.{key}")

    next_actions = payload.get("next_actions")
    if not isinstance(next_actions, list):
        detail["invalid_fields"].append("next_actions")

    if detail["schema_version"] != HANDOFF_CARD_SCHEMA_VERSION:
        detail["invalid_fields"].append("schema_version")

    ok = not detail["missing_fields"] and not detail["invalid_fields"]
    return {"ok": ok, "detail": detail}


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


def _handoff_objective(summary: dict[str, Any]) -> str:
    orchestration = summary.get("orchestration", {})
    primary = orchestration.get("primary_repair_step")
    if isinstance(primary, dict):
        return str(primary.get("summary") or orchestration.get("why_recommended") or "")
    return str(orchestration.get("why_recommended") or "Inspect the repository outputs and continue the recommended workflow.")


def _handoff_primary_task(summary: dict[str, Any], qa: dict[str, Any]) -> dict[str, Any]:
    orchestration = summary.get("orchestration", {})
    qa_brief = qa.get("investigation_brief") if isinstance(qa, dict) else None
    if isinstance(qa_brief, dict):
        suggested_context_files = list(
            dict.fromkeys(
                [
                    *list(qa_brief.get("required_context_paths", []) or []),
                    *list(qa_brief.get("supporting_context_paths", []) or []),
                ]
            )
        )
        suggested_context_chars = int(
            qa.get("context_pack", {}).get("target_context_budget_chars", 0) or 0
        )
        qa_command_args = _ask_repair_args(
            Path(summary.get("repo", {}).get("output_dir") or ""),
            str(qa.get("question") or qa_brief.get("question") or "<question>"),
            suggested_context_files,
            context_chars=suggested_context_chars or None,
        )
        return {
            "source": "qa",
            "id": "qa_investigation",
            "category": "qa",
            "summary": str(qa.get("question") or "Continue QA investigation."),
            "detail": str(qa.get("llm_skip_reason") or "Use the QA investigation brief."),
            "recommended_command": "ask",
            "recommended_args": qa_command_args,
            "recommended_command_line": _command_line(qa_command_args),
            "investigation_brief": qa_brief,
        }
    primary = orchestration.get("primary_repair_step")
    if isinstance(primary, dict):
        return {
            "source": "repair_plan",
            "id": primary.get("id"),
            "category": primary.get("category"),
            "summary": primary.get("summary"),
            "detail": primary.get("detail"),
            "recommended_command": primary.get("recommended_command"),
            "recommended_args": list(primary.get("recommended_args", []) or []),
            "recommended_command_line": primary.get("recommended_command_line"),
            "investigation_brief": primary.get("investigation_brief"),
        }
    return {
        "source": "summary",
        "id": "follow_recommended_command",
        "category": "workflow",
        "summary": str(orchestration.get("why_recommended") or "Continue with the recommended command."),
        "detail": str(orchestration.get("why_recommended") or ""),
        "recommended_command": orchestration.get("recommended_command"),
        "recommended_args": list(orchestration.get("recommended_args", []) or []),
        "recommended_command_line": orchestration.get("recommended_command_line"),
        "investigation_brief": None,
    }


def _handoff_qa_summary(qa: dict[str, Any]) -> dict[str, Any]:
    if not qa:
        return {"available": False}
    return {
        "available": True,
        "question": qa.get("question"),
        "context_safe_for_llm": qa.get("context_safe_for_llm"),
        "required_context_paths": list(qa.get("required_context_paths", [])),
        "supporting_context_paths": list(qa.get("supporting_context_paths", [])),
        "reading_order": list(qa.get("reading_order", [])),
        "investigation_brief": qa.get("investigation_brief"),
    }


def _handoff_evaluation_summary(evaluation: dict[str, Any]) -> dict[str, Any]:
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
    }


def _handoff_readiness_summary(readiness: dict[str, Any]) -> dict[str, Any]:
    if not readiness:
        return {"available": False}
    return {
        "available": True,
        "passed": readiness.get("passed"),
        "errors": readiness.get("errors", 0),
        "warnings": readiness.get("warnings", 0),
    }


def _handoff_impact_summary(impact: dict[str, Any]) -> dict[str, Any]:
    if not impact:
        return {"available": False}
    return {
        "available": True,
        "input_paths": list(impact.get("input_paths", [])),
        "affected_files": list(impact.get("affected_files", [])),
    }


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


def _artifact_contract_summary(output_dir: Path) -> dict[str, Any]:
    items: dict[str, Any] = {}
    reusable_ready = True
    missing_required_for_present: dict[str, list[str]] = {}
    for name in SUMMARY_ARTIFACTS:
        contract = ARTIFACT_CONTRACTS.get(name)
        if not contract:
            continue
        path = output_dir / name
        exists = path.exists()
        required = list(contract.get("depends_on", []))
        optional = list(contract.get("optional_depends_on", []))
        missing_required = [dep for dep in required if not (output_dir / dep).exists()]
        missing_optional = [dep for dep in optional if not (output_dir / dep).exists()]
        if exists and missing_required:
            reusable_ready = False
            missing_required_for_present[name] = missing_required
        items[name] = {
            "exists": exists,
            "produced_by": list(contract.get("produced_by", [])),
            "depends_on": required,
            "optional_depends_on": optional,
            "missing_required_dependencies": missing_required,
            "missing_optional_dependencies": missing_optional,
            "reusable": bool(contract.get("reusable", False)),
            "dependency_contract_satisfied": not exists or not missing_required,
        }
    return {
        "reusable_ready": reusable_ready,
        "present_artifacts_with_missing_dependencies": missing_required_for_present,
        "items": items,
    }


def _orchestration_summary(
    output_dir: Path,
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
) -> dict[str, Any]:
    reuse = reuse_readiness_by_command(output_dir)
    recommended_command = _recommended_command(qa, evaluation, readiness, quality_gate)
    recommended_args = _recommended_args(recommended_command, output_dir)
    blocked_detail = reuse["details"].get(recommended_command, {})
    blocked_reason = reuse["blocked_by"].get(recommended_command, "")
    repair_plan = _repair_plan(
        output_dir,
        qa,
        evaluation,
        readiness,
        quality_gate,
    )
    return {
        "recommended_command": recommended_command,
        "recommended_args": recommended_args,
        "recommended_command_line": _command_line(recommended_args),
        "why_recommended": _recommended_reason(
            qa,
            evaluation,
            readiness,
            quality_gate,
            blocked_reason=blocked_reason,
        ),
        "recovery_commands": _recovery_commands(
            output_dir,
            recommended_command,
            blocked_detail=blocked_detail,
        ),
        "requires_fresh_analysis": _requires_fresh_analysis(blocked_detail),
        "repair_ready": bool(repair_plan),
        "blocking_issues_count": len(repair_plan),
        "primary_repair_step": repair_plan[0] if repair_plan else None,
        "repair_plan": repair_plan,
        "can_reuse_for": reuse["can_reuse_for"],
        "blocked_by": reuse["blocked_by"],
    }


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


def _recommended_command(
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
) -> str:
    if not qa:
        return "ask"
    if qa and not qa.get("context_safe_for_llm"):
        return "ask"
    if not evaluation:
        return "eval"
    if quality_gate and quality_gate.get("passed") is False:
        return "eval"
    if not readiness:
        return "ready"
    if readiness and not readiness.get("passed"):
        return "ready"
    return "none"


def _recommended_reason(
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
    *,
    blocked_reason: str,
) -> str:
    if not qa:
        reason = "No QA artifact is available yet, so prompt-ready source context has not been verified."
        return _append_blocked_reason(reason, blocked_reason)
    if qa and not qa.get("context_safe_for_llm"):
        reason = qa.get("llm_skip_reason") or (
            "The latest QA context is not safe for code reasoning because required source files are missing or incomplete."
        )
        return _append_blocked_reason(
            f"Latest QA context is not safe for LLM use: {reason}",
            blocked_reason,
        )
    if not evaluation:
        reason = "No evaluation artifact is available yet, so RAG and CodeGraph quality has not been scored."
        return _append_blocked_reason(reason, blocked_reason)
    if quality_gate and quality_gate.get("passed") is False:
        score = quality_gate.get("score")
        threshold = quality_gate.get("threshold")
        reason = (
            f"The latest evaluation quality gate failed (score={score}, threshold={threshold})."
        )
        return _append_blocked_reason(reason, blocked_reason)
    if not readiness:
        reason = "No readiness artifact is available yet, so the output has not passed the final competition gate."
        return _append_blocked_reason(reason, blocked_reason)
    if readiness and not readiness.get("passed"):
        reason = "The latest readiness gate did not pass, so the output still needs remediation."
        return _append_blocked_reason(reason, blocked_reason)
    return "All tracked QA, evaluation, and readiness checks are satisfied."


def _recommended_args(command: str, output_dir: Path) -> list[str]:
    if command == "ask":
        return ["--from-output", str(output_dir), "--ask", "<question>", "--json"]
    if command == "eval":
        return ["--from-output", str(output_dir), "--eval", "--json"]
    if command == "ready":
        return [
            "--from-output",
            str(output_dir),
            "--ready",
            "--ready-fail-under",
            "1.0",
            "--json",
        ]
    return []


def _command_line(args: list[str]) -> str:
    if not args:
        return ""
    return "python main.py " + " ".join(_quote_arg(arg) for arg in args)


def _quote_arg(value: str) -> str:
    if not value or any(char.isspace() for char in value):
        escaped = value.replace('"', '\\"')
        return f'"{escaped}"'
    return value


def _recovery_commands(
    output_dir: Path,
    recommended_command: str,
    *,
    blocked_detail: Any,
) -> list[str]:
    if recommended_command == "none":
        return []
    detail = blocked_detail if isinstance(blocked_detail, dict) else {}
    missing_roots = list(detail.get("missing_root_artifacts", []) or [])
    dependency_failures = detail.get("dependency_failures", {}) or {}
    commands: list[str] = []
    followup = _command_line(_recommended_args(recommended_command, output_dir))
    if missing_roots:
        commands.append("python main.py <repo-path> --out <output-root>")
        if followup:
            commands.append(followup)
        return list(dict.fromkeys(commands))
    if dependency_failures and followup:
        commands.append(followup)
        return commands
    if followup:
        commands.append(followup)
    return commands


def _requires_fresh_analysis(blocked_detail: Any) -> bool:
    detail = blocked_detail if isinstance(blocked_detail, dict) else {}
    return bool(detail.get("missing_root_artifacts"))


def _append_blocked_reason(reason: str, blocked_reason: str) -> str:
    if not blocked_reason:
        return reason
    return f"{reason} Reuse is currently blocked: {blocked_reason}"


def _repair_plan(
    output_dir: Path,
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    readiness: dict[str, Any],
    quality_gate: dict[str, Any],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    source_budget_by_path = _source_context_budget_lookup(output_dir)
    if not qa:
        issues.append(
            _repair_item(
                "qa_missing",
                category="qa",
                severity="warning",
                summary="Generate QA artifacts and prompt-ready source context.",
                detail="No QA artifact is available yet, so the source-context path has not been verified.",
                affected_commands=["ask", "ready"],
                recommended_command="ask",
                output_dir=output_dir,
            )
        )
    elif not qa.get("context_safe_for_llm"):
        focus_paths = _qa_focus_paths(qa)
        suggested_question = str(qa.get("question") or "Where is the missing repository evidence?")
        suggested_context_chars = _qa_suggested_context_chars(
            qa,
            focus_paths,
            source_budget_by_path,
        )
        issues.append(
            _repair_item(
                "qa_unsafe",
                category="qa",
                severity="error",
                summary="Regenerate QA with complete required and target source files.",
                detail=qa.get("llm_skip_reason")
                or "The latest QA payload is not safe for LLM reasoning.",
                affected_commands=["ask", "ready"],
                recommended_command="ask",
                output_dir=output_dir,
                extra={
                    "missing_required_context_paths": list(
                        qa.get("missing_required_context_paths", [])
                    ),
                    "missing_target_context_paths": list(
                        qa.get("missing_target_context_paths", [])
                    ),
                    "focus_paths": focus_paths,
                    "suggested_context_files": focus_paths,
                    "suggested_question": suggested_question,
                    "suggested_context_chars": suggested_context_chars,
                    "investigation_command_args": _ask_repair_args(
                        output_dir,
                        suggested_question,
                        focus_paths,
                        context_chars=suggested_context_chars,
                    ),
                    "investigation_command_line": _command_line(
                        _ask_repair_args(
                            output_dir,
                            suggested_question,
                            focus_paths,
                            context_chars=suggested_context_chars,
                        )
                    ),
                },
            )
        )
    if not evaluation:
        issues.append(
            _repair_item(
                "evaluation_missing",
                category="evaluation",
                severity="warning",
                summary="Run evaluation to score RAG and CodeGraph quality.",
                detail="No evaluation artifact is available yet.",
                affected_commands=["eval", "ready"],
                recommended_command="eval",
                output_dir=output_dir,
            )
        )
    elif quality_gate and quality_gate.get("passed") is False:
        failing_rag_cases = _failing_rag_cases(evaluation)
        failing_trace_cases = _failing_trace_cases(evaluation)
        focus_paths = _evaluation_focus_paths(evaluation)
        suggested_question = _evaluation_suggested_question(evaluation)
        suggested_context_chars = _focus_path_context_budget(
            focus_paths,
            source_budget_by_path,
        )
        issues.append(
            _repair_item(
                "evaluation_failed",
                category="evaluation",
                severity="error",
                summary="Improve failing retrieval or trace cases and rerun evaluation.",
                detail=(
                    f"Evaluation quality gate failed with score={quality_gate.get('score')} "
                    f"threshold={quality_gate.get('threshold')}."
                ),
                affected_commands=["eval", "ready"],
                recommended_command="eval",
                output_dir=output_dir,
                extra={
                    "score": quality_gate.get("score"),
                    "threshold": quality_gate.get("threshold"),
                    "failing_rag_cases": failing_rag_cases,
                    "failing_trace_cases": failing_trace_cases,
                    "focus_paths": focus_paths,
                    "suggested_context_files": focus_paths,
                    "suggested_question": suggested_question,
                    "suggested_context_chars": suggested_context_chars,
                    "investigation_command_args": _ask_repair_args(
                        output_dir,
                        suggested_question,
                        focus_paths,
                        context_chars=suggested_context_chars,
                    ),
                    "investigation_command_line": _command_line(
                        _ask_repair_args(
                            output_dir,
                            suggested_question,
                            focus_paths,
                            context_chars=suggested_context_chars,
                        )
                    ),
                },
            )
        )
    if not readiness:
        issues.append(
            _repair_item(
                "readiness_missing",
                category="readiness",
                severity="warning",
                summary="Run the readiness gate for a final delivery verdict.",
                detail="No readiness artifact is available yet.",
                affected_commands=["ready"],
                recommended_command="ready",
                output_dir=output_dir,
            )
        )
    elif not readiness.get("passed"):
        failing_checks = _failing_readiness_checks(readiness)
        focus_paths = _readiness_focus_paths(readiness, qa, evaluation)
        suggested_question = (
            _readiness_suggested_question(readiness, qa, evaluation)
        )
        suggested_context_chars = _readiness_suggested_context_chars(
            readiness,
            qa,
            evaluation,
            focus_paths,
            source_budget_by_path,
        )
        issues.append(
            _repair_item(
                "readiness_failed",
                category="readiness",
                severity="error",
                summary="Fix readiness errors and rerun the final gate.",
                detail="The latest readiness artifact did not pass all required checks.",
                affected_commands=["ready"],
                recommended_command="ready",
                output_dir=output_dir,
                extra={
                    "errors": readiness.get("errors", 0),
                    "warnings": readiness.get("warnings", 0),
                    "summary": readiness.get("summary", {}),
                    "failing_checks": failing_checks,
                    "focus_paths": focus_paths,
                    "suggested_context_files": focus_paths,
                    "suggested_question": suggested_question,
                    "suggested_context_chars": suggested_context_chars,
                    "investigation_command_args": _ask_repair_args(
                        output_dir,
                        suggested_question,
                        focus_paths,
                        context_chars=suggested_context_chars,
                    ),
                    "investigation_command_line": _command_line(
                        _ask_repair_args(
                            output_dir,
                            suggested_question,
                            focus_paths,
                            context_chars=suggested_context_chars,
                        )
                    ),
                },
            )
        )
    return issues


def _repair_item(
    issue_id: str,
    *,
    category: str,
    severity: str,
    summary: str,
    detail: str,
    affected_commands: list[str],
    recommended_command: str,
    output_dir: Path,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    args = _recommended_args(recommended_command, output_dir)
    merged_extra = dict(extra or {})
    investigation_brief = _repair_investigation_brief(
        issue_id,
        category=category,
        summary=summary,
        detail=detail,
        recommended_command=recommended_command,
        output_dir=output_dir,
        extra=merged_extra,
    )
    item = {
        "id": issue_id,
        "category": category,
        "severity": severity,
        "summary": summary,
        "detail": detail,
        "affected_commands": affected_commands,
        "recommended_command": recommended_command,
        "recommended_args": args,
        "recommended_command_line": _command_line(args),
        "requires_fresh_analysis": False,
        "investigation_brief": investigation_brief,
    }
    if merged_extra:
        item.update(merged_extra)
    return item


def _ask_repair_args(
    output_dir: Path,
    question: str,
    context_files: list[str],
    *,
    context_chars: int | None = None,
) -> list[str]:
    args = [
        "--from-output",
        str(output_dir),
        "--ask",
        question or "<question>",
        "--json",
    ]
    if context_chars and context_chars > 0:
        args.extend(["--context-chars", str(context_chars)])
    for path in context_files[:8]:
        args.extend(["--context-file", path])
    return args


def _repair_investigation_brief(
    issue_id: str,
    *,
    category: str,
    summary: str,
    detail: str,
    recommended_command: str,
    output_dir: Path,
    extra: dict[str, Any],
) -> dict[str, Any]:
    suggested_question = str(extra.get("suggested_question") or "")
    suggested_context_files = list(extra.get("suggested_context_files", []) or [])
    focus_paths = list(extra.get("focus_paths", []) or suggested_context_files)
    suggested_context_chars = int(extra.get("suggested_context_chars") or 0)
    failing_rag_cases = list(extra.get("failing_rag_cases", []) or [])
    failing_trace_cases = list(extra.get("failing_trace_cases", []) or [])
    failing_checks = list(extra.get("failing_checks", []) or [])
    reading_order = _repair_reading_order(
        focus_paths=focus_paths,
        suggested_context_files=suggested_context_files,
        failing_rag_cases=failing_rag_cases,
        failing_trace_cases=failing_trace_cases,
        failing_checks=failing_checks,
    )
    return {
        "issue_id": issue_id,
        "category": category,
        "summary": summary,
        "detail": detail,
        "question": suggested_question,
        "focus_paths": focus_paths,
        "suggested_context_files": suggested_context_files,
        "suggested_context_chars": suggested_context_chars,
        "recommended_command": recommended_command,
        "investigation_command_args": _ask_repair_args(
            output_dir,
            suggested_question,
            suggested_context_files,
            context_chars=suggested_context_chars or None,
        )
        if recommended_command == "ask" or suggested_question or suggested_context_files
        else [],
        "reading_order": reading_order,
        "guardrails": _repair_guardrails(
            category=category,
            focus_paths=focus_paths,
            failing_checks=failing_checks,
        ),
        "failure_evidence": {
            "failing_rag_cases": failing_rag_cases,
            "failing_trace_cases": failing_trace_cases,
            "failing_checks": failing_checks,
        },
    }


def _repair_reading_order(
    *,
    focus_paths: list[str],
    suggested_context_files: list[str],
    failing_rag_cases: list[dict[str, Any]],
    failing_trace_cases: list[dict[str, Any]],
    failing_checks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    expected_paths: list[str] = []
    for case in failing_rag_cases:
        expected_paths.extend(case.get("missing_expected_paths", []))
        expected_paths.extend(case.get("missing_prompt_context_paths", []))
        expected_paths.extend(case.get("missing_complete_file_paths", []))
    for case in failing_trace_cases:
        expected_paths.extend(case.get("expected_paths", []))
    check_related_paths: list[str] = []
    for check in failing_checks:
        detail = check.get("detail", {})
        if isinstance(detail, dict):
            for key in (
                "missing_required_context_paths",
                "missing_target_context_paths",
                "incomplete_required_context_paths",
                "incomplete_target_context_paths",
            ):
                check_related_paths.extend(detail.get(key, []) or [])
    expected = list(dict.fromkeys(expected_paths))
    check_paths = list(dict.fromkeys(check_related_paths))
    suggested = list(dict.fromkeys(suggested_context_files))
    focus = list(dict.fromkeys(focus_paths))
    return [
        {
            "priority": 1,
            "label": "expected_or_missing_evidence",
            "paths": expected,
        },
        {
            "priority": 2,
            "label": "suggested_context_files",
            "paths": [path for path in suggested if path not in expected],
        },
        {
            "priority": 3,
            "label": "check_related_paths",
            "paths": [path for path in check_paths if path not in expected and path not in suggested],
        },
        {
            "priority": 4,
            "label": "remaining_focus_paths",
            "paths": [
                path
                for path in focus
                if path not in expected and path not in suggested and path not in check_paths
            ],
        },
    ]


def _repair_guardrails(
    *,
    category: str,
    focus_paths: list[str],
    failing_checks: list[dict[str, Any]],
) -> list[str]:
    rules = [
        "Read priority 1 files before using any supporting or fallback files.",
        "Do not claim a fix until the failing evidence paths are present as real source context.",
        "Prefer exact source paths and line ranges over summaries when answering or debugging.",
    ]
    if category == "qa":
        rules.append("If required files remain missing or incomplete, keep the answer unresolved and expand context first.")
    if category == "evaluation":
        rules.append("Repair the failing evaluation cases directly; do not rely on unrelated green cases as proof.")
    if category == "readiness":
        rules.append("Clear every error-status readiness check before treating the output as competition-ready.")
    if failing_checks:
        check_names = ", ".join(str(check.get("name") or "") for check in failing_checks if check.get("name"))
        if check_names:
            rules.append(f"Prioritize the failing checks in this order of evidence pressure: {check_names}.")
    if not focus_paths:
        rules.append("If no concrete paths are listed, start by regenerating artifacts with the suggested command.")
    return rules


def _qa_focus_paths(qa: dict[str, Any]) -> list[str]:
    return list(
        dict.fromkeys(
            [
                *list(qa.get("missing_required_context_paths", [])),
                *list(qa.get("incomplete_required_context_paths", [])),
                *list(qa.get("missing_target_context_paths", [])),
                *list(qa.get("incomplete_target_context_paths", [])),
            ]
        )
    )


def _qa_suggested_context_chars(
    qa: dict[str, Any],
    focus_paths: list[str],
    source_budget_by_path: dict[str, int] | None = None,
) -> int:
    required_budget = int(qa.get("required_context_budget_chars") or 0)
    target_budget = int(qa.get("target_context_budget_chars") or 0)
    base = max(required_budget, target_budget, 12000)
    return _normalize_context_budget(
        max(base, _focus_path_context_budget(focus_paths, source_budget_by_path))
    )


def _failing_rag_cases(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    cases = evaluation.get("rag", [])
    if not isinstance(cases, list):
        return []
    failing: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        hit = bool(case.get("hit"))
        prompt_ok = bool(case.get("prompt_context_available"))
        complete_ok = bool(case.get("complete_file_context_available"))
        if hit and prompt_ok and complete_ok:
            continue
        failing.append(
            {
                "question": case.get("question"),
                "expected_paths": list(case.get("expected_paths", [])),
                "matched_paths": list(case.get("matched_paths", [])),
                "prompt_context_matched_paths": list(
                    case.get("prompt_context_matched_paths", [])
                ),
                "complete_file_matched_paths": list(
                    case.get("complete_file_matched_paths", [])
                ),
                "missing_expected_paths": [
                    path
                    for path in list(case.get("expected_paths", []))
                    if path not in list(case.get("matched_paths", []))
                ],
                "missing_prompt_context_paths": [
                    path
                    for path in list(case.get("expected_paths", []))
                    if path not in list(case.get("prompt_context_matched_paths", []))
                ],
                "missing_complete_file_paths": [
                    path
                    for path in list(case.get("expected_paths", []))
                    if path not in list(case.get("complete_file_matched_paths", []))
                ],
            }
        )
    return failing


def _failing_trace_cases(evaluation: dict[str, Any]) -> list[dict[str, Any]]:
    cases = evaluation.get("trace", [])
    if not isinstance(cases, list):
        return []
    failing: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict) or bool(case.get("hit")):
            continue
        failing.append(
            {
                "route": case.get("route"),
                "expected_paths": list(case.get("expected_paths", [])),
                "expected_names": list(case.get("expected_names", [])),
                "matched_paths": list(case.get("matched_paths", [])),
                "matched_names": list(case.get("matched_names", [])),
            }
        )
    return failing


def _evaluation_focus_paths(evaluation: dict[str, Any]) -> list[str]:
    paths: list[str] = []
    for case in _failing_rag_cases(evaluation):
        paths.extend(case.get("missing_prompt_context_paths", []))
        paths.extend(case.get("missing_complete_file_paths", []))
        paths.extend(case.get("missing_expected_paths", []))
    for case in _failing_trace_cases(evaluation):
        expected = list(case.get("expected_paths", []))
        matched = set(case.get("matched_paths", []))
        paths.extend(path for path in expected if path not in matched)
    return list(dict.fromkeys(paths))


def _evaluation_suggested_question(evaluation: dict[str, Any]) -> str:
    trace_cases = _failing_trace_cases(evaluation)
    if trace_cases:
        route = trace_cases[0].get("route")
        if route:
            return f"Explain the implementation chain for {route}"
    rag_cases = _failing_rag_cases(evaluation)
    if rag_cases:
        return str(rag_cases[0].get("question") or "Which files are missing from evaluation context?")
    return "Which files are blocking evaluation?"


def _failing_readiness_checks(readiness: dict[str, Any]) -> list[dict[str, Any]]:
    checks = readiness.get("checks", [])
    if not isinstance(checks, list):
        return []
    failing: list[dict[str, Any]] = []
    for check in checks:
        if not isinstance(check, dict):
            continue
        status = str(check.get("status") or "")
        if status not in {"error", "warning"}:
            continue
        failing.append(
            {
                "name": check.get("name"),
                "status": status,
                "message": check.get("message"),
                "detail": check.get("detail", {}),
            }
        )
    return failing


def _readiness_focus_paths(
    readiness: dict[str, Any],
    qa: dict[str, Any],
    evaluation: dict[str, Any],
) -> list[str]:
    paths: list[str] = []
    failing_checks = _failing_readiness_checks(readiness)
    names = {str(check.get("name") or "") for check in failing_checks}
    if "qa" in names:
        paths.extend(_qa_focus_paths(qa))
    if "evaluation" in names:
        paths.extend(_evaluation_focus_paths(evaluation))
    return list(dict.fromkeys(paths))


def _readiness_suggested_question(
    readiness: dict[str, Any],
    qa: dict[str, Any],
    evaluation: dict[str, Any],
) -> str:
    failing_checks = _failing_readiness_checks(readiness)
    names = {str(check.get("name") or "") for check in failing_checks}
    if "qa" in names and qa.get("question"):
        return str(qa.get("question"))
    if "evaluation" in names:
        return _evaluation_suggested_question(evaluation)
    return str(qa.get("question") or "Which files are blocking readiness?")


def _readiness_suggested_context_chars(
    readiness: dict[str, Any],
    qa: dict[str, Any],
    evaluation: dict[str, Any],
    focus_paths: list[str],
    source_budget_by_path: dict[str, int] | None = None,
) -> int:
    failing_checks = _failing_readiness_checks(readiness)
    names = {str(check.get("name") or "") for check in failing_checks}
    budgets = [_focus_path_context_budget(focus_paths, source_budget_by_path)]
    if "qa" in names:
        budgets.append(_qa_suggested_context_chars(qa, focus_paths, source_budget_by_path))
    if "evaluation" in names:
        budgets.append(
            _focus_path_context_budget(
                _evaluation_focus_paths(evaluation),
                source_budget_by_path,
            )
        )
    return _normalize_context_budget(max(budgets or [12000]))


def _focus_path_context_budget(
    paths: list[str],
    source_budget_by_path: dict[str, int] | None = None,
) -> int:
    unique_paths = list(dict.fromkeys(paths))
    count = len(unique_paths)
    if count <= 0:
        return 12000
    known_total = 0
    missing_count = 0
    budgets = source_budget_by_path or {}
    for path in unique_paths:
        budget = int(budgets.get(path) or 0)
        if budget > 0:
            known_total += budget
        else:
            missing_count += 1
    heuristic_total = 12000 + max(0, missing_count - 1) * 6000 if missing_count else 0
    return _normalize_context_budget(max(known_total + heuristic_total, 12000))


def _source_context_budget_lookup(output_dir: Path) -> dict[str, int]:
    rag_index_path = output_dir / "rag_index.json"
    if not rag_index_path.exists():
        return {}
    try:
        index = load_rag_index(rag_index_path)
    except ArtifactLoadError:
        return {}

    file_metadata: dict[str, dict[str, Any]] = {}
    source_lines_by_path: dict[str, dict[int, str]] = {}
    source_meta_by_path: dict[str, dict[str, Any]] = {}
    for chunk in index.chunks:
        if not chunk.path:
            continue
        if chunk.kind == "file":
            file_metadata[chunk.path] = dict(chunk.metadata)
            continue
        if chunk.kind != "source":
            continue
        source_lines = source_lines_by_path.setdefault(chunk.path, {})
        for line_no, line in _source_code_lines(chunk.text):
            source_lines.setdefault(line_no, line)
        metadata = source_meta_by_path.setdefault(chunk.path, {})
        language = str(chunk.metadata.get("language") or "")
        if language and not metadata.get("language"):
            metadata["language"] = language
        start_line = _line_value(chunk.metadata.get("start_line"), chunk.line)
        if start_line is not None:
            current = _line_value(metadata.get("start_line"))
            metadata["start_line"] = start_line if current is None else min(current, start_line)
        end_line = _line_value(chunk.metadata.get("end_line"), start_line)
        if end_line is not None:
            current = _line_value(metadata.get("end_line"))
            metadata["end_line"] = end_line if current is None else max(current, end_line)

    budgets: dict[str, int] = {}
    for path, numbered_lines in source_lines_by_path.items():
        if not numbered_lines:
            continue
        start_line = min(numbered_lines)
        end_line = max(numbered_lines)
        file_chunk_meta = file_metadata.get(path, {})
        source_meta = source_meta_by_path.get(path, {})
        total_lines = _line_value(file_chunk_meta.get("lines"), end_line)
        language = (
            str(source_meta.get("language") or "")
            or str(file_chunk_meta.get("language") or "")
            or "unknown"
        )
        contiguous = len(numbered_lines) == end_line - start_line + 1
        complete_file = start_line == 1 and contiguous and (not total_lines or end_line >= total_lines)
        text = "\n".join(
            [
                f"Source file: {path}",
                f"Language: {language}",
                f"Line range: {start_line}-{end_line}",
                f"Complete file: {'yes' if complete_file else 'no'}",
                "Code:",
                *[f"{line_no}: {numbered_lines[line_no]}" for line_no in sorted(numbered_lines)],
            ]
        )
        budgets[path] = len(text) + 260
    return budgets


def _source_code_lines(text: str) -> list[tuple[int, str]]:
    lines = text.splitlines()
    try:
        code_start = lines.index("Code:") + 1
    except ValueError:
        code_start = 0
    parsed: list[tuple[int, str]] = []
    for line in lines[code_start:]:
        match = re.match(r"^(\d+): ?(.*)$", line)
        if match:
            parsed.append((int(match.group(1)), match.group(2)))
    return parsed


def _line_value(value: object, fallback: int | None = None) -> int | None:
    if value is None:
        return fallback
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _normalize_context_budget(value: int) -> int:
    minimum = 12000
    maximum = 96000
    bounded = max(minimum, min(maximum, int(value)))
    rounded = ((bounded + 999) // 1000) * 1000
    return min(maximum, max(minimum, rounded))
