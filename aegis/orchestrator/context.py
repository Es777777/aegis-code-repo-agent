from __future__ import annotations

from aegis.models import Evidence, FileRecord, RepoKnowledge


class ContextRouter:
    """Selects the smallest useful repository context for each analysis role."""

    def __init__(self, knowledge: RepoKnowledge, *, max_chars: int = 14000) -> None:
        self.knowledge = knowledge
        self.max_chars = max_chars
        self.lookup = {record.path: record for record in knowledge.files}

    def route(self, role: str) -> str:
        role = role.lower()
        if "interface" in role:
            paths = list(self.knowledge.interface_catalog) or self.knowledge.repo_map[:10]
        elif "runtime" in role or "build" in role:
            paths = self.knowledge.configs + self.knowledge.entrypoints
        elif "risk" in role:
            paths = self.knowledge.repo_map[:20]
        elif "data" in role:
            paths = [
                record.path
                for record in self.knowledge.files
                if any(token in record.path.lower() for token in ("model", "schema", "repository", "dao", "store", "db"))
            ]
        else:
            paths = self.knowledge.repo_map[:16]
        return self._render(paths)

    def _render(self, paths: list[str]) -> str:
        lines: list[str] = []
        lines.append(f"Repo: {self.knowledge.repo_name}")
        lines.append(f"Frameworks: {', '.join(self.knowledge.frameworks) or 'unknown'}")
        lines.append(f"Entrypoints: {', '.join(self.knowledge.entrypoints[:10]) or 'unknown'}")
        lines.append(
            "CodeGraph: "
            f"{self.knowledge.code_graph.stats.get('node_count', 0)} nodes, "
            f"{self.knowledge.code_graph.stats.get('edge_count', 0)} edges"
        )
        lines.append("")
        for path in paths:
            record = self.lookup.get(path)
            if not record:
                continue
            lines.extend(self._record(record))
            if len("\n".join(lines)) >= self.max_chars:
                break
        return "\n".join(lines)[: self.max_chars]

    @staticmethod
    def _record(record: FileRecord) -> list[str]:
        lines = [
            f"FILE {record.path}",
            f"  language={record.language} lines={record.lines}",
        ]
        if record.imports:
            lines.append(f"  imports={', '.join(record.imports[:12])}")
        if record.symbols:
            lines.append(f"  symbols={', '.join(record.symbols[:16])}")
        if record.interfaces:
            lines.append(f"  interfaces={', '.join(record.interfaces[:12])}")
        for ev in record.evidence[:4]:
            lines.append(f"  evidence {ev.path}:{ev.line} {ev.snippet}")
        lines.append("")
        return lines
