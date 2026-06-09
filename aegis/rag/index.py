from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from aegis.models import CodeGraphEdge, CodeGraphNode, Evidence, FileRecord, RepoKnowledge


@dataclass
class RAGChunk:
    id: str
    kind: str
    title: str
    text: str
    path: str | None = None
    line: int | None = None
    node_ids: list[str] = field(default_factory=list)
    edge_ids: list[str] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class RAGIndex:
    repo_name: str
    chunks: list[RAGChunk]
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RAGIndexBuilder:
    def __init__(self, knowledge: RepoKnowledge) -> None:
        self.knowledge = knowledge
        self.nodes = {node.id: node for node in knowledge.code_graph.nodes}
        self.edges = knowledge.code_graph.edges
        self.files = {record.path: record for record in knowledge.files}

    def build(self) -> RAGIndex:
        chunks: list[RAGChunk] = []
        chunks.extend(self._repo_overview_chunks())
        chunks.extend(self._file_chunks())
        chunks.extend(self._symbol_chunks())
        chunks.extend(self._interface_chunks())
        chunks.extend(self._data_chunks())
        chunks.extend(self._edge_chunks())
        return RAGIndex(
            repo_name=self.knowledge.repo_name,
            chunks=chunks,
            stats={
                "chunk_count": len(chunks),
                "chunk_kinds": self._count_kinds(chunks),
            },
        )

    def _repo_overview_chunks(self) -> list[RAGChunk]:
        text = "\n".join(
            [
                f"Repository: {self.knowledge.repo_name}",
                f"Frameworks: {', '.join(self.knowledge.frameworks) or 'unknown'}",
                f"Entrypoints: {', '.join(self.knowledge.entrypoints) or 'unknown'}",
                f"Configs: {', '.join(self.knowledge.configs) or 'none'}",
                f"Repo Map: {', '.join(self.knowledge.repo_map[:20])}",
                f"CodeGraph nodes: {self.knowledge.code_graph.stats.get('node_count', 0)}",
                f"CodeGraph edges: {self.knowledge.code_graph.stats.get('edge_count', 0)}",
            ]
        )
        return [
            RAGChunk(
                id="repo:overview",
                kind="repo_overview",
                title=f"{self.knowledge.repo_name} overview",
                text=text,
                metadata={"repo": self.knowledge.repo_name},
            )
        ]

    def _file_chunks(self) -> list[RAGChunk]:
        chunks: list[RAGChunk] = []
        for record in self.knowledge.files:
            text = "\n".join(
                [
                    f"File: {record.path}",
                    f"Language: {record.language}",
                    f"Lines: {record.lines}",
                    f"Imports: {', '.join(record.imports) or 'none'}",
                    f"Symbols: {', '.join(record.symbols) or 'none'}",
                    f"Interfaces: {', '.join(record.interfaces) or 'none'}",
                    f"Calls: {', '.join(record.calls[:40]) or 'none'}",
                    "Evidence:",
                    *[f"- {ev.path}:{ev.line} {ev.snippet}" for ev in record.evidence[:8]],
                ]
            )
            chunks.append(
                RAGChunk(
                    id=f"file:{record.path}",
                    kind="file",
                    title=record.path,
                    text=text,
                    path=record.path,
                    line=1,
                    node_ids=[f"file:{record.path}"],
                    evidence=record.evidence[:8],
                    metadata={"language": record.language, "lines": record.lines},
                )
            )
        return chunks

    def _symbol_chunks(self) -> list[RAGChunk]:
        chunks: list[RAGChunk] = []
        for node in self.knowledge.code_graph.nodes:
            if node.kind not in {"class", "function"}:
                continue
            related = self._related_edges(node.id)
            text = "\n".join(
                [
                    f"{node.kind.title()}: {node.name}",
                    f"Path: {node.path}",
                    f"Line: {node.line}",
                    f"Language: {node.language}",
                    f"Qualified name: {node.metadata.get('qualified_name', '')}",
                    "Relations:",
                    *[self._edge_text(edge) for edge in related[:12]],
                ]
            )
            chunks.append(
                RAGChunk(
                    id=f"node:{node.id}",
                    kind=node.kind,
                    title=f"{node.kind}: {node.name}",
                    text=text,
                    path=node.path,
                    line=node.line,
                    node_ids=[node.id],
                    edge_ids=[self._edge_id(edge) for edge in related[:12]],
                    evidence=self._node_evidence(node),
                    metadata=dict(node.metadata),
                )
            )
        return chunks

    def _interface_chunks(self) -> list[RAGChunk]:
        chunks: list[RAGChunk] = []
        for node in self.knowledge.code_graph.nodes:
            if node.kind != "interface":
                continue
            related = self._related_edges(node.id)
            route = str(node.metadata.get("route", node.name))
            text = "\n".join(
                [
                    f"Interface: {node.name}",
                    f"Route: {route}",
                    f"Method: {node.metadata.get('method', '')}",
                    f"Declared in: {node.path}:{node.line}",
                    "Trace relations:",
                    *[self._edge_text(edge) for edge in related[:16]],
                ]
            )
            chunks.append(
                RAGChunk(
                    id=f"interface:{node.id}",
                    kind="interface",
                    title=f"Interface {node.name}",
                    text=text,
                    path=node.path,
                    line=node.line,
                    node_ids=[node.id],
                    edge_ids=[self._edge_id(edge) for edge in related],
                    evidence=self._node_evidence(node),
                    metadata=dict(node.metadata),
                )
            )
        return chunks

    def _data_chunks(self) -> list[RAGChunk]:
        chunks: list[RAGChunk] = []
        for node in self.knowledge.code_graph.nodes:
            if node.kind != "data_model":
                continue
            related = self._related_edges(node.id)
            chunks.append(
                RAGChunk(
                    id=f"data:{node.id}",
                    kind="data_model",
                    title=f"Data model {node.name}",
                    text="\n".join(
                        [
                            f"Data model: {node.name}",
                            f"Path: {node.path}",
                            "Relations:",
                            *[self._edge_text(edge) for edge in related[:12]],
                        ]
                    ),
                    path=node.path,
                    line=node.line,
                    node_ids=[node.id],
                    edge_ids=[self._edge_id(edge) for edge in related],
                    evidence=self._node_evidence(node),
                    metadata=dict(node.metadata),
                )
            )
        return chunks

    def _edge_chunks(self) -> list[RAGChunk]:
        chunks: list[RAGChunk] = []
        important = {"imports", "calls", "calls_file", "routes_to", "defines_data", "configured_by"}
        for edge in self.edges:
            if edge.kind not in important:
                continue
            source = self.nodes.get(edge.source)
            target = self.nodes.get(edge.target)
            if not source or not target:
                continue
            chunks.append(
                RAGChunk(
                    id=f"edge:{self._edge_id(edge)}",
                    kind=f"edge:{edge.kind}",
                    title=f"{source.name} {edge.kind} {target.name}",
                    text=self._edge_text(edge),
                    path=edge.evidence.path if edge.evidence else source.path,
                    line=edge.evidence.line if edge.evidence else source.line,
                    node_ids=[edge.source, edge.target],
                    edge_ids=[self._edge_id(edge)],
                    evidence=[edge.evidence] if edge.evidence else [],
                    metadata={"kind": edge.kind, "confidence": edge.confidence},
                )
            )
        return chunks

    def _related_edges(self, node_id: str) -> list[CodeGraphEdge]:
        return [
            edge
            for edge in self.edges
            if edge.source == node_id or edge.target == node_id
        ]

    def _edge_text(self, edge: CodeGraphEdge) -> str:
        source = self.nodes.get(edge.source)
        target = self.nodes.get(edge.target)
        source_name = source.name if source else edge.source
        target_name = target.name if target else edge.target
        ev = f" evidence={edge.evidence.path}:{edge.evidence.line}" if edge.evidence else ""
        return f"{source_name} --{edge.kind}--> {target_name}{ev}"

    def _node_evidence(self, node: CodeGraphNode) -> list[Evidence]:
        if not node.path:
            return []
        record = self.files.get(node.path)
        if not record:
            return []
        if node.line:
            for ev in record.evidence:
                if ev.line == node.line:
                    return [ev]
        return record.evidence[:3]

    @staticmethod
    def _edge_id(edge: CodeGraphEdge) -> str:
        return f"{edge.source}|{edge.kind}|{edge.target}"

    @staticmethod
    def _count_kinds(chunks: list[RAGChunk]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for chunk in chunks:
            counts[chunk.kind] = counts.get(chunk.kind, 0) + 1
        return counts
