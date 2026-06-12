from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from pathlib import Path
from typing import Any

from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.models import RepoKnowledge
from aegis.rag.index import RAGIndex
from aegis.rag.retriever import RAGRetriever


@dataclass
class RAGEvalCase:
    question: str
    expected_paths: list[str]
    top_k: int = 10


@dataclass
class TraceEvalCase:
    route: str
    expected_paths: list[str] = field(default_factory=list)
    expected_names: list[str] = field(default_factory=list)
    max_depth: int = 6


@dataclass
class EvaluationSuite:
    name: str
    rag: list[RAGEvalCase] = field(default_factory=list)
    trace: list[TraceEvalCase] = field(default_factory=list)


def builtin_suite(repo_name: str) -> EvaluationSuite:
    if repo_name == "sample_repo":
        return EvaluationSuite(
            name="sample_repo_builtin",
            rag=[
                RAGEvalCase(
                    question="用户创建接口在哪里，数据写入哪里？",
                    expected_paths=[
                        "app.py",
                        "services/user_service.py",
                        "repositories/user_repository.py",
                    ],
                ),
                RAGEvalCase(
                    question="POST /users 的调用链路是什么？",
                    expected_paths=[
                        "app.py",
                        "services/user_service.py",
                        "repositories/user_repository.py",
                    ],
                ),
            ],
            trace=[
                TraceEvalCase(
                    route="/users",
                    expected_paths=[
                        "app.py",
                        "services/user_service.py",
                        "repositories/user_repository.py",
                    ],
                    expected_names=["POST /users", "create_user", "UserService", "UserRepository"],
                )
            ],
        )
    if repo_name == "eda_repo":
        return EvaluationSuite(
            name="eda_repo_builtin",
            rag=[
                RAGEvalCase("项目入口在哪里", ["src/main_entrypoint.py"]),
                RAGEvalCase("布线核心模块是什么", ["src/routing/rw_route.py"]),
                RAGEvalCase("模块布局和硬宏布局", ["src/placement/block_placer.py"]),
                RAGEvalCase("是否依赖 Vivado 外部工具", ["src/integrations/vivado_tools.py"]),
                RAGEvalCase("时序分析延迟模型", ["src/timing/timing_model.py"]),
                RAGEvalCase("partial DFX routing", ["src/routing/partial_dfx_router.py"]),
                RAGEvalCase("项目是否支持完整 RTL 流程", ["src/rtl/rtl_flow.py"]),
                RAGEvalCase("器件资源在哪里加载", ["src/device/device_resources.py"]),
            ],
        )
    return EvaluationSuite(name=f"{repo_name}_empty")


def load_suite(path: Path) -> EvaluationSuite:
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return EvaluationSuite(
        name=str(data.get("name") or path.stem),
        rag=[
            RAGEvalCase(
                question=str(item["question"]),
                expected_paths=list(item.get("expected_paths", [])),
                top_k=int(item.get("top_k", 10)),
            )
            for item in data.get("rag", [])
        ],
        trace=[
            TraceEvalCase(
                route=str(item["route"]),
                expected_paths=list(item.get("expected_paths", [])),
                expected_names=list(item.get("expected_names", [])),
                max_depth=int(item.get("max_depth", 6)),
            )
            for item in data.get("trace", [])
        ],
    )


class Evaluator:
    def __init__(self, knowledge: RepoKnowledge, index: RAGIndex) -> None:
        self.knowledge = knowledge
        self.index = index
        self.retriever = RAGRetriever(index)
        self.graph_query = CodeGraphQuery(knowledge.code_graph)

    def run(self, suite: EvaluationSuite) -> dict[str, Any]:
        rag_cases = [self._eval_rag_case(case) for case in suite.rag]
        trace_cases = [self._eval_trace_case(case) for case in suite.trace]
        rag_total = len(rag_cases)
        trace_total = len(trace_cases)
        rag_hits = sum(1 for case in rag_cases if case["hit"])
        trace_hits = sum(1 for case in trace_cases if case["hit"])
        source_hits = sum(1 for case in rag_cases if case["source_context_available"])
        return {
            "suite": suite.name,
            "repo": self.knowledge.repo_name,
            "metrics": {
                "rag_cases": rag_total,
                "rag_hits": rag_hits,
                "rag_recall": self._rate(rag_hits, rag_total),
                "trace_cases": trace_total,
                "trace_hits": trace_hits,
                "trace_success_rate": self._rate(trace_hits, trace_total),
                "source_context_cases": rag_total,
                "source_context_hits": source_hits,
                "source_context_coverage": self._rate(source_hits, rag_total),
                "overall_score": self._overall_score(rag_hits, rag_total, trace_hits, trace_total, source_hits),
            },
            "rag": rag_cases,
            "trace": trace_cases,
        }

    def _eval_rag_case(self, case: RAGEvalCase) -> dict[str, Any]:
        results = self.retriever.search(case.question, top_k=case.top_k)
        paths = [result.chunk.path for result in results if result.chunk.path]
        unique_paths = list(dict.fromkeys(paths))
        expected = set(case.expected_paths)
        matched = sorted(expected.intersection(unique_paths))
        source_context_available = any(
            result.chunk.kind == "source" or self.retriever.source_companion(result.chunk)
            for result in results
        )
        return {
            **asdict(case),
            "hit": bool(matched),
            "matched_paths": matched,
            "result_paths": unique_paths,
            "source_context_available": source_context_available,
            "top_results": [
                {
                    "title": result.chunk.title,
                    "kind": result.chunk.kind,
                    "path": result.chunk.path,
                    "line": result.chunk.line,
                    "score": result.score,
                }
                for result in results[: min(case.top_k, 10)]
            ],
        }

    def _eval_trace_case(self, case: TraceEvalCase) -> dict[str, Any]:
        trace = self.graph_query.trace_interface(case.route, max_depth=case.max_depth)
        paths = [node.path for node in trace if node.path]
        names = [node.name for node in trace]
        expected_paths = set(case.expected_paths)
        expected_names = set(case.expected_names)
        matched_paths = sorted(expected_paths.intersection(paths))
        matched_names = sorted(expected_names.intersection(names))
        path_ok = not expected_paths or bool(matched_paths)
        name_ok = not expected_names or bool(matched_names)
        return {
            **asdict(case),
            "hit": bool(trace) and path_ok and name_ok,
            "matched_paths": matched_paths,
            "matched_names": matched_names,
            "nodes": [
                {
                    "id": node.id,
                    "kind": node.kind,
                    "name": node.name,
                    "path": node.path,
                    "line": node.line,
                }
                for node in trace
            ],
        }

    @staticmethod
    def _rate(numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 4)

    @classmethod
    def _overall_score(
        cls,
        rag_hits: int,
        rag_total: int,
        trace_hits: int,
        trace_total: int,
        source_hits: int,
    ) -> float:
        weights: list[tuple[float, float]] = []
        if rag_total:
            weights.append((0.5, cls._rate(rag_hits, rag_total)))
            weights.append((0.25, cls._rate(source_hits, rag_total)))
        if trace_total:
            weights.append((0.25, cls._rate(trace_hits, trace_total)))
        if not weights:
            return 0.0
        total_weight = sum(weight for weight, _ in weights)
        return round(sum(weight * score for weight, score in weights) / total_weight, 4)
