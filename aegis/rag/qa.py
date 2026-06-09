from __future__ import annotations

from dataclasses import dataclass

from aegis.llm import LLMClient, LLMError
from aegis.models import RepoKnowledge
from aegis.rag.index import RAGIndex
from aegis.rag.retriever import RAGRetriever, RetrievalResult


@dataclass
class QAAnswer:
    question: str
    answer: str
    results: list[RetrievalResult]
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

    def answer(self, question: str, *, top_k: int = 8) -> QAAnswer:
        results = self.retriever.search(question, top_k=top_k)
        if self.llm and self.llm.available:
            context = self.retriever.context(question, top_k=top_k)
            system = (
                "你是 AEGIS 的仓库问答 Agent。只能基于提供的 RAG 上下文回答。"
                "如果证据不足，明确说不确定。回答中文，引用文件路径和行号。"
            )
            user = f"问题：{question}\n\nRAG 上下文：\n{context}"
            try:
                return QAAnswer(
                    question=question,
                    answer=self.llm.complete(system=system, user=user),
                    results=results,
                    used_llm=True,
                )
            except LLMError as exc:
                fallback = self._offline_answer(question, results)
                fallback += f"\n\nLLM 调用失败，已使用离线回答：{exc}"
                return QAAnswer(question=question, answer=fallback, results=results, used_llm=False)
        return QAAnswer(
            question=question,
            answer=self._offline_answer(question, results),
            results=results,
            used_llm=False,
        )

    def _offline_answer(self, question: str, results: list[RetrievalResult]) -> str:
        if not results:
            return "没有检索到足够相关的仓库证据。可以换一个更具体的问题，例如接口路径、类名、文件名或模块名。"
        lines = [
            f"问题：{question}",
            "",
            "离线 RAG 检索到以下关键证据：",
        ]
        for idx, result in enumerate(results[:6], start=1):
            chunk = result.chunk
            lines.append(f"{idx}. {chunk.title}（{chunk.kind}，score={result.score:.2f}）")
            if chunk.path:
                location = f"{chunk.path}:{chunk.line or 1}"
                lines.append(f"   位置：{location}")
            if chunk.evidence:
                ev = chunk.evidence[0]
                lines.append(f"   证据：{ev.path}:{ev.line} {ev.snippet}")
            summary = " ".join(chunk.text.splitlines()[:3])
            lines.append(f"   摘要：{summary[:260]}")
        lines.append("")
        lines.append("结论：以上证据可作为回答依据；若需要自然语言综合推理，请配置 AEGIS_LLM_* 并使用 --llm。")
        return "\n".join(lines)
