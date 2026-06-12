from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.llm import LLMClient, LLMError
from aegis.models import RepoKnowledge
from aegis.rag.index import RAGChunk, RAGIndex
from aegis.rag.retriever import RAGContextPack, RAGRetriever, RetrievalResult


ROUTE_RE = re.compile(r"(/[A-Za-z0-9_./{}<>\-:]+)")


@dataclass
class QAAnswer:
    question: str
    answer: str
    results: list[RetrievalResult]
    context_pack: RAGContextPack
    graph_context: dict[str, Any] | None = None
    used_llm: bool = False


class RepositoryQAAgent:
    def __init__(
        self,
        knowledge: RepoKnowledge,
        index: RAGIndex,
        *,
        llm: LLMClient | None = None,
    ) -> None:
        self.knowledge = knowledge
        self.index = index
        self.retriever = RAGRetriever(index)
        self.llm = llm

    def answer(
        self,
        question: str,
        *,
        top_k: int = 8,
        max_context_chars: int = 12000,
    ) -> QAAnswer:
        results = self.retriever.search(question, top_k=top_k)
        graph_context = self._graph_context(question, results)
        context_pack = self.retriever.context_pack(
            question,
            top_k=top_k,
            max_chars=max_context_chars,
            required_paths=self._graph_source_paths(graph_context),
        )
        if self.llm and self.llm.available:
            system = (
                "You are the AEGIS repository QA agent. Answer only from the provided "
                "context pack. The context pack contains real source file blocks with "
                "line numbers. If Graph Context is present, use it to explain route "
                "and call-chain structure, then verify with source blocks. If the "
                "source evidence is insufficient, say so. Cite file paths and line ranges."
            )
            graph_section = self._render_graph_context(graph_context)
            user = f"Question: {question}\n\n{graph_section}\n\n{context_pack.render()}"
            try:
                return QAAnswer(
                    question=question,
                    answer=self.llm.complete(system=system, user=user),
                    results=results,
                    context_pack=context_pack,
                    graph_context=graph_context,
                    used_llm=True,
                )
            except LLMError as exc:
                fallback = self._offline_answer(question, results, graph_context)
                fallback += f"\n\nLLM request failed; returned offline evidence answer instead: {exc}"
                return QAAnswer(
                    question=question,
                    answer=fallback,
                    results=results,
                    context_pack=context_pack,
                    graph_context=graph_context,
                    used_llm=False,
                )
        return QAAnswer(
            question=question,
            answer=self._offline_answer(question, results, graph_context),
            results=results,
            context_pack=context_pack,
            graph_context=graph_context,
            used_llm=False,
        )

    def _offline_answer(
        self,
        question: str,
        results: list[RetrievalResult],
        graph_context: dict[str, Any] | None = None,
    ) -> str:
        if not results:
            return (
                "No repository evidence was retrieved. Try a more specific route, "
                "class name, file name, or module name."
            )
        lines = [
            f"Question: {question}",
            "",
            "Offline RAG retrieved the following evidence:",
        ]
        if graph_context and graph_context.get("nodes"):
            lines.extend(["", "CodeGraph trace:"])
            for idx, node in enumerate(graph_context["nodes"][:12], start=1):
                location = ""
                if node.get("path") and node.get("line"):
                    location = f" ({node['path']}:{node['line']})"
                elif node.get("path"):
                    location = f" ({node['path']})"
                lines.append(f"   {idx}. {node['kind']}: {node['name']}{location}")
            lines.append("")
        for idx, result in enumerate(results[:6], start=1):
            chunk = result.chunk
            lines.append(f"{idx}. {chunk.title} ({chunk.kind}, score={result.score:.2f})")
            if chunk.path:
                lines.append(f"   Location: {chunk.path}:{chunk.line or 1}")
            if chunk.evidence:
                ev = chunk.evidence[0]
                lines.append(f"   Evidence: {ev.path}:{ev.line} {ev.snippet}")
            summary = " ".join(chunk.text.splitlines()[:3])
            lines.append(f"   Summary: {summary[:260]}")
            source = chunk if chunk.kind == "source" else self.retriever.source_companion(chunk)
            if source:
                excerpt = self._source_excerpt(source, focus_line=chunk.line)
                if excerpt:
                    lines.append("   Source context:")
                    lines.extend(f"      {line}" for line in excerpt)
        lines.append("")
        lines.append(
            "Conclusion: use the evidence above as citations. Configure AEGIS_LLM_* "
            "and pass --llm for synthesized natural-language reasoning."
        )
        return "\n".join(lines)

    def _graph_context(
        self,
        question: str,
        results: list[RetrievalResult],
    ) -> dict[str, Any] | None:
        route = self._route_from_question(question) or self._route_from_results(results)
        if not route:
            return None
        trace = CodeGraphQuery(self.knowledge.code_graph).trace_interface(route)
        if not trace:
            return {"route": route, "nodes": []}
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

    @staticmethod
    def _route_from_question(question: str) -> str | None:
        match = ROUTE_RE.search(question)
        return match.group(1).rstrip("，。！？?,.;") if match else None

    @staticmethod
    def _route_from_results(results: list[RetrievalResult]) -> str | None:
        for result in results:
            route = result.chunk.metadata.get("route")
            if isinstance(route, str) and route.startswith("/"):
                return route
        return None

    @staticmethod
    def _render_graph_context(graph_context: dict[str, Any] | None) -> str:
        if not graph_context:
            return "Graph Context: none"
        lines = [
            "Graph Context:",
            f"Route: {graph_context.get('route', '')}",
        ]
        nodes = graph_context.get("nodes") or []
        if not nodes:
            lines.append("Nodes: none")
            return "\n".join(lines)
        lines.append("Trace nodes:")
        for idx, node in enumerate(nodes[:16], start=1):
            location = ""
            if node.get("path") and node.get("line"):
                location = f" {node['path']}:{node['line']}"
            elif node.get("path"):
                location = f" {node['path']}"
            lines.append(f"{idx}. {node['kind']} {node['name']}{location}")
        return "\n".join(lines)

    @staticmethod
    def _graph_source_paths(graph_context: dict[str, Any] | None) -> list[str]:
        if not graph_context:
            return []
        paths: list[str] = []
        for node in graph_context.get("nodes") or []:
            path = node.get("path")
            if isinstance(path, str) and path:
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _source_excerpt(
        chunk: RAGChunk,
        *,
        focus_line: int | None = None,
        max_lines: int = 16,
    ) -> list[str]:
        lines = chunk.text.splitlines()
        try:
            code_start = lines.index("Code:") + 1
        except ValueError:
            code_start = 0
        code_lines = lines[code_start:]
        if focus_line:
            prefix = f"{focus_line}:"
            for idx, line in enumerate(code_lines):
                if line.startswith(prefix):
                    start = max(0, idx - max_lines // 2)
                    return code_lines[start : start + max_lines]
        return code_lines[:max_lines]
