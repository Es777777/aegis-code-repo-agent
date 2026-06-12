from __future__ import annotations

from dataclasses import dataclass
import math
import re
from typing import Any

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
    retrieved_from: str | None = None


@dataclass
class RAGContextBlock:
    rank: int
    chunk_id: str
    chunk_kind: str
    title: str
    path: str | None
    start_line: int | None
    end_line: int | None
    score: float
    matched_terms: list[str]
    retrieved_from: str
    content: str
    context_mode: str = "source_chunk"
    complete_file: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "chunk_kind": self.chunk_kind,
            "title": self.title,
            "path": self.path,
            "start_line": self.start_line,
            "end_line": self.end_line,
            "score": self.score,
            "matched_terms": self.matched_terms,
            "retrieved_from": self.retrieved_from,
            "context_mode": self.context_mode,
            "complete_file": self.complete_file,
            "content": self.content,
        }


@dataclass
class RAGContextPack:
    query: str
    max_chars: int
    used_chars: int
    blocks: list[RAGContextBlock]
    required_context_paths: list[str] | None = None
    dropped_blocks: int = 0

    def render(self) -> str:
        source_paths = self.source_paths()
        missing_required_paths = self.missing_required_context_paths()
        incomplete_required_paths = self.incomplete_required_context_paths()
        unsatisfied_required_paths = self.unsatisfied_required_context_paths()
        lines = [
            "AEGIS RAG CONTEXT PACK",
            f"Query: {self.query}",
            f"Budget: {self.used_chars}/{self.max_chars} chars",
            f"Files in context: {', '.join(source_paths) if source_paths else 'none'}",
            f"Complete files in context: {', '.join(self.complete_file_paths()) or 'none'}",
            f"Required context paths: {', '.join(self.required_context_paths or []) or 'none'}",
            f"Missing required context paths: {', '.join(missing_required_paths) or 'none'}",
            f"Incomplete required context paths: {', '.join(incomplete_required_paths) or 'none'}",
            f"Required context satisfied: {str(not unsatisfied_required_paths).lower()}",
            "Instruction: answer only from the real source files and line ranges below; cite paths and lines.",
            "",
        ]
        if unsatisfied_required_paths:
            lines.extend(
                [
                    "Warning: required files are missing or incomplete in this context pack. "
                    "Do not answer claims that depend on those files; ask for a larger context budget.",
                    "",
                ]
            )
        for block in self.blocks:
            location = block.path or "repository"
            if block.start_line and block.end_line:
                location = f"{location}:{block.start_line}-{block.end_line}"
            elif block.start_line:
                location = f"{location}:{block.start_line}"
            lines.extend(
                [
                    f"[{block.rank}] {block.chunk_kind} {location} score={block.score:.2f}",
                    (
                        f"kind={block.chunk_kind} mode={block.context_mode} "
                        f"complete_file={str(block.complete_file).lower()} "
                        f"path={block.path or ''} line={block.start_line or ''}"
                    ),
                    f"title: {block.title}",
                    f"retrieved_from: {block.retrieved_from}",
                    f"matched_terms: {', '.join(block.matched_terms) or 'none'}",
                    "content:",
                    block.content,
                    "",
                ]
            )
        if self.dropped_blocks:
            lines.append(f"Dropped blocks because of context budget: {self.dropped_blocks}")
        return "\n".join(lines).strip()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "max_chars": self.max_chars,
            "used_chars": self.used_chars,
            "dropped_blocks": self.dropped_blocks,
            "required_context_paths": self.required_context_paths or [],
            "missing_required_context_paths": self.missing_required_context_paths(),
            "incomplete_required_context_paths": self.incomplete_required_context_paths(),
            "unsatisfied_required_context_paths": self.unsatisfied_required_context_paths(),
            "required_context_satisfied": not self.unsatisfied_required_context_paths(),
            "source_paths": self.source_paths(),
            "complete_file_paths": self.complete_file_paths(),
            "blocks": [block.to_dict() for block in self.blocks],
        }

    def source_paths(self) -> list[str]:
        return list(
            dict.fromkeys(
                block.path
                for block in self.blocks
                if block.chunk_kind == "source" and block.path
            )
        )

    def complete_file_paths(self) -> list[str]:
        return list(
            dict.fromkeys(
                block.path
                for block in self.blocks
                if block.chunk_kind == "source" and block.path and block.complete_file
            )
        )

    def missing_required_context_paths(self) -> list[str]:
        source_paths = set(self.source_paths())
        return [
            path
            for path in dict.fromkeys(self.required_context_paths or [])
            if path not in source_paths
        ]

    def incomplete_required_context_paths(self) -> list[str]:
        source_paths = set(self.source_paths())
        complete_paths = set(self.complete_file_paths())
        return [
            path
            for path in dict.fromkeys(self.required_context_paths or [])
            if path in source_paths and path not in complete_paths
        ]

    def unsatisfied_required_context_paths(self) -> list[str]:
        return list(
            dict.fromkeys(
                [
                    *self.missing_required_context_paths(),
                    *self.incomplete_required_context_paths(),
                ]
            )
        )


class RAGRetriever:
    def __init__(self, index: RAGIndex) -> None:
        self.index = index
        self.documents = [self._tokens(chunk.title + "\n" + chunk.text) for chunk in index.chunks]
        self.df = self._document_frequency(self.documents)
        self.avgdl = sum(len(doc) for doc in self.documents) / max(len(self.documents), 1)
        self.chunks_by_id = {chunk.id: chunk for chunk in index.chunks}
        self.chunks_by_path: dict[str, list[RAGChunk]] = {}
        self.source_chunks_by_path: dict[str, list[RAGChunk]] = {}
        self.chunks_by_node: dict[str, list[RAGChunk]] = {}
        for chunk in index.chunks:
            if chunk.path:
                self.chunks_by_path.setdefault(chunk.path, []).append(chunk)
                if chunk.kind == "source":
                    self.source_chunks_by_path.setdefault(chunk.path, []).append(chunk)
            for node_id in chunk.node_ids:
                self.chunks_by_node.setdefault(node_id, []).append(chunk)
        for chunks in self.source_chunks_by_path.values():
            chunks.sort(key=lambda item: self._line_value(item.metadata.get("start_line"), item.line) or 0)

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
        return self.context_pack(query, top_k=top_k, max_chars=max_chars).render()

    def context_pack(
        self,
        query: str,
        *,
        top_k: int = 8,
        max_chars: int = 12000,
        required_paths: list[str] | None = None,
    ) -> RAGContextPack:
        candidates = self.search(query, top_k=max(top_k * 4, top_k + 12))
        context_results = self.file_context(
            candidates,
            max_paths=max(1, top_k),
            max_chunks=max(top_k * 4, top_k + 6),
        )
        if required_paths:
            context_results = self._required_path_results(required_paths) + context_results
        blocks: list[RAGContextBlock] = []
        seen: set[str] = set()
        used_chars = 0
        dropped = 0

        def add_block(
            result: RetrievalResult,
            block_chunk: RAGChunk,
            *,
            context_mode: str,
            complete_file: bool,
            allow_truncate: bool,
        ) -> bool:
            nonlocal used_chars, dropped
            if block_chunk.id in seen:
                return True
            start_line = self._line_value(block_chunk.metadata.get("start_line"), block_chunk.line)
            end_line = self._line_value(block_chunk.metadata.get("end_line"), start_line)
            header_chars = 260
            remaining = max_chars - used_chars - header_chars
            if remaining < 200:
                dropped += 1
                return False
            content = block_chunk.text
            if len(content) > remaining:
                if not allow_truncate:
                    dropped += 1
                    return False
                content = content[: max(0, remaining - 40)].rstrip() + "\n...[truncated by context budget]"
            retrieved_from = result.retrieved_from or (
                result.chunk.id
                if result.chunk.id == block_chunk.id
                else f"{result.chunk.id} -> {block_chunk.id}"
            )
            block = RAGContextBlock(
                rank=len(blocks) + 1,
                chunk_id=block_chunk.id,
                chunk_kind=block_chunk.kind,
                title=block_chunk.title,
                path=block_chunk.path,
                start_line=start_line,
                end_line=end_line,
                score=result.score,
                matched_terms=result.matched_terms,
                retrieved_from=retrieved_from,
                content=content,
                context_mode=context_mode,
                complete_file=complete_file,
            )
            blocks.append(block)
            seen.add(block_chunk.id)
            used_chars += len(block.content) + header_chars
            return True

        path_groups: dict[str, list[RetrievalResult]] = {}
        path_order: list[str] = []
        fallback_results: list[RetrievalResult] = []
        for result in context_results:
            focus = result.chunk if result.chunk.kind == "source" else self.source_companion(result.chunk)
            if focus and focus.path and focus.path in self.source_chunks_by_path:
                if focus.path not in path_groups:
                    path_order.append(focus.path)
                path_groups.setdefault(focus.path, []).append(result)
            else:
                fallback_results.append(result)

        for path in path_order:
            group = path_groups[path]
            representative = group[0]
            full_file = self._full_source_file_chunk(path)
            if full_file and add_block(
                representative,
                full_file,
                context_mode="full_file",
                complete_file=bool(full_file.metadata.get("complete_file")),
                allow_truncate=False,
            ):
                continue
            for result in group:
                add_block(
                    result,
                    self._context_chunk(result.chunk),
                    context_mode="partial_file",
                    complete_file=False,
                    allow_truncate=True,
                )

        for result in fallback_results:
            add_block(
                result,
                self._context_chunk(result.chunk),
                context_mode="semantic_chunk",
                complete_file=False,
                allow_truncate=True,
            )
        return RAGContextPack(
            query=query,
            max_chars=max_chars,
            used_chars=min(used_chars, max_chars),
            blocks=blocks,
            required_context_paths=list(dict.fromkeys(required_paths or [])),
            dropped_blocks=dropped,
        )

    def _required_path_results(self, paths: list[str]) -> list[RetrievalResult]:
        results: list[RetrievalResult] = []
        for path in dict.fromkeys(paths):
            for idx, chunk in enumerate(self.source_chunks_by_path.get(path, [])):
                results.append(
                    RetrievalResult(
                        chunk=chunk,
                        score=max(0.45 - idx * 0.03, 0.2),
                        matched_terms=[],
                        retrieved_from=f"required_path:{path}",
                    )
                )
        return results

    def _context_chunk(self, chunk: RAGChunk) -> RAGChunk:
        if chunk.kind == "source":
            return chunk
        companion = self.source_companion(chunk)
        return companion or chunk

    def _full_source_file_chunk(self, path: str) -> RAGChunk | None:
        source_chunks = self.source_chunks_by_path.get(path, [])
        if not source_chunks:
            return None
        numbered_lines: dict[int, str] = {}
        for chunk in source_chunks:
            for line_no, line in self._source_code_lines(chunk):
                numbered_lines.setdefault(line_no, line)
        if not numbered_lines:
            return None
        start_line = min(numbered_lines)
        end_line = max(numbered_lines)
        file_chunk = self._file_chunk(path)
        total_lines = self._line_value(file_chunk.metadata.get("lines") if file_chunk else None, end_line)
        language = (
            str(source_chunks[0].metadata.get("language") or "")
            or str(file_chunk.metadata.get("language") if file_chunk else "")
        )
        contiguous = len(numbered_lines) == end_line - start_line + 1
        complete_file = start_line == 1 and contiguous and (not total_lines or end_line >= total_lines)
        code = [f"{line_no}: {numbered_lines[line_no]}" for line_no in sorted(numbered_lines)]
        return RAGChunk(
            id=f"source-file:{path}",
            kind="source",
            title=f"{path}: full source file",
            text="\n".join(
                [
                    f"Source file: {path}",
                    f"Language: {language or 'unknown'}",
                    f"Line range: {start_line}-{end_line}",
                    f"Complete file: {'yes' if complete_file else 'no'}",
                    "Code:",
                    *code,
                ]
            ),
            path=path,
            line=start_line,
            node_ids=[f"file:{path}"],
            evidence=source_chunks[0].evidence,
            metadata={
                "language": language,
                "start_line": start_line,
                "end_line": end_line,
                "complete_file": complete_file,
                "source_chunk_count": len(source_chunks),
            },
        )

    def _file_chunk(self, path: str) -> RAGChunk | None:
        for chunk in self.chunks_by_path.get(path, []):
            if chunk.kind == "file":
                return chunk
        return None

    @staticmethod
    def _source_code_lines(chunk: RAGChunk) -> list[tuple[int, str]]:
        lines = chunk.text.splitlines()
        try:
            code_start = lines.index("Code:") + 1
        except ValueError:
            code_start = 0
        parsed: list[tuple[int, str]] = []
        for line in lines[code_start:]:
            match = re.match(r"^(\d+): ?(.*)$", line)
            if match:
                parsed.append((int(match.group(1)), match.group(2)))
        return parsed

    @staticmethod
    def _line_value(value: object, fallback: int | None = None) -> int | None:
        if value is None:
            return fallback
        try:
            return int(value)
        except (TypeError, ValueError):
            return fallback

    def file_context(
        self,
        results: list[RetrievalResult],
        *,
        max_paths: int,
        max_chunks: int,
    ) -> list[RetrievalResult]:
        expanded: list[RetrievalResult] = []
        seen_chunks: set[str] = set()
        seen_paths: set[str] = set()
        for result in results:
            focus = result.chunk if result.chunk.kind == "source" else self.source_companion(result.chunk)
            if focus and focus.path:
                if focus.path not in seen_paths and len(seen_paths) >= max_paths:
                    continue
                seen_paths.add(focus.path)
                for source, factor in self._source_window(focus):
                    if source.id in seen_chunks:
                        continue
                    expanded.append(
                        RetrievalResult(
                            chunk=source,
                            score=max(result.score * factor, 0.2),
                            matched_terms=result.matched_terms,
                            retrieved_from=(
                                result.retrieved_from
                                or (
                                    result.chunk.id
                                    if result.chunk.id == source.id
                                    else f"{result.chunk.id} -> {source.id}"
                                )
                            ),
                        )
                    )
                    seen_chunks.add(source.id)
                    if len(expanded) >= max_chunks:
                        return expanded
                continue
            if result.chunk.id not in seen_chunks:
                expanded.append(result)
                seen_chunks.add(result.chunk.id)
                if len(expanded) >= max_chunks:
                    return expanded
        return expanded

    def _source_window(self, focus: RAGChunk) -> list[tuple[RAGChunk, float]]:
        if not focus.path:
            return [(focus, 1.0)]
        source_chunks = self.source_chunks_by_path.get(focus.path, [])
        if not source_chunks:
            return [(focus, 1.0)]
        try:
            focus_index = next(idx for idx, item in enumerate(source_chunks) if item.id == focus.id)
        except StopIteration:
            focus_index = 0
        ordered: list[tuple[int, float]] = [(focus_index, 1.0)]
        if len(source_chunks) <= 3:
            ordered.extend((idx, 0.82) for idx in range(len(source_chunks)) if idx != focus_index)
        else:
            for offset, factor in [(-1, 0.78), (1, 0.78), (-2, 0.62), (2, 0.62)]:
                idx = focus_index + offset
                if 0 <= idx < len(source_chunks):
                    ordered.append((idx, factor))
        seen: set[int] = set()
        window: list[tuple[RAGChunk, float]] = []
        for idx, factor in ordered:
            if idx in seen:
                continue
            seen.add(idx)
            window.append((source_chunks[idx], factor))
        return window

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
                        retrieved_from=f"{result.chunk.id} -> {companion.id}",
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
        source_chunks = self.source_chunks_by_path.get(chunk.path, [])
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
                source_chunks = self.source_chunks_by_path.get(chunk.path, [])
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
                added = RetrievalResult(
                    chunk=neighbor,
                    score=bonus_score,
                    matched_terms=matched,
                    retrieved_from=f"{result.chunk.id} -> {neighbor.id}",
                )
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
