from __future__ import annotations

from dataclasses import dataclass
import math
import re

from aegis.rag.index import RAGChunk, RAGIndex


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]*|[\u4e00-\u9fff]+|\d+")

QUERY_EXPANSIONS = {
    "用户": ["user", "users", "account"],
    "创建": ["create", "post", "save", "insert"],
    "接口": ["interface", "api", "route", "endpoint"],
    "路由": ["route", "endpoint", "api"],
    "数据": ["data", "model", "repository", "db"],
    "写入": ["save", "write", "insert", "repository"],
    "仓库": ["repository", "repo"],
    "服务": ["service"],
    "配置": ["config", "runtime", "pyproject", "package"],
    "风险": ["risk", "security"],
}


@dataclass
class RetrievalResult:
    chunk: RAGChunk
    score: float
    matched_terms: list[str]


class RAGRetriever:
    def __init__(self, index: RAGIndex) -> None:
        self.index = index
        self.documents = [self._tokens(chunk.title + "\n" + chunk.text) for chunk in index.chunks]
        self.df = self._document_frequency(self.documents)
        self.avgdl = sum(len(doc) for doc in self.documents) / max(len(self.documents), 1)

    def search(self, query: str, *, top_k: int = 8, kind: str | None = None) -> list[RetrievalResult]:
        q_tokens = self._expand_query_tokens(self._tokens(query))
        if not q_tokens:
            return []
        results: list[RetrievalResult] = []
        for chunk, doc in zip(self.index.chunks, self.documents):
            if kind and chunk.kind != kind:
                continue
            score = self._bm25(q_tokens, doc)
            score += self._path_bonus(q_tokens, chunk)
            if score <= 0:
                continue
            matched = sorted(set(q_tokens).intersection(doc))
            results.append(RetrievalResult(chunk=chunk, score=score, matched_terms=matched))
        return sorted(results, key=lambda item: item.score, reverse=True)[:top_k]

    def context(self, query: str, *, top_k: int = 8, max_chars: int = 12000) -> str:
        parts: list[str] = []
        for idx, result in enumerate(self.search(query, top_k=top_k), start=1):
            chunk = result.chunk
            evidence = "; ".join(
                f"{ev.path}:{ev.line} {ev.snippet}" for ev in chunk.evidence[:3]
            )
            parts.append(
                "\n".join(
                    [
                        f"[{idx}] {chunk.title} score={result.score:.2f}",
                        f"kind={chunk.kind} path={chunk.path or ''} line={chunk.line or ''}",
                        chunk.text,
                        f"evidence: {evidence}" if evidence else "evidence: none",
                    ]
                )
            )
            if len("\n\n".join(parts)) >= max_chars:
                break
        return "\n\n".join(parts)[:max_chars]

    def _bm25(self, query_tokens: list[str], doc_tokens: list[str]) -> float:
        if not doc_tokens:
            return 0.0
        tf: dict[str, int] = {}
        for token in doc_tokens:
            tf[token] = tf.get(token, 0) + 1
        k1 = 1.5
        b = 0.75
        score = 0.0
        doc_len = len(doc_tokens)
        total_docs = max(len(self.documents), 1)
        for token in query_tokens:
            freq = tf.get(token, 0)
            if freq == 0:
                continue
            df = self.df.get(token, 0)
            idf = math.log(1 + (total_docs - df + 0.5) / (df + 0.5))
            denom = freq + k1 * (1 - b + b * doc_len / max(self.avgdl, 1))
            score += idf * (freq * (k1 + 1)) / denom
        return score

    @staticmethod
    def _path_bonus(query_tokens: list[str], chunk: RAGChunk) -> float:
        haystack = " ".join([chunk.id, chunk.title, chunk.path or ""]).lower()
        return sum(1.5 for token in set(query_tokens) if token in haystack)

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return [match.group(0).lower() for match in TOKEN_RE.finditer(value)]

    @staticmethod
    def _expand_query_tokens(tokens: list[str]) -> list[str]:
        expanded = list(tokens)
        joined = "".join(tokens)
        for key, values in QUERY_EXPANSIONS.items():
            if key in joined or key in tokens:
                expanded.extend(values)
        return expanded

    @staticmethod
    def _document_frequency(docs: list[list[str]]) -> dict[str, int]:
        df: dict[str, int] = {}
        for doc in docs:
            for token in set(doc):
                df[token] = df.get(token, 0) + 1
        return df
