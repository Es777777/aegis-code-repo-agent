from __future__ import annotations

from dataclasses import dataclass, field
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
    required_context_paths: list[str] = field(default_factory=list)
    llm_system_prompt: str = ""
    llm_user_prompt: str = ""
    graph_context: dict[str, Any] | None = None
    used_llm: bool = False
    context_safe_for_llm: bool = False
    llm_skip_reason: str = ""


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
        required_paths = self._required_context_paths(question, graph_context)
        context_pack = self.retriever.context_pack(
            question,
            top_k=top_k,
            max_chars=max_context_chars,
            required_paths=required_paths,
        )
        system = self._llm_system_prompt()
        user = self._llm_user_prompt(
            question,
            graph_context=graph_context,
            context_pack=context_pack,
        )
        unsatisfied_required_paths = context_pack.unsatisfied_required_context_paths()
        llm_skip_reason = self._llm_context_skip_reason(context_pack)
        context_safe_for_llm = not llm_skip_reason
        if self.llm and self.llm.available and context_safe_for_llm:
            try:
                return QAAnswer(
                    question=question,
                    answer=self.llm.complete(system=system, user=user),
                    results=results,
                    context_pack=context_pack,
                    required_context_paths=required_paths,
                    llm_system_prompt=system,
                    llm_user_prompt=user,
                    graph_context=graph_context,
                    used_llm=True,
                    context_safe_for_llm=True,
                )
            except LLMError as exc:
                fallback = self._offline_answer(
                    question,
                    results,
                    graph_context,
                    missing_required_paths=unsatisfied_required_paths,
                )
                fallback += f"\n\nLLM request failed; returned offline evidence answer instead: {exc}"
                return QAAnswer(
                    question=question,
                    answer=fallback,
                    results=results,
                    context_pack=context_pack,
                    required_context_paths=required_paths,
                    llm_system_prompt=system,
                    llm_user_prompt=user,
                    graph_context=graph_context,
                    used_llm=False,
                    context_safe_for_llm=True,
                    llm_skip_reason=f"LLM request failed: {exc}",
                )
        if self.llm and self.llm.available and not context_safe_for_llm:
            fallback = self._offline_answer(
                question,
                results,
                graph_context,
                missing_required_paths=unsatisfied_required_paths,
            )
            fallback += (
                "\n\nLLM request skipped because the prompt context is not safe for code "
                f"reasoning: {llm_skip_reason} Increase --context-chars or narrow the question."
            )
            return QAAnswer(
                question=question,
                answer=fallback,
                results=results,
                context_pack=context_pack,
                required_context_paths=required_paths,
                llm_system_prompt=system,
                llm_user_prompt=user,
                graph_context=graph_context,
                used_llm=False,
                context_safe_for_llm=False,
                llm_skip_reason=llm_skip_reason,
            )
        return QAAnswer(
            question=question,
            answer=self._offline_answer(
                question,
                results,
                graph_context,
                missing_required_paths=unsatisfied_required_paths,
            ),
            results=results,
            context_pack=context_pack,
            required_context_paths=required_paths,
            llm_system_prompt=system,
            llm_user_prompt=user,
            graph_context=graph_context,
            used_llm=False,
            context_safe_for_llm=context_safe_for_llm,
            llm_skip_reason=llm_skip_reason,
        )

    def _offline_answer(
        self,
        question: str,
        results: list[RetrievalResult],
        graph_context: dict[str, Any] | None = None,
        missing_required_paths: list[str] | None = None,
    ) -> str:
        missing_required_paths = missing_required_paths or []
        if not results and not missing_required_paths:
            return (
                "No repository evidence was retrieved. Try a more specific route, "
                "class name, file name, or module name."
            )
        lines = [
            f"Question: {question}",
            "",
            "Offline RAG retrieved the following evidence:",
        ]
        if missing_required_paths:
            lines.extend(
                [
                    "",
                    "Required context missing or incomplete:",
                    *[f"   - {path}" for path in missing_required_paths],
                    "Increase --context-chars or ask a narrower question before relying on an LLM answer.",
                ]
            )
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

    def _required_context_paths(
        self,
        question: str,
        graph_context: dict[str, Any] | None,
    ) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self._graph_source_paths(graph_context),
                    *self._explicit_source_paths(question),
                ]
            )
        )

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

    def _explicit_source_paths(self, question: str) -> list[str]:
        normalized_question = question.replace("\\", "/").lower()
        basename_counts: dict[str, int] = {}
        stem_counts: dict[str, int] = {}
        for path in self.retriever.source_chunks_by_path:
            basename = path.rsplit("/", 1)[-1].lower()
            stem = basename.rsplit(".", 1)[0]
            basename_counts[basename] = basename_counts.get(basename, 0) + 1
            stem_counts[stem] = stem_counts.get(stem, 0) + 1

        paths: list[str] = []
        for path in self.retriever.source_chunks_by_path:
            normalized_path = path.lower()
            basename = normalized_path.rsplit("/", 1)[-1]
            stem = basename.rsplit(".", 1)[0]
            if normalized_path in normalized_question:
                paths.append(path)
            elif basename_counts.get(basename, 0) == 1 and basename in normalized_question:
                paths.append(path)
            elif stem_counts.get(stem, 0) == 1 and stem in normalized_question:
                paths.append(path)
        return list(dict.fromkeys(paths))

    @staticmethod
    def _llm_system_prompt() -> str:
        return (
            "You are the AEGIS repository QA agent. Answer only from the provided "
            "context pack. The context pack contains real line-numbered source "
            "files when complete_file=true, and focused source windows otherwise. "
            "Treat Files in context and Complete files in context as the source of "
            "truth. If Graph Context is present, use it to explain route and "
            "call-chain structure, then verify with source blocks. If the source "
            "evidence is insufficient, say so. Cite file paths and line ranges."
        )

    @staticmethod
    def _llm_context_skip_reason(context_pack: RAGContextPack) -> str:
        reasons: list[str] = []
        unsatisfied_required = context_pack.unsatisfied_required_context_paths()
        if unsatisfied_required:
            reasons.append(
                "required files are missing or incomplete in the prompt context: "
                + ", ".join(unsatisfied_required)
                + "."
            )
        unsatisfied_target = context_pack.unsatisfied_target_context_paths()
        if unsatisfied_target:
            reasons.append(
                "retrieved target files are missing or incomplete in the prompt context: "
                + ", ".join(unsatisfied_target)
                + "."
            )
        if not context_pack.source_context_satisfied():
            reasons.append("no real source file content was packed into the prompt context.")
        if not context_pack.complete_file_context_satisfied():
            reasons.append("no complete source file was packed into the prompt context.")
        return " ".join(reasons)

    @classmethod
    def _llm_user_prompt(
        cls,
        question: str,
        *,
        graph_context: dict[str, Any] | None,
        context_pack: RAGContextPack,
    ) -> str:
        graph_section = cls._render_graph_context(graph_context)
        return f"Question: {question}\n\n{graph_section}\n\n{context_pack.render()}"

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
