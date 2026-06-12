from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from aegis.artifacts import ArtifactLoadError, load_analysis_result, load_rag_index
from aegis.config import AegisConfig, LLMConfig, load_env_file
from aegis.doctor import Doctor
from aegis.evaluation import Evaluator, builtin_suite, load_suite
from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.llm import LLMClient
from aegis.manifest import (
    contract_targets_for_command,
    TRACKED_ARTIFACTS,
    build_manifest,
    format_artifact_contract_errors,
    format_manifest_integrity_errors,
    reuse_readiness_by_command,
    verify_artifact_contracts,
    verify_manifest_integrity,
)
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGIndexBuilder
from aegis.rag.qa import QAAnswer, RepositoryQAAgent
from aegis.rag.retriever import RetrievalResult
from aegis.readiness import ReadinessAssessor
from aegis.server import serve
from aegis.summary import (
    build_handoff_card,
    build_run_summary,
    stabilize_manifest_and_summary,
    verify_handoff_card,
    write_run_summary,
)
from aegis.utils import write_json


def parse_args() -> argparse.Namespace:
    load_env_file(".env")
    config = AegisConfig.from_env()
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="AEGIS: multi-agent repository reading and analysis.",
    )
    parser.add_argument("repo", nargs="?", default=config.repo_path, help="Local repository path")
    parser.add_argument("--out", default=config.output_dir, help="Output root, default: output/aegis")
    parser.add_argument("--from-output", help="Load existing output/aegis/<repo> artifacts and skip scanning")
    parser.add_argument("--max-files", type=int, default=config.max_files, help="Maximum files to scan")
    parser.add_argument("--include", action="append", default=list(config.include or []), help="Include glob; repeatable")
    parser.add_argument("--exclude", action="append", default=list(config.exclude or []), help="Exclude glob; repeatable")
    parser.add_argument("--no-cache", action="store_true", default=not config.use_cache, help="Disable parser cache")
    parser.add_argument("--llm", action="store_true", default=bool(config.llm and config.llm.enabled), help="Enable optional LLM synthesis")
    parser.add_argument(
        "--serve",
        nargs="?",
        const=config.serve_dir or config.output_dir,
        default=None,
        help="Serve a report directory over HTTP",
    )
    parser.add_argument("--host", default=config.serve_host, help="Report server host")
    parser.add_argument("--port", type=int, default=config.serve_port, help="Report server port")
    parser.add_argument("--doctor", action="store_true", help="Run environment and configuration checks")
    parser.add_argument("--trace-interface", help="Trace an interface route, for example /users")
    parser.add_argument("--impact", action="store_true", help="Run CodeGraph impact analysis for changed files")
    parser.add_argument(
        "--impact-file",
        action="append",
        default=[],
        help="Changed file path to analyze; repeatable. Defaults to git diff changed files.",
    )
    parser.add_argument("--impact-depth", type=int, default=3, help="Maximum reverse CodeGraph depth for --impact")
    parser.add_argument("--ask", help="Ask the repository with the RAG agent")
    parser.add_argument("--top-k", type=int, default=8, help="Number of RAG retrieval results")
    parser.add_argument(
        "--context-file",
        action="append",
        default=[],
        help="Force a repository file into the RAG prompt context; repeatable",
    )
    parser.add_argument(
        "--context-chars",
        type=int,
        default=config.rag_context_chars,
        help="RAG context pack character budget passed to LLM and JSON payloads",
    )
    parser.add_argument("--eval", action="store_true", help="Run built-in or custom RAG/CodeGraph evaluation")
    parser.add_argument("--eval-suite", help="Evaluation suite JSON file")
    parser.add_argument("--eval-fail-under", type=float, help="Fail when overall_score is below this 0..1 threshold")
    parser.add_argument("--ready", action="store_true", help="Run readiness checks and write readiness.json")
    parser.add_argument("--ready-fail-under", type=float, default=0.75, help="Readiness evaluation score threshold")
    parser.add_argument("--ready-ask", help="Run an ask smoke question before readiness and verify QA artifacts")
    parser.add_argument("--status", action="store_true", help="Inspect output status, reuse gates, and handoff summary")
    parser.add_argument("--handoff", action="store_true", help="Return the unified handoff card plus minimal trust metadata")
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON")
    return parser.parse_args()


def output_paths(output_dir: Path) -> dict[str, str]:
    return {
        "output_dir": str(output_dir),
        "report": str(output_dir / "report.md"),
        "html": str(output_dir / "report.html"),
        "mermaid": str(output_dir / "architecture.mmd"),
        "knowledge": str(output_dir / "knowledge.json"),
        "findings": str(output_dir / "findings.json"),
        "rag_index": str(output_dir / "rag_index.json"),
        "evaluation": str(output_dir / "evaluation.json"),
        "impact": str(output_dir / "impact.json"),
        "readiness": str(output_dir / "readiness.json"),
        "manifest": str(output_dir / "manifest.json"),
        "qa_answer": str(output_dir / "qa_answer.json"),
        "context_pack": str(output_dir / "context_pack.md"),
        "llm_prompt": str(output_dir / "llm_prompt.md"),
        "run_summary": str(output_dir / "run_summary.json"),
        "handoff_card": str(output_dir / "handoff_card.json"),
    }


def result_payload(result: Any) -> dict[str, Any]:
    return {
        "repo": result.knowledge.repo_name,
        "root": result.knowledge.root,
        "outputs": output_paths(result.output_dir),
        "stats": result.knowledge.stats,
        "frameworks": result.knowledge.frameworks,
        "entrypoints": result.knowledge.entrypoints,
        "configs": result.knowledge.configs,
        "findings_count": len(result.findings),
    }


def trace_payload(route: str, trace: list[Any]) -> dict[str, Any]:
    return {
        "route": route,
        "nodes": [
            {
                "id": node.id,
                "kind": node.kind,
                "name": node.name,
                "path": node.path,
                "line": node.line,
                "language": node.language,
                "metadata": node.metadata,
            }
            for node in trace
        ],
    }


def node_payload(node: Any) -> dict[str, Any]:
    return {
        "id": node.id,
        "kind": node.kind,
        "name": node.name,
        "path": node.path,
        "line": node.line,
        "language": node.language,
        "metadata": node.metadata,
    }


def impact_payload(paths: list[str], impact: list[Any], *, depth: int, source: str) -> dict[str, Any]:
    affected_files = list(dict.fromkeys(node.path for node in impact if node.path))
    affected_symbols = [
        {
            "kind": node.kind,
            "name": node.name,
            "path": node.path,
            "line": node.line,
        }
        for node in impact
        if node.kind in {"class", "function", "interface", "data_model"}
    ]
    return {
        "source": source,
        "depth": depth,
        "input_paths": paths,
        "affected_files": affected_files,
        "affected_symbols": affected_symbols,
        "nodes": [node_payload(node) for node in impact],
    }


def retrieval_payload(agent: RepositoryQAAgent, result: RetrievalResult) -> dict[str, Any]:
    chunk = result.chunk
    source = chunk if chunk.kind == "source" else agent.retriever.source_companion(chunk)
    return {
        "score": result.score,
        "matched_terms": result.matched_terms,
        "chunk": {
            "id": chunk.id,
            "kind": chunk.kind,
            "title": chunk.title,
            "path": chunk.path,
            "line": chunk.line,
            "metadata": chunk.metadata,
            "evidence": [asdict(item) for item in chunk.evidence],
        },
        "source_excerpt": (
            RepositoryQAAgent._source_excerpt(source, focus_line=chunk.line)
            if source
            else []
        ),
    }


def qa_payload(agent: RepositoryQAAgent, answer: QAAnswer) -> dict[str, Any]:
    return {
        "question": answer.question,
        "answer": answer.answer,
        "used_llm": answer.used_llm,
        "context_safe_for_llm": answer.context_safe_for_llm,
        "llm_skip_reason": answer.llm_skip_reason,
        "graph_context": answer.graph_context,
        "investigation_brief": answer.investigation_brief,
        "required_context_paths": answer.required_context_paths,
        "target_context_paths": answer.context_pack.target_context_paths or [],
        "supporting_context_paths": answer.context_pack.supporting_context_paths(),
        "reading_order": answer.context_pack.reading_order(),
        "source_paths": answer.context_pack.source_paths(),
        "complete_file_paths": answer.context_pack.complete_file_paths(),
        "missing_required_context_paths": answer.context_pack.missing_required_context_paths(),
        "incomplete_required_context_paths": answer.context_pack.incomplete_required_context_paths(),
        "unsatisfied_required_context_paths": answer.context_pack.unsatisfied_required_context_paths(),
        "required_context_satisfied": not answer.context_pack.unsatisfied_required_context_paths(),
        "missing_target_context_paths": answer.context_pack.missing_target_context_paths(),
        "incomplete_target_context_paths": answer.context_pack.incomplete_target_context_paths(),
        "unsatisfied_target_context_paths": answer.context_pack.unsatisfied_target_context_paths(),
        "target_context_satisfied": not answer.context_pack.unsatisfied_target_context_paths(),
        "source_context_satisfied": answer.context_pack.source_context_satisfied(),
        "complete_file_context_satisfied": answer.context_pack.complete_file_context_satisfied(),
        "context_pack": answer.context_pack.to_dict(),
        "llm_prompt": {
            "system": answer.llm_system_prompt,
            "user": answer.llm_user_prompt,
        },
        "results": [retrieval_payload(agent, item) for item in answer.results],
    }


def write_qa_artifacts(output_dir: Path, qa_data: dict[str, Any], answer: QAAnswer) -> None:
    write_json(output_dir / "qa_answer.json", qa_data)
    (output_dir / "context_pack.md").write_text(render_qa_context_markdown(answer), encoding="utf-8")
    (output_dir / "llm_prompt.md").write_text(render_llm_prompt_markdown(answer), encoding="utf-8")


def render_qa_context_markdown(answer: QAAnswer) -> str:
    lines = [
        f"# AEGIS QA Context Pack",
        "",
        f"Question: {answer.question}",
        "",
    ]
    if answer.graph_context:
        lines.extend(
            [
                "## CodeGraph Context",
                "",
                f"Route: {answer.graph_context.get('route', '')}",
                "",
            ]
        )
        nodes = answer.graph_context.get("nodes") or []
        if nodes:
            for idx, node in enumerate(nodes, start=1):
                location = ""
                if node.get("path") and node.get("line"):
                    location = f" ({node['path']}:{node['line']})"
                elif node.get("path"):
                    location = f" ({node['path']})"
                lines.append(f"{idx}. `{node['kind']}` {node['name']}{location}")
        else:
            lines.append("No CodeGraph trace nodes were found.")
        lines.append("")
    if answer.investigation_brief:
        lines.extend(
            [
                "## Investigation Brief",
                "",
                "```text",
                RepositoryQAAgent._render_investigation_brief(answer.investigation_brief),
                "```",
                "",
            ]
        )
    lines.extend(
        [
            "## Source Context",
            "",
            "```text",
            answer.context_pack.render(),
            "```",
            "",
        ]
    )
    return "\n".join(lines)


def render_llm_prompt_markdown(answer: QAAnswer) -> str:
    return "\n".join(
        [
            "# AEGIS QA LLM Prompt",
            "",
            f"Question: {answer.question}",
            f"Used LLM: {str(answer.used_llm).lower()}",
            f"Context safe for LLM: {str(answer.context_safe_for_llm).lower()}",
            f"LLM skip reason: {answer.llm_skip_reason or 'none'}",
            f"Required context paths: {', '.join(answer.required_context_paths) or 'none'}",
            f"Target context paths: {', '.join(answer.context_pack.target_context_paths or []) or 'none'}",
            f"Supporting context paths: {', '.join(answer.context_pack.supporting_context_paths()) or 'none'}",
            "Missing required context paths: "
            f"{', '.join(answer.context_pack.missing_required_context_paths()) or 'none'}",
            "Incomplete required context paths: "
            f"{', '.join(answer.context_pack.incomplete_required_context_paths()) or 'none'}",
            "Unsatisfied required context paths: "
            f"{', '.join(answer.context_pack.unsatisfied_required_context_paths()) or 'none'}",
            "Required context satisfied: "
            f"{str(not answer.context_pack.unsatisfied_required_context_paths()).lower()}",
            "Missing target context paths: "
            f"{', '.join(answer.context_pack.missing_target_context_paths()) or 'none'}",
            "Incomplete target context paths: "
            f"{', '.join(answer.context_pack.incomplete_target_context_paths()) or 'none'}",
            "Unsatisfied target context paths: "
            f"{', '.join(answer.context_pack.unsatisfied_target_context_paths()) or 'none'}",
            "Target context satisfied: "
            f"{str(not answer.context_pack.unsatisfied_target_context_paths()).lower()}",
            "Source context satisfied: "
            f"{str(answer.context_pack.source_context_satisfied()).lower()}",
            "Complete-file context satisfied: "
            f"{str(answer.context_pack.complete_file_context_satisfied()).lower()}",
            f"Files in context: {', '.join(answer.context_pack.source_paths()) or 'none'}",
            f"Complete files in context: {', '.join(answer.context_pack.complete_file_paths()) or 'none'}",
            "",
            "## Investigation Brief",
            "",
            "```text",
            RepositoryQAAgent._render_investigation_brief(answer.investigation_brief),
            "```",
            "",
            "## System Prompt",
            "",
            "```text",
            answer.llm_system_prompt,
            "```",
            "",
            "## User Prompt",
            "",
            "```text",
            answer.llm_user_prompt,
            "```",
            "",
        ]
    )


def get_rag_index(result: Any, *, prefer_saved: bool) -> Any:
    rag_index_path = result.output_dir / "rag_index.json"
    if prefer_saved:
        try:
            return load_rag_index(rag_index_path)
        except ArtifactLoadError as exc:
            raise SystemExit(str(exc)) from exc
    return RAGIndexBuilder(result.knowledge).build()


def refresh_manifest(result: Any, args: argparse.Namespace) -> None:
    run_config = analysis_run_manifest(result, args)
    return build_manifest(
        result,
        max_files=run_config["max_files"],
        include=run_config["include"],
        exclude=run_config["exclude"],
        use_cache=run_config["use_cache"],
        llm_enabled=run_config["llm_enabled"],
        events_count=run_config["events_count"],
        post_run=post_run_manifest(args),
    )


def analysis_run_manifest(result: Any, args: argparse.Namespace) -> dict[str, Any]:
    current = {
        "max_files": args.max_files,
        "include": list(args.include or []),
        "exclude": list(args.exclude or []),
        "use_cache": not args.no_cache,
        "llm_enabled": bool(args.llm),
        "events_count": _events_count(result.output_dir),
    }
    if not args.from_output:
        return current
    try:
        raw = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return current
    previous_run = raw.get("run", {}) if isinstance(raw, dict) else {}
    if not isinstance(previous_run, dict):
        return current
    return {
        "max_files": int(previous_run.get("max_files", current["max_files"])),
        "include": list(previous_run.get("include", current["include"]) or []),
        "exclude": list(previous_run.get("exclude", current["exclude"]) or []),
        "use_cache": bool(previous_run.get("use_cache", current["use_cache"])),
        "llm_enabled": bool(previous_run.get("llm_enabled", current["llm_enabled"])),
        "events_count": int(previous_run.get("events_count", current["events_count"])),
    }


def post_run_manifest(args: argparse.Namespace) -> dict[str, Any]:
    return {
        "from_output": args.from_output,
        "status": bool(args.status),
        "ask": args.ask,
        "ready_ask": args.ready_ask,
        "top_k": args.top_k,
        "context_chars": args.context_chars,
        "context_files": list(args.context_file or []),
        "trace_interface": args.trace_interface,
        "impact": bool(args.impact or args.impact_file),
        "impact_files": list(args.impact_file or []),
        "impact_depth": args.impact_depth,
        "eval": bool(args.eval or args.eval_suite or args.eval_fail_under is not None or args.ready),
        "eval_suite": args.eval_suite,
        "eval_fail_under": args.eval_fail_under,
        "ready": bool(args.ready),
        "ready_fail_under": args.ready_fail_under,
    }


def _events_count(output_dir: Path) -> int:
    events_path = output_dir / "events.json"
    if not events_path.exists():
        return 0
    try:
        data = json.loads(events_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return 0
    return len(data) if isinstance(data, list) else 0


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=True, indent=2))


def print_doctor(payload: dict[str, Any]) -> None:
    print("AEGIS doctor:")
    for check in payload["checks"]:
        print(f"- {check['status']}: {check['name']} - {check['message']}")
    print(
        "Result: "
        f"{'passed' if payload['passed'] else 'failed'} "
        f"({payload['errors']} errors, {payload['warnings']} warnings)"
    )


def metric_ratio(hits: int, total: int, rate: float) -> str:
    if total <= 0:
        return "n/a"
    return f"{hits}/{total} ({rate:.2%})"


def quality_gate_payload(metrics: dict[str, Any], threshold: float | None) -> dict[str, Any]:
    score = float(metrics.get("overall_score", 0.0))
    passed = threshold is None or score >= threshold
    return {
        "threshold": threshold,
        "score": score,
        "passed": passed,
    }


def _action_flags_present(args: argparse.Namespace) -> bool:
    return bool(
        args.trace_interface
        or args.impact
        or args.impact_file
        or args.ask
        or args.eval
        or args.eval_suite
        or args.eval_fail_under is not None
        or args.ready
        or args.ready_ask
    )


def command_artifact_contracts(args: argparse.Namespace) -> dict[str, list[str]]:
    ask_question = args.ask or args.ready_ask
    should_eval = bool(args.eval or args.eval_suite or args.eval_fail_under is not None or args.ready)
    should_impact = bool(args.impact or args.impact_file)
    command_names: list[str] = []
    if args.trace_interface:
        command_names.append("trace")
    if should_impact:
        command_names.append("impact")
    if ask_question:
        command_names.append("ask")
    if should_eval and not args.ready:
        command_names.append("eval")
    if args.ready:
        command_names.append("ready")
    required_roots: list[str] = []
    related_artifacts: list[str] = []
    for command_name in command_names:
        targets = contract_targets_for_command(command_name)
        required_roots.extend(targets["required_roots"])
        related_artifacts.extend(targets["related_artifacts"])
    return {
        "required_roots": list(dict.fromkeys(required_roots or ["knowledge.json"])),
        "related_artifacts": [name for name in dict.fromkeys(related_artifacts or ["run_summary.json"]) if name in TRACKED_ARTIFACTS],
    }


def _recovery_commands_from_message(message: str) -> list[str]:
    commands: list[str] = []
    lowered = message.lower()
    if "qa_answer.json" in lowered or "context_pack.md" in lowered or "llm_prompt.md" in lowered:
        commands.append("python main.py --from-output <output-dir> --ask \"<question>\" --json")
    if "evaluation.json" in lowered:
        commands.append("python main.py --from-output <output-dir> --eval --json")
    if "readiness.json" in lowered:
        commands.append("python main.py --from-output <output-dir> --ready --ready-fail-under 1.0 --json")
    if "impact.json" in lowered:
        commands.append("python main.py --from-output <output-dir> --impact --impact-file <path> --json")
    if "knowledge.json" in lowered or "rag_index.json" in lowered or "manifest.json" in lowered:
        commands.append("python main.py <repo-path> --out <output-root>")
    if not commands:
        commands.append("python main.py <repo-path> --out <output-root>")
    return list(dict.fromkeys(commands))


def _artifact_recovery_suffix(result: Any, message: str) -> str:
    reuse = reuse_readiness_by_command(result.output_dir)
    lines = []
    if reuse["blocked_by"]:
        lines.append("Blocked commands:")
        for command, reason in reuse["blocked_by"].items():
            lines.append(f"- {command}: {reason}")
    commands = _recovery_commands_from_message(message)
    if commands:
        lines.append("Suggested recovery commands:")
        lines.extend(f"- {command}" for command in commands)
    return ("\n" + "\n".join(lines)) if lines else ""


def _read_json_object_if_possible(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    detail = {
        "path": str(path),
        "exists": path.exists(),
        "readable": False,
        "trusted": False,
        "source_used": "missing",
        "error": None,
    }
    if not path.exists():
        return {}, detail
    try:
        value = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        detail["error"] = str(exc)
        return {}, detail
    if not isinstance(value, dict):
        detail["error"] = "artifact is not a JSON object"
        return {}, detail
    detail["readable"] = True
    detail["source_used"] = "artifact"
    return value, detail


def status_report_payload(
    result: Any,
    *,
    manifest_check: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    manifest_check = manifest_check or verify_manifest_integrity(
        result.output_dir,
        repo_name=result.knowledge.repo_name,
        validate_present_tracked=True,
    )
    summary_artifact, summary_detail = _read_json_object_if_possible(
        result.output_dir / "run_summary.json"
    )
    summary_trusted = bool(summary_detail["readable"] and manifest_check.get("ok"))
    summary_detail["trusted"] = summary_trusted
    if summary_trusted:
        summary = summary_artifact
    else:
        summary = build_run_summary(result)
        summary_detail["source_used"] = "rebuilt"
    handoff_artifact, handoff_detail = _read_json_object_if_possible(
        result.output_dir / "handoff_card.json"
    )
    handoff_validation = verify_handoff_card(handoff_artifact) if handoff_artifact else {"ok": False, "detail": {"schema_version": None, "missing_fields": ["root"], "invalid_fields": [], "warnings": []}}
    handoff_trusted = bool(
        handoff_detail["readable"]
        and manifest_check.get("ok")
        and handoff_validation.get("ok")
    )
    handoff_detail["trusted"] = handoff_trusted
    if handoff_trusted:
        handoff_card = handoff_artifact
    else:
        handoff_card = build_handoff_card(result, summary=summary)
        handoff_detail["source_used"] = "rebuilt"
        handoff_validation = verify_handoff_card(handoff_card)
    reuse = reuse_readiness_by_command(result.output_dir)
    report = {
        "ok": bool(manifest_check.get("ok")),
        "output_dir": str(result.output_dir),
        "manifest_integrity": manifest_check,
        "run_summary_artifact": summary_detail,
        "handoff_card_artifact": handoff_detail,
        "handoff_card_validation": handoff_validation,
        "artifact_contracts": summary.get("artifact_contracts", {}),
        "repair_plan": summary.get("orchestration", {}).get("repair_plan", []),
        "primary_repair_step": summary.get("orchestration", {}).get("primary_repair_step"),
        "reuse_by_command": {
            "can_reuse_for": reuse.get("can_reuse_for", []),
            "blocked_by": reuse.get("blocked_by", {}),
            "details": reuse.get("details", {}),
        },
    }
    return summary, report, handoff_card


def print_status(payload: dict[str, Any]) -> None:
    summary = payload.get("summary", {})
    orchestration = payload.get("orchestration", {})
    status_report = payload.get("status_report", {})
    print("AEGIS status:")
    print(f"- repo: {payload.get('repo')}")
    print(f"- output dir: {payload.get('outputs', {}).get('output_dir', '')}")
    print(f"- summary status: {summary.get('status', 'unknown')}")
    print(
        "- manifest integrity: "
        f"{'ok' if status_report.get('manifest_integrity', {}).get('ok') else 'failed'}"
    )
    artifact_detail = status_report.get("run_summary_artifact", {})
    print(
        "- run summary source: "
        f"{artifact_detail.get('source_used', 'missing')}"
        f" (trusted={str(bool(artifact_detail.get('trusted'))).lower()})"
    )
    print(
        f"- recommended command: {orchestration.get('recommended_command', 'none')}"
    )
    if orchestration.get("recommended_command_line"):
        print(f"- recommended command line: {orchestration['recommended_command_line']}")
    can_reuse = status_report.get("reuse_by_command", {}).get("can_reuse_for", [])
    print(f"- reusable commands: {', '.join(can_reuse) if can_reuse else 'none'}")
    blocked = status_report.get("reuse_by_command", {}).get("blocked_by", {})
    if blocked:
        print("- blocked commands:")
        for command, reason in blocked.items():
            print(f"  - {command}: {reason}")
    primary_repair = status_report.get("primary_repair_step")
    if isinstance(primary_repair, dict):
        print(
            "- primary repair step: "
            f"{primary_repair.get('summary', '')}"
        )
        if primary_repair.get("recommended_command_line"):
            print(f"  - command: {primary_repair['recommended_command_line']}")
    next_actions = summary.get("next_actions", [])
    if next_actions:
        print("- next actions:")
        for item in next_actions:
            print(f"  - {item}")


def print_handoff(payload: dict[str, Any]) -> None:
    handoff = payload.get("handoff_card", {})
    status_report = payload.get("status_report", {})
    primary = handoff.get("primary_task", {})
    action = handoff.get("recommended_action", {})
    print("AEGIS handoff:")
    print(f"- repo: {payload.get('repo')}")
    print(f"- status: {handoff.get('status', 'unknown')}")
    print(
        "- handoff trusted: "
        f"{str(bool(status_report.get('handoff_card_artifact', {}).get('trusted'))).lower()}"
    )
    print(f"- recommended command: {action.get('command', 'none')}")
    if action.get("command_line"):
        print(f"- recommended command line: {action['command_line']}")
    print(f"- primary task: {primary.get('summary', '')}")
    if primary.get("recommended_command_line"):
        print(f"- primary task command: {primary['recommended_command_line']}")
    brief = primary.get("investigation_brief")
    if isinstance(brief, dict):
        reading_order = brief.get("reading_order", [])
        if reading_order:
            first = reading_order[0]
            print(
                "- first reading bucket: "
                f"{first.get('label', '')} -> {', '.join(first.get('paths', [])) or 'none'}"
            )


def main() -> int:
    args = parse_args()
    if args.eval_fail_under is not None and not 0 <= args.eval_fail_under <= 1:
        raise SystemExit("--eval-fail-under must be between 0 and 1, for example 0.85")
    if not 0 <= args.ready_fail_under <= 1:
        raise SystemExit("--ready-fail-under must be between 0 and 1, for example 0.85")
    if args.context_chars <= 0:
        raise SystemExit("--context-chars must be a positive integer")
    if args.impact_depth < 0:
        raise SystemExit("--impact-depth must be zero or greater")
    if args.ready_ask and not args.ready:
        raise SystemExit("--ready-ask requires --ready")
    if (args.status or args.handoff) and _action_flags_present(args):
        raise SystemExit("--status/--handoff cannot be combined with ask/trace/impact/eval/ready actions")
    if args.serve:
        serve(Path(args.serve), host=args.host, port=args.port)
        return 0
    if args.doctor:
        repo = Path(args.repo) if args.repo else None
        payload = Doctor(
            repo=repo,
            output_root=Path(args.out),
            llm_config=LLMConfig.from_env(enabled=args.llm),
        ).run()
        if args.json:
            print_json({"doctor": payload})
        else:
            print_doctor(payload)
        return 0 if payload["passed"] else 2

    if args.from_output:
        try:
            result = load_analysis_result(Path(args.from_output))
            if not args.status and not args.handoff:
                manifest_check = verify_manifest_integrity(
                    result.output_dir,
                    repo_name=result.knowledge.repo_name,
                    validate_present_tracked=True,
                )
                if not manifest_check["ok"]:
                    raise ArtifactLoadError(
                        "Manifest integrity check failed for --from-output: "
                        + format_manifest_integrity_errors(manifest_check)
                        + _artifact_recovery_suffix(
                            result,
                            format_manifest_integrity_errors(manifest_check),
                        )
                    )
                contract_targets = command_artifact_contracts(args)
                contract_check = verify_artifact_contracts(
                    result.output_dir,
                    required_roots=contract_targets["required_roots"],
                    related_artifacts=contract_targets["related_artifacts"],
                )
                if not contract_check["ok"]:
                    raise ArtifactLoadError(
                        "Artifact contract check failed for --from-output: "
                        + format_artifact_contract_errors(contract_check)
                        + _artifact_recovery_suffix(
                            result,
                            format_artifact_contract_errors(contract_check),
                        )
                    )
        except ArtifactLoadError as exc:
            raise SystemExit(str(exc)) from exc
    elif not args.repo:
        raise SystemExit("Missing repository path. Usage: python main.py <repo-path>")
    else:
        repo = Path(args.repo)
        if not repo.exists() or not repo.is_dir():
            raise SystemExit(f"Repository path does not exist or is not a directory: {repo}")
        workflow = AegisWorkflow(
            repo,
            output_root=Path(args.out),
            max_files=args.max_files,
            include=args.include,
            exclude=args.exclude,
            use_cache=not args.no_cache,
            llm_config=LLMConfig.from_env(enabled=args.llm),
        )
        result = workflow.run()

    payload = result_payload(result)
    if args.status or args.handoff:
        manifest_check = verify_manifest_integrity(
            result.output_dir,
            repo_name=result.knowledge.repo_name,
            validate_present_tracked=True,
        )
        summary, status_report, handoff_card = status_report_payload(result, manifest_check=manifest_check)
        payload["status_report"] = status_report
        payload["handoff_card"] = handoff_card
        if args.status:
            payload["summary"] = summary
            payload["orchestration"] = summary.get("orchestration", {})
            if args.json:
                print_json(payload)
            else:
                print_status(payload)
        else:
            handoff_payload = {
                "repo": payload["repo"],
                "root": payload["root"],
                "outputs": payload["outputs"],
                "handoff_card": handoff_card,
                "status_report": {
                    "ok": status_report.get("ok"),
                    "manifest_integrity": status_report.get("manifest_integrity"),
                    "handoff_card_artifact": status_report.get("handoff_card_artifact"),
                    "handoff_card_validation": status_report.get("handoff_card_validation"),
                },
            }
            if args.json:
                print_json(handoff_payload)
            else:
                print_handoff(handoff_payload)
        return 0 if status_report["ok"] else 2
    trace = []
    impact = []
    answer = None
    qa = None
    rag_index = None
    if args.trace_interface:
        query = CodeGraphQuery(result.knowledge.code_graph)
        trace = query.trace_interface(args.trace_interface)
        payload["trace"] = trace_payload(args.trace_interface, trace)
    should_impact = args.impact or bool(args.impact_file)
    if should_impact:
        paths = list(dict.fromkeys(args.impact_file or result.knowledge.changed_files))
        source = "explicit" if args.impact_file else "git_diff"
        query = CodeGraphQuery(result.knowledge.code_graph)
        impact = query.impacted_by_files(paths, max_depth=args.impact_depth) if paths else []
        payload["impact"] = impact_payload(paths, impact, depth=args.impact_depth, source=source)
        write_json(result.output_dir / "impact.json", payload["impact"])
    ask_question = args.ask or args.ready_ask
    if ask_question:
        llm_config = LLMConfig.from_env(enabled=args.llm)
        rag_index = get_rag_index(result, prefer_saved=bool(args.from_output))
        qa = RepositoryQAAgent(
            result.knowledge,
            rag_index,
            llm=LLMClient(llm_config) if llm_config.enabled else None,
        )
        answer = qa.answer(
            ask_question,
            top_k=args.top_k,
            max_context_chars=args.context_chars,
            context_files=list(args.context_file or []),
        )
        payload["qa"] = qa_payload(qa, answer)
        write_qa_artifacts(result.output_dir, payload["qa"], answer)

    should_eval = args.eval or args.eval_suite or args.eval_fail_under is not None or args.ready
    if should_eval:
        if rag_index is None:
            rag_index = get_rag_index(result, prefer_saved=bool(args.from_output))
        suite = load_suite(Path(args.eval_suite)) if args.eval_suite else builtin_suite(result.knowledge.repo_name)
        evaluation = Evaluator(result.knowledge, rag_index).run(suite)
        write_json(result.output_dir / "evaluation.json", evaluation)
        payload["evaluation"] = evaluation
        payload["quality_gate"] = quality_gate_payload(evaluation["metrics"], args.eval_fail_under)
    if args.ready:
        doctor_payload = Doctor(
            repo=Path(result.knowledge.root),
            output_root=Path(args.out),
            llm_config=LLMConfig.from_env(enabled=args.llm),
        ).run()
        readiness = ReadinessAssessor(
            result,
            doctor_payload=doctor_payload,
            evaluation_payload=payload.get("evaluation"),
            qa_payload=payload.get("qa"),
            threshold=args.ready_fail_under,
        ).run()
        write_json(result.output_dir / "readiness.json", readiness)
        payload["readiness"] = readiness
    if args.ready or should_eval or should_impact or ask_question:
        stabilize_manifest_and_summary(
            result,
            manifest_builder=lambda: refresh_manifest(result, args),
            payload=payload,
        )
    else:
        write_run_summary(result, payload=payload)

    run_summary_path = result.output_dir / "run_summary.json"
    if run_summary_path.exists():
        try:
            run_summary = json.loads(run_summary_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            run_summary = {}
        if isinstance(run_summary, dict):
            payload["summary"] = run_summary
            orchestration = run_summary.get("orchestration")
            if isinstance(orchestration, dict):
                payload["orchestration"] = orchestration
    handoff_card_path = result.output_dir / "handoff_card.json"
    if handoff_card_path.exists():
        try:
            payload["handoff_card"] = json.loads(handoff_card_path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload["handoff_card"] = build_handoff_card(
                result,
                payload=payload,
                summary=payload.get("summary"),
            )
    else:
        payload["handoff_card"] = build_handoff_card(
            result,
            payload=payload,
            summary=payload.get("summary"),
        )

    if args.json:
        print_json(payload)
        passed = payload.get("quality_gate", {}).get("passed", True)
        passed = passed and payload.get("readiness", {}).get("passed", True)
        return 0 if passed else 2

    print(f"AEGIS analysis complete: {result.output_dir}")
    for key in ("report", "html", "mermaid", "knowledge", "findings", "rag_index"):
        print(f"- {key.replace('_', ' ')}: {payload['outputs'][key]}")
    if args.trace_interface:
        print(f"\nCodeGraph trace for {args.trace_interface}:")
        if not trace:
            print("- no matching interface found")
        for node in trace:
            if node.path and node.line:
                location = f" ({node.path}:{node.line})"
            elif node.path:
                location = f" ({node.path})"
            else:
                location = ""
            print(f"- {node.kind}: {node.name}{location}")
    if should_impact:
        print("\nCodeGraph impact analysis:")
        if not payload["impact"]["input_paths"]:
            print("- no changed files provided and no git diff changed files were recorded")
        elif not impact:
            print("- no impacted nodes found")
        else:
            print(f"- input files: {', '.join(payload['impact']['input_paths'])}")
            print(f"- affected files: {', '.join(payload['impact']['affected_files']) or 'none'}")
            for node in impact[:20]:
                if node.path and node.line:
                    location = f" ({node.path}:{node.line})"
                elif node.path:
                    location = f" ({node.path})"
                else:
                    location = ""
                print(f"- {node.kind}: {node.name}{location}")
            print(f"- impact: {payload['outputs']['impact']}")
    if ask_question and answer:
        print(f"\nAEGIS RAG answer ({'LLM' if answer.used_llm else 'offline'}):")
        print(answer.answer)
        print("\nContext pack:")
        print(answer.context_pack.render())
    if should_eval:
        metrics = payload["evaluation"]["metrics"]
        print("\nAEGIS evaluation:")
        print(f"- suite: {payload['evaluation']['suite']}")
        print(f"- rag recall: {metric_ratio(metrics['rag_hits'], metrics['rag_cases'], metrics['rag_recall'])}")
        print(f"- trace success: {metric_ratio(metrics['trace_hits'], metrics['trace_cases'], metrics['trace_success_rate'])}")
        print(
            "- source context coverage: "
            f"{metric_ratio(metrics['source_context_hits'], metrics['source_context_cases'], metrics['source_context_coverage'])}"
        )
        print(
            "- prompt context coverage: "
            f"{metric_ratio(metrics['prompt_context_hits'], metrics['prompt_context_cases'], metrics['prompt_context_coverage'])}"
        )
        print(
            "- prompt expected-path coverage: "
            f"{metric_ratio(metrics['prompt_context_expected_path_hits'], metrics['prompt_context_expected_paths'], metrics['prompt_context_expected_path_coverage'])}"
        )
        print(
            "- complete-file context coverage: "
            f"{metric_ratio(metrics['complete_file_context_hits'], metrics['complete_file_context_cases'], metrics['complete_file_context_coverage'])}"
        )
        print(
            "- complete-file expected-path coverage: "
            f"{metric_ratio(metrics['complete_file_expected_path_hits'], metrics['complete_file_expected_paths'], metrics['complete_file_expected_path_coverage'])}"
        )
        print(f"- overall score: {metrics['overall_score']:.2%}")
        print(f"- evaluation: {payload['outputs']['evaluation']}")
        gate = payload.get("quality_gate")
        if gate and gate["threshold"] is not None:
            print(
                "- quality gate: "
                f"{'passed' if gate['passed'] else 'failed'} "
                f"(score={gate['score']:.2%}, threshold={gate['threshold']:.2%})"
            )
            if not gate["passed"]:
                return 2
    if args.ready:
        readiness = payload["readiness"]
        print("\nAEGIS readiness:")
        print(f"- status: {'passed' if readiness['passed'] else 'failed'}")
        print(f"- threshold: {readiness['threshold']:.2%}")
        for check in readiness["checks"]:
            print(f"- {check['status']}: {check['name']} - {check['message']}")
        print(f"- readiness: {payload['outputs']['readiness']}")
        if not readiness["passed"]:
            return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
