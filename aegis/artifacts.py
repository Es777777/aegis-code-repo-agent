from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from aegis.models import (
    AnalysisResult,
    CodeGraph,
    CodeGraphEdge,
    CodeGraphNode,
    Evidence,
    FileRecord,
    Finding,
    RepoKnowledge,
)
from aegis.rag.index import RAGChunk, RAGIndex


def load_analysis_result(output_dir: Path) -> AnalysisResult:
    knowledge = load_knowledge(output_dir / "knowledge.json")
    findings_path = output_dir / "findings.json"
    findings = load_findings(findings_path) if findings_path.exists() else []
    return AnalysisResult(knowledge=knowledge, findings=findings, output_dir=output_dir)


def load_knowledge(path: Path) -> RepoKnowledge:
    raw = _read_json(path)
    code_graph = raw["code_graph"]
    return RepoKnowledge(
        root=raw["root"],
        repo_name=raw["repo_name"],
        files=[_file_record(item) for item in raw.get("files", [])],
        frameworks=list(raw.get("frameworks", [])),
        entrypoints=list(raw.get("entrypoints", [])),
        configs=list(raw.get("configs", [])),
        changed_files=list(raw.get("changed_files", [])),
        repo_map=list(raw.get("repo_map", [])),
        dependency_graph={key: list(value) for key, value in raw.get("dependency_graph", {}).items()},
        call_graph={key: list(value) for key, value in raw.get("call_graph", {}).items()},
        code_graph=CodeGraph(
            nodes=[CodeGraphNode(**item) for item in code_graph.get("nodes", [])],
            edges=[_code_graph_edge(item) for item in code_graph.get("edges", [])],
            entrypoints=list(code_graph.get("entrypoints", [])),
            interfaces=list(code_graph.get("interfaces", [])),
            data_nodes=list(code_graph.get("data_nodes", [])),
            stats=dict(code_graph.get("stats", {})),
        ),
        interface_catalog={key: list(value) for key, value in raw.get("interface_catalog", {}).items()},
        evidence_store=[_evidence(item) for item in raw.get("evidence_store", [])],
        stats=dict(raw.get("stats", {})),
    )


def load_findings(path: Path) -> list[Finding]:
    return [
        Finding(
            agent=item["agent"],
            title=item["title"],
            summary=item["summary"],
            severity=item.get("severity", "info"),
            confidence=float(item.get("confidence", 0.75)),
            evidence=[_evidence(ev) for ev in item.get("evidence", [])],
            tags=list(item.get("tags", [])),
        )
        for item in _read_json(path)
    ]


def load_rag_index(path: Path) -> RAGIndex:
    raw = _read_json(path)
    return RAGIndex(
        repo_name=raw["repo_name"],
        chunks=[
            RAGChunk(
                id=item["id"],
                kind=item["kind"],
                title=item["title"],
                text=item["text"],
                path=item.get("path"),
                line=item.get("line"),
                node_ids=list(item.get("node_ids", [])),
                edge_ids=list(item.get("edge_ids", [])),
                evidence=[_evidence(ev) for ev in item.get("evidence", [])],
                metadata=dict(item.get("metadata", {})),
            )
            for item in raw.get("chunks", [])
        ],
        stats=dict(raw.get("stats", {})),
    )


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _evidence(raw: dict[str, Any]) -> Evidence:
    return Evidence(
        path=raw["path"],
        line=int(raw["line"]),
        snippet=raw["snippet"],
        confidence=float(raw.get("confidence", 0.75)),
        source=raw.get("source", "static-scan"),
    )


def _file_record(raw: dict[str, Any]) -> FileRecord:
    return FileRecord(
        path=raw["path"],
        language=raw["language"],
        size=int(raw["size"]),
        lines=int(raw["lines"]),
        content_hash=raw["content_hash"],
        cached=bool(raw.get("cached", False)),
        imports=list(raw.get("imports", [])),
        symbols=list(raw.get("symbols", [])),
        interfaces=list(raw.get("interfaces", [])),
        calls=list(raw.get("calls", [])),
        evidence=[_evidence(item) for item in raw.get("evidence", [])],
    )


def _code_graph_edge(raw: dict[str, Any]) -> CodeGraphEdge:
    evidence = raw.get("evidence")
    return CodeGraphEdge(
        source=raw["source"],
        target=raw["target"],
        kind=raw["kind"],
        evidence=_evidence(evidence) if evidence else None,
        confidence=float(raw.get("confidence", 0.75)),
        metadata=dict(raw.get("metadata", {})),
    )
