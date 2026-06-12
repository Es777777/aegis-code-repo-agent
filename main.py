from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

from aegis.config import AegisConfig, LLMConfig, load_env_file
from aegis.evaluation import Evaluator, builtin_suite, load_suite
from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.llm import LLMClient
from aegis.rag.qa import QAAnswer
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGIndexBuilder
from aegis.rag.qa import RepositoryQAAgent
from aegis.rag.retriever import RetrievalResult
from aegis.server import serve
from aegis.utils import write_json


def parse_args() -> argparse.Namespace:
    load_env_file(".env")
    config = AegisConfig.from_env()
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="AEGIS 2.0 MVP: multi-agent repository reading and analysis.",
    )
    parser.add_argument("repo", nargs="?", default=config.repo_path, help="要分析的本地代码仓库路径")
    parser.add_argument("--out", default=config.output_dir, help="输出目录，默认 output/aegis")
    parser.add_argument("--max-files", type=int, default=config.max_files, help="最大扫描文件数")
    parser.add_argument("--no-cache", action="store_true", default=not config.use_cache, help="禁用文件解析缓存")
    parser.add_argument("--llm", action="store_true", default=bool(config.llm and config.llm.enabled), help="启用可选 LLM 综合分析")
    parser.add_argument(
        "--serve",
        nargs="?",
        const=config.serve_dir or config.output_dir,
        default=None,
        help="启动静态报告服务器，传入要服务的目录；不传目录时读取 AEGIS_SERVE_DIR",
    )
    parser.add_argument("--host", default=config.serve_host, help="报告服务器 host")
    parser.add_argument("--port", type=int, default=config.serve_port, help="报告服务器 port")
    parser.add_argument("--trace-interface", help="分析后输出接口链路，例如 /users")
    parser.add_argument("--ask", help="分析后使用 RAG Agent 回答仓库问题")
    parser.add_argument("--top-k", type=int, default=8, help="RAG 检索返回数量")
    parser.add_argument("--eval", action="store_true", help="运行 RAG/CodeGraph 内置或自定义评测")
    parser.add_argument("--eval-suite", help="评测用例 JSON 文件；不传则使用当前示例仓库的内置用例")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出分析摘要、接口追踪或 RAG 问答结果")
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
        "results": [retrieval_payload(agent, item) for item in answer.results],
    }


def print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def metric_ratio(hits: int, total: int, rate: float) -> str:
    if total <= 0:
        return "n/a"
    return f"{hits}/{total} ({rate:.2%})"


def main() -> int:
    args = parse_args()
    if args.serve:
        serve(Path(args.serve), host=args.host, port=args.port)
        return 0
    if not args.repo:
        raise SystemExit("缺少仓库路径。用法：python main.py <repo-path> 或 python main.py --serve <report-dir>")
    repo = Path(args.repo)
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"仓库路径不存在或不是目录：{repo}")
    workflow = AegisWorkflow(
        repo,
        output_root=Path(args.out),
        max_files=args.max_files,
        use_cache=not args.no_cache,
        llm_config=LLMConfig.from_env(enabled=args.llm),
    )
    result = workflow.run()
    payload = result_payload(result)
    trace = []
    answer = None
    qa = None
    rag_index = None
    if args.trace_interface:
        query = CodeGraphQuery(result.knowledge.code_graph)
        trace = query.trace_interface(args.trace_interface)
        payload["trace"] = trace_payload(args.trace_interface, trace)
    if args.ask:
        llm_config = LLMConfig.from_env(enabled=args.llm)
        rag_index = RAGIndexBuilder(result.knowledge).build()
        qa = RepositoryQAAgent(
            result.knowledge,
            rag_index,
            llm=LLMClient(llm_config) if llm_config.enabled else None,
        )
        answer = qa.answer(args.ask, top_k=args.top_k)
        payload["qa"] = qa_payload(qa, answer)
    if args.eval or args.eval_suite:
        if rag_index is None:
            rag_index = RAGIndexBuilder(result.knowledge).build()
        suite = load_suite(Path(args.eval_suite)) if args.eval_suite else builtin_suite(result.knowledge.repo_name)
        evaluation = Evaluator(result.knowledge, rag_index).run(suite)
        write_json(result.output_dir / "evaluation.json", evaluation)
        payload["evaluation"] = evaluation
    if args.json:
        print_json(payload)
        return 0
    print(f"AEGIS analysis complete: {result.output_dir}")
    for key in ("report", "html", "mermaid", "knowledge", "findings", "rag_index"):
        print(f"- {key.replace('_', ' ')}: {payload['outputs'][key]}")
    if args.trace_interface:
        print(f"\nCodeGraph trace for {args.trace_interface}:")
        if not trace:
            print("- no matching interface found")
        for node in trace:
            location = (
                f" ({node.path}:{node.line})"
                if node.path and node.line
                else f" ({node.path})"
                if node.path
                else ""
            )
            print(f"- {node.kind}: {node.name}{location}")
    if args.ask:
        print(f"\nAEGIS RAG answer ({'LLM' if answer.used_llm else 'offline'}):")
        print(answer.answer)
    if args.eval or args.eval_suite:
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
