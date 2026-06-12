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
from aegis.manifest import build_manifest
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGIndexBuilder
from aegis.rag.qa import QAAnswer, RepositoryQAAgent
from aegis.rag.retriever import RetrievalResult
from aegis.readiness import ReadinessAssessor
from aegis.server import serve
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
        "context_pack": answer.context_pack.to_dict(),
        "results": [retrieval_payload(agent, item) for item in answer.results],
    }


def get_rag_index(result: Any, *, prefer_saved: bool) -> Any:
    rag_index_path = result.output_dir / "rag_index.json"
    if prefer_saved and rag_index_path.exists():
        try:
            return load_rag_index(rag_index_path)
        except ArtifactLoadError as exc:
            raise SystemExit(str(exc)) from exc
    return RAGIndexBuilder(result.knowledge).build()


def refresh_manifest(result: Any, args: argparse.Namespace) -> None:
    manifest = build_manifest(
        result,
        max_files=args.max_files,
        include=list(args.include or []),
        exclude=list(args.exclude or []),
        use_cache=not args.no_cache,
        llm_enabled=bool(args.llm),
        events_count=_events_count(result.output_dir),
    )
    write_json(result.output_dir / "manifest.json", manifest)


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
    print(json.dumps(payload, ensure_ascii=False, indent=2))


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
    if args.ask:
        llm_config = LLMConfig.from_env(enabled=args.llm)
        rag_index = get_rag_index(result, prefer_saved=bool(args.from_output))
        qa = RepositoryQAAgent(
            result.knowledge,
            rag_index,
            llm=LLMClient(llm_config) if llm_config.enabled else None,
        )
        answer = qa.answer(
            args.ask,
            top_k=args.top_k,
            max_context_chars=args.context_chars,
        )
        payload["qa"] = qa_payload(qa, answer)

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
            threshold=args.ready_fail_under,
        ).run()
        write_json(result.output_dir / "readiness.json", readiness)
        payload["readiness"] = readiness
    if args.ready or should_eval or should_impact:
        refresh_manifest(result, args)

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
    if args.ask and answer:
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
