from __future__ import annotations

from collections import Counter, defaultdict, deque
from pathlib import Path
import re

from aegis.models import CodeGraph, CodeGraphEdge, CodeGraphNode, Evidence, FileRecord


DATA_PATH_HINTS = {
    "model",
    "models",
    "schema",
    "schemas",
    "entity",
    "entities",
    "repository",
    "repositories",
    "dao",
    "db",
    "database",
    "migration",
    "migrations",
    "store",
}

CONFIG_NODE_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
}


class CodeGraphBuilder:
    """Builds a repository-level code graph from static scan records.

    This is deliberately dependency-free. It creates a complete graph contract
    today, while leaving room to swap the extraction backend for Tree-sitter or
    LSP later.
    """

    def __init__(
        self,
        files: list[FileRecord],
        *,
        dependency_graph: dict[str, list[str]],
        call_graph: dict[str, list[str]],
        entrypoints: list[str],
        configs: list[str],
    ) -> None:
        self.files = files
        self.dependency_graph = dependency_graph
        self.call_graph = call_graph
        self.entrypoints = entrypoints
        self.configs = configs
        self.nodes: dict[str, CodeGraphNode] = {}
        self.edges: dict[tuple[str, str, str], CodeGraphEdge] = {}
        self.symbol_index: dict[str, list[str]] = defaultdict(list)
        self.interface_nodes: list[str] = []
        self.data_nodes: list[str] = []

    def build(self) -> CodeGraph:
        for record in self.files:
            self._add_file(record)
        for record in self.files:
            self._add_symbols(record)
            self._add_interfaces(record)
            self._add_config_or_data_nodes(record)
        for record in self.files:
            self._add_import_edges(record)
            self._add_call_edges(record)
            self._add_config_edges(record)

        kind_counts = Counter(node.kind for node in self.nodes.values())
        edge_counts = Counter(edge.kind for edge in self.edges.values())
        return CodeGraph(
            nodes=list(self.nodes.values()),
            edges=list(self.edges.values()),
            entrypoints=[self._file_id(path) for path in self.entrypoints if self._file_id(path) in self.nodes],
            interfaces=self.interface_nodes,
            data_nodes=self.data_nodes,
            stats={
                "node_count": len(self.nodes),
                "edge_count": len(self.edges),
                "node_kinds": dict(kind_counts.most_common()),
                "edge_kinds": dict(edge_counts.most_common()),
            },
        )

    def _add_file(self, record: FileRecord) -> None:
        self._node(
            self._file_id(record.path),
            "file",
            record.path,
            path=record.path,
            language=record.language,
            metadata={
                "lines": record.lines,
                "size": record.size,
                "cached": record.cached,
                "content_hash": record.content_hash,
            },
        )
        module_name = self._module_name(record.path)
        if module_name:
            module_id = self._module_id(module_name)
            self._node(module_id, "module", module_name, path=record.path, language=record.language)
            self._edge(module_id, self._file_id(record.path), "contains_file", self._evidence(record))

    def _add_symbols(self, record: FileRecord) -> None:
        file_id = self._file_id(record.path)
        for symbol in record.symbols:
            symbol_id = self._symbol_id(record.path, symbol)
            line = self._line_for(record, symbol)
            self._node(
                symbol_id,
                self._symbol_kind(symbol),
                symbol,
                path=record.path,
                line=line,
                language=record.language,
                metadata={"qualified_name": f"{self._module_name(record.path)}.{symbol}"},
            )
            self.symbol_index[symbol].append(symbol_id)
            self._edge(file_id, symbol_id, "defines", self._evidence(record, symbol))
            self._edge(symbol_id, file_id, "defined_in", self._evidence(record, symbol))

    def _add_interfaces(self, record: FileRecord) -> None:
        file_id = self._file_id(record.path)
        for raw in record.interfaces:
            method, route = self._split_interface(raw)
            interface_id = self._interface_id(record.path, method, route)
            line = self._line_for(record, route)
            self._node(
                interface_id,
                "interface",
                f"{method} {route}".strip(),
                path=record.path,
                line=line,
                language=record.language,
                metadata={"method": method, "route": route},
            )
            self.interface_nodes.append(interface_id)
            self._edge(file_id, interface_id, "exposes", self._evidence(record, route))
            self._edge(interface_id, file_id, "declared_in", self._evidence(record, route))
            handler = self._nearest_symbol(record, line)
            if handler:
                self._edge(interface_id, self._symbol_id(record.path, handler), "routes_to", self._evidence(record, route))

    def _add_config_or_data_nodes(self, record: FileRecord) -> None:
        file_id = self._file_id(record.path)
        if Path(record.path).name in CONFIG_NODE_NAMES or record.path in self.configs:
            config_id = self._typed_id("config", record.path)
            self._node(config_id, "config", record.path, path=record.path, language=record.language)
            self._edge(config_id, file_id, "describes", self._evidence(record))
        if self._is_data_file(record.path):
            data_id = self._typed_id("data", record.path)
            self._node(data_id, "data_model", Path(record.path).stem, path=record.path, language=record.language)
            self.data_nodes.append(data_id)
            self._edge(file_id, data_id, "defines_data", self._evidence(record))

    def _add_import_edges(self, record: FileRecord) -> None:
        source = self._file_id(record.path)
        for dep in self.dependency_graph.get(record.path, []):
            target = self._file_id(dep) if dep in self.dependency_graph else self._external_id(dep)
            if dep not in self.dependency_graph:
                self._node(target, "external_module", dep)
            self._edge(source, target, "imports", self._evidence(record), confidence=0.8)

    def _add_call_edges(self, record: FileRecord) -> None:
        source_file = self._file_id(record.path)
        for target_file in self.call_graph.get(record.path, []):
            self._edge(source_file, self._file_id(target_file), "calls_file", self._evidence(record), confidence=0.55)
        for call in record.calls:
            for symbol_id in self.symbol_index.get(call, [])[:5]:
                if not symbol_id.startswith(f"symbol:{record.path}:"):
                    self._edge(source_file, symbol_id, "calls", self._evidence(record, call), confidence=0.55)

    def _add_config_edges(self, record: FileRecord) -> None:
        if record.path not in self.entrypoints:
            return
        source = self._file_id(record.path)
        for config in self.configs:
            self._edge(source, self._file_id(config), "configured_by", self._evidence(record), confidence=0.5)

    def _node(
        self,
        node_id: str,
        kind: str,
        name: str,
        *,
        path: str | None = None,
        line: int | None = None,
        language: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> None:
        if node_id in self.nodes:
            return
        self.nodes[node_id] = CodeGraphNode(
            id=node_id,
            kind=kind,
            name=name,
            path=path,
            line=line,
            language=language,
            metadata=dict(metadata or {}),
        )

    def _edge(
        self,
        source: str,
        target: str,
        kind: str,
        evidence: Evidence | None = None,
        *,
        confidence: float = 0.75,
        metadata: dict[str, object] | None = None,
    ) -> None:
        key = (source, target, kind)
        if key in self.edges:
            return
        self.edges[key] = CodeGraphEdge(
            source=source,
            target=target,
            kind=kind,
            evidence=evidence,
            confidence=confidence,
            metadata=dict(metadata or {}),
        )

    @staticmethod
    def _file_id(path: str) -> str:
        return f"file:{path}"

    @staticmethod
    def _module_id(name: str) -> str:
        return f"module:{name}"

    @staticmethod
    def _symbol_id(path: str, symbol: str) -> str:
        return f"symbol:{path}:{symbol}"

    @staticmethod
    def _interface_id(path: str, method: str, route: str) -> str:
        route_slug = re.sub(r"[^A-Za-z0-9_/-]+", "_", route).strip("_") or "root"
        return f"interface:{path}:{method}:{route_slug}"

    @staticmethod
    def _external_id(name: str) -> str:
        return f"external:{name}"

    @staticmethod
    def _typed_id(kind: str, path: str) -> str:
        return f"{kind}:{path}"

    @staticmethod
    def _module_name(path: str) -> str:
        return Path(path).with_suffix("").as_posix().replace("/", ".")

    @staticmethod
    def _split_interface(value: str) -> tuple[str, str]:
        parts = value.split(maxsplit=1)
        if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
            return parts[0].upper(), parts[1]
        return "ROUTE", value

    @staticmethod
    def _symbol_kind(symbol: str) -> str:
        if symbol[:1].isupper():
            return "class"
        return "function"

    @staticmethod
    def _is_data_file(path: str) -> bool:
        tokens = set()
        for part in path.lower().replace("\\", "/").replace("-", "_").split("/"):
            stem = part.rsplit(".", 1)[0]
            tokens.add(stem)
            tokens.update(piece for piece in stem.split("_") if piece)
        return any(token in DATA_PATH_HINTS for token in tokens)

    @staticmethod
    def _line_for(record: FileRecord, needle: str) -> int | None:
        for ev in record.evidence:
            if needle and needle in ev.snippet:
                return ev.line
        return record.evidence[0].line if record.evidence else None

    def _nearest_symbol(self, record: FileRecord, line: int | None) -> str | None:
        if not record.symbols:
            return None
        if line is None:
            return record.symbols[0]
        after: list[tuple[int, str]] = []
        before: list[tuple[int, str]] = []
        for symbol in record.symbols:
            symbol_line = self._line_for(record, symbol)
            if symbol_line is None:
                continue
            if symbol_line >= line:
                after.append((symbol_line - line, symbol))
            else:
                before.append((line - symbol_line, symbol))
        if after:
            return sorted(after)[0][1]
        if before:
            return sorted(before)[0][1]
        return record.symbols[0]

    @staticmethod
    def _evidence(record: FileRecord, needle: str | None = None) -> Evidence:
        if needle:
            for ev in record.evidence:
                if needle in ev.snippet:
                    return ev
        if record.evidence:
            return record.evidence[0]
        return Evidence(
            path=record.path,
            line=1,
            snippet=f"{record.language} file with {record.lines} lines",
            confidence=0.55,
            source="codegraph",
        )


class CodeGraphQuery:
    TRACE_EDGE_KINDS = {
        "routes_to",
        "declared_in",
        "defined_in",
        "imports",
        "calls",
        "calls_file",
        "defines_data",
        "configured_by",
    }

    def __init__(self, graph: CodeGraph) -> None:
        self.graph = graph
        self.nodes = {node.id: node for node in graph.nodes}
        self.out_edges: dict[str, list[CodeGraphEdge]] = defaultdict(list)
        self.in_edges: dict[str, list[CodeGraphEdge]] = defaultdict(list)
        for edge in graph.edges:
            self.out_edges[edge.source].append(edge)
            self.in_edges[edge.target].append(edge)

    def nodes_by_kind(self, kind: str) -> list[CodeGraphNode]:
        return [node for node in self.graph.nodes if node.kind == kind]

    def edges_by_kind(self, kind: str) -> list[CodeGraphEdge]:
        return [edge for edge in self.graph.edges if edge.kind == kind]

    def trace_interface(self, route_or_method: str, *, max_depth: int = 6) -> list[CodeGraphNode]:
        needle = route_or_method.lower()
        starts = [
            node
            for node in self.nodes_by_kind("interface")
            if needle in node.name.lower() or needle in str(node.metadata.get("route", "")).lower()
        ]
        if not starts:
            return []
        return self._bfs(starts[0].id, max_depth=max_depth, edge_kinds=self.TRACE_EDGE_KINDS)

    def impacted_by_files(self, paths: list[str], *, max_depth: int = 3) -> list[CodeGraphNode]:
        starts = [f"file:{path}" for path in paths if f"file:{path}" in self.nodes]
        seen: set[str] = set()
        ordered: list[CodeGraphNode] = []
        for start in starts:
            for node in self._reverse_bfs(start, max_depth=max_depth):
                if node.id not in seen:
                    seen.add(node.id)
                    ordered.append(node)
        return ordered

    def _bfs(
        self,
        start: str,
        *,
        max_depth: int,
        edge_kinds: set[str] | None = None,
    ) -> list[CodeGraphNode]:
        result: list[CodeGraphNode] = []
        seen = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            node_id, depth = queue.popleft()
            node = self.nodes.get(node_id)
            if node:
                result.append(node)
            if depth >= max_depth:
                continue
            edges = self.out_edges.get(node_id, [])
            if edge_kinds is not None:
                edges = [edge for edge in edges if edge.kind in edge_kinds]
            for edge in self._ordered_edges(edges):
                if edge.target not in seen:
                    seen.add(edge.target)
                    queue.append((edge.target, depth + 1))
        return result

    @staticmethod
    def _ordered_edges(edges: list[CodeGraphEdge]) -> list[CodeGraphEdge]:
        priority = {
            "routes_to": 0,
            "declared_in": 1,
            "defined_in": 2,
            "calls": 3,
            "calls_file": 4,
            "imports": 5,
            "defines_data": 6,
            "configured_by": 7,
        }
        return sorted(edges, key=lambda edge: (priority.get(edge.kind, 99), edge.target))

    def _reverse_bfs(self, start: str, *, max_depth: int) -> list[CodeGraphNode]:
        result: list[CodeGraphNode] = []
        seen = {start}
        queue: deque[tuple[str, int]] = deque([(start, 0)])
        while queue:
            node_id, depth = queue.popleft()
            node = self.nodes.get(node_id)
            if node:
                result.append(node)
            if depth >= max_depth:
                continue
            for edge in self.in_edges.get(node_id, []):
                if edge.source not in seen:
                    seen.add(edge.source)
                    queue.append((edge.source, depth + 1))
        return result
