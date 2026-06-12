from __future__ import annotations

from dataclasses import dataclass

from aegis.llm import LLMClient, LLMError
from aegis.models import RepoKnowledge
from aegis.rag.index import RAGChunk, RAGIndex
from aegis.rag.retriever import RAGContextPack, RAGRetriever, RetrievalResult


@dataclass
class QAAnswer:
    question: str
    answer: str
    results: list[RetrievalResult]
    context_pack: RAGContextPack
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
        context_pack = self.retriever.context_pack(
            question,
            top_k=top_k,
            max_chars=max_context_chars,
        )
        if self.llm and self.llm.available:
            system = (
                "You are the AEGIS repository QA agent. Answer only from the provided "
                "context pack. If the evidence is insufficient, say so. Cite file "
                "paths and line ranges."
            )
            user = f"Question: {question}\n\n{context_pack.render()}"
            try:
                return QAAnswer(
                    question=question,
                    answer=self.llm.complete(system=system, user=user),
                    results=results,
                    context_pack=context_pack,
                    used_llm=True,
                )
            except LLMError as exc:
                fallback = self._offline_answer(question, results)
                fallback += f"\n\nLLM request failed; returned offline evidence answer instead: {exc}"
                return QAAnswer(
                    question=question,
                    answer=fallback,
                    results=results,
                    context_pack=context_pack,
                    used_llm=False,
                )
        return QAAnswer(
            question=question,
            answer=self._offline_answer(question, results),
            results=results,
            context_pack=context_pack,
            used_llm=False,
        )

    def _offline_answer(self, question: str, results: list[RetrievalResult]) -> str:
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
