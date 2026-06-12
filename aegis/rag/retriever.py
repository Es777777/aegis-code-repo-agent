from __future__ import annotations

from dataclasses import dataclass
import math
import re

from aegis.rag.index import RAGChunk, RAGIndex


TOKEN_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_./:-]*|[\u4e00-\u9fff]+|\d+")
CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|$)|[A-Z]?[a-z]+|\d+")

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
    "入口": ["entry", "entrypoint", "main", "start", "standalone", "driver", "cli"],
    "项目入口": ["main", "entrypoint", "mainentrypoint", "standaloneentrypoint"],
    "核心": ["core", "main", "primary", "central"],
    "模块": ["module", "component", "class", "package"],
    "依赖": ["dependency", "depends", "imports", "uses", "requires"],
    "外部工具": ["external", "tool", "tools", "process", "vivado"],
    "布局": ["place", "placer", "placement", "blockplacer"],
    "硬宏": ["hard", "macro", "hardmacro", "macroplacer"],
    "布线": ["route", "router", "routing", "rwroute"],
    "时序": ["timing", "delay", "timingmodel", "timinggraph", "delayestimator"],
    "延迟": ["delay", "latency", "timing"],
    "rtl": ["rtl", "verilog", "vhdl", "synthesis"],
    "流程": ["flow", "pipeline", "workflow"],
    "完整": ["complete", "full", "end-to-end"],
    "普通单元": ["cell", "cells", "standardcell", "standard"],
    "器件": ["device", "deviceresources", "resources"],
    "资源": ["resource", "resources", "deviceresources"],
    "dfx": ["dfx", "partial", "partialdfxrouter", "reconfig"],
    "partial": ["partial", "dfx", "partialdfxrouter"],
    "vivado": ["vivado", "vivadotools", "ila", "satrouter", "blockcreator"],
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
        self.chunks_by_id = {chunk.id: chunk for chunk in index.chunks}
        self.chunks_by_path: dict[str, list[RAGChunk]] = {}
        self.chunks_by_node: dict[str, list[RAGChunk]] = {}
        for chunk in index.chunks:
            if chunk.path:
                self.chunks_by_path.setdefault(chunk.path, []).append(chunk)
            for node_id in chunk.node_ids:
                self.chunks_by_node.setdefault(node_id, []).append(chunk)

    def search(
        self,
        query: str,
        *,
        top_k: int = 8,
        kind: str | None = None,
        expand: bool = True,
    ) -> list[RetrievalResult]:
        q_tokens = self._expand_query_tokens(self._tokens(query))
        if not q_tokens:
            return []
        results: list[RetrievalResult] = []
        for chunk, doc in zip(self.index.chunks, self.documents):
            if kind and chunk.kind != kind:
                continue
            score = self._bm25(q_tokens, doc)
            score += self._path_bonus(q_tokens, chunk)
            score += self._substring_bonus(q_tokens, chunk)
            if chunk.kind == "source" and score > 0:
                score *= 1.15
            if score <= 0:
                continue
            matched = sorted(set(q_tokens).intersection(doc))
            results.append(RetrievalResult(chunk=chunk, score=score, matched_terms=matched))
        ranked = sorted(results, key=lambda item: item.score, reverse=True)
        if expand and not kind:
            ranked = self._expand_with_neighbor_context(ranked, q_tokens)
        return ranked[:top_k]

    def context(self, query: str, *, top_k: int = 8, max_chars: int = 12000) -> str:
        parts: list[str] = []
        candidates = self.search(query, top_k=max(top_k * 3, top_k + 8))
        context_results = self.with_source_context(candidates, max_results=top_k + min(top_k, 4))
        for idx, result in enumerate(context_results, start=1):
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

    def with_source_context(
        self,
        results: list[RetrievalResult],
        *,
        max_results: int,
    ) -> list[RetrievalResult]:
        expanded: list[RetrievalResult] = []
        seen: set[str] = set()
        source_paths: set[str] = set()
        for result in results:
            if result.chunk.id not in seen:
                expanded.append(result)
                seen.add(result.chunk.id)
                if result.chunk.kind == "source" and result.chunk.path:
                    source_paths.add(result.chunk.path)
            companion = self.source_companion(result.chunk)
            if companion and companion.id not in seen and companion.path not in source_paths:
                expanded.append(
                    RetrievalResult(
                        chunk=companion,
                        score=max(result.score * 0.92, 0.2),
                        matched_terms=result.matched_terms,
                    )
                )
                seen.add(companion.id)
                if companion.path:
                    source_paths.add(companion.path)
            if len(expanded) >= max_results:
                break
        return expanded

    def source_companion(self, chunk: RAGChunk) -> RAGChunk | None:
        if chunk.kind == "source" or not chunk.path:
            return None
        source_chunks = [
            item for item in self.chunks_by_path.get(chunk.path, []) if item.kind == "source"
        ]
        if not source_chunks:
            return None
        if chunk.line:
            for source in source_chunks:
                start = int(source.metadata.get("start_line", source.line or 1))
                end = int(source.metadata.get("end_line", start))
                if start <= chunk.line <= end:
                    return source
        return source_chunks[0]

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
    def _substring_bonus(query_tokens: list[str], chunk: RAGChunk) -> float:
        haystack = (chunk.title + "\n" + chunk.text).lower()
        score = 0.0
        for token in set(query_tokens):
            if len(token) >= 3 and token in haystack:
                score += 0.35
        return score

    @staticmethod
    def _tokens(value: str) -> list[str]:
        tokens: list[str] = []
        for match in TOKEN_RE.finditer(value):
            raw = match.group(0)
            lowered = raw.lower()
            tokens.append(lowered)
            if re.search(r"[A-Za-z]", raw):
                pieces = RAGRetriever._identifier_pieces(raw)
                tokens.extend(pieces)
        return [token for token in tokens if token]

    @staticmethod
    def _identifier_pieces(value: str) -> list[str]:
        pieces: list[str] = []
        for part in re.split(r"[/_.:\-\s]+", value):
            if not part:
                continue
            pieces.append(part.lower())
            pieces.extend(piece.lower() for piece in CAMEL_RE.findall(part))
        return pieces

    @staticmethod
    def _expand_query_tokens(tokens: list[str]) -> list[str]:
        expanded = list(tokens)
        joined = "".join(tokens)
        for key, values in QUERY_EXPANSIONS.items():
            if key in joined or key in tokens:
                expanded.extend(values)
        return list(dict.fromkeys(expanded))

    def _expand_with_neighbor_context(
        self,
        ranked: list[RetrievalResult],
        query_tokens: list[str],
    ) -> list[RetrievalResult]:
        if not ranked:
            return []
        by_id = {result.chunk.id: result for result in ranked}
        expanded: list[RetrievalResult] = list(ranked)
        for result in ranked[:8]:
            chunk = result.chunk
            neighbors: list[RAGChunk] = []
            if chunk.path:
                source_chunks = [item for item in self.chunks_by_path.get(chunk.path, []) if item.kind == "source"]
                neighbors.extend(source_chunks[:3])
                file_chunks = [item for item in self.chunks_by_path.get(chunk.path, []) if item.kind == "file"]
                neighbors.extend(file_chunks[:1])
            for node_id in chunk.node_ids:
                neighbors.extend(self.chunks_by_node.get(node_id, [])[:3])
            for neighbor in neighbors:
                if neighbor.id in by_id:
                    continue
                bonus_score = max(result.score * 0.62, 0.2)
                matched = sorted(set(query_tokens).intersection(self._tokens(neighbor.title + "\n" + neighbor.text)))
                added = RetrievalResult(chunk=neighbor, score=bonus_score, matched_terms=matched)
                by_id[neighbor.id] = added
                expanded.append(added)
        return sorted(expanded, key=lambda item: item.score, reverse=True)

    @staticmethod
    def _document_frequency(docs: list[list[str]]) -> dict[str, int]:
        df: dict[str, int] = {}
        for doc in docs:
            for token in set(doc):
                df[token] = df.get(token, 0) + 1
        return df
