from __future__ import annotations

from pathlib import Path

from aegis.models import FileRecord, RepoKnowledge
from aegis.utils import read_text


class ContextRouter:
    """Selects the smallest useful repository context for each analysis role."""

    SOURCE_READ_BYTES = 180_000

    def __init__(self, knowledge: RepoKnowledge, *, max_chars: int = 14000) -> None:
        self.knowledge = knowledge
        self.max_chars = max_chars
        self.lookup = {record.path: record for record in knowledge.files}
        self.root = Path(knowledge.root)

    def route(self, role: str, *, max_chars: int | None = None) -> str:
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
        return self._render(paths, max_chars=max_chars or self.max_chars)

    def _render(self, paths: list[str], *, max_chars: int) -> str:
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
        for path in dict.fromkeys(paths):
            record = self.lookup.get(path)
            if not record:
                continue
            current = "\n".join(lines)
            remaining = max_chars - len(current)
            if remaining < 300:
                break
            lines.extend(self._record(record, remaining))
        return "\n".join(lines)[:max_chars]

    def _record(self, record: FileRecord, budget: int) -> list[str]:
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
        source_budget = budget - len("\n".join(lines)) - 80
        source_lines = self._source_context(record, max_chars=source_budget)
        if source_lines:
            lines.extend(source_lines)
        lines.append("")
        return lines

    def _source_context(self, record: FileRecord, *, max_chars: int) -> list[str]:
        if max_chars < 240:
            return []
        try:
            text = read_text(self.root / record.path, max_bytes=self.SOURCE_READ_BYTES)
        except (OSError, UnicodeDecodeError):
            return []
        source_lines = text.splitlines()
        if not source_lines:
            return []
        header = [
            "  source_context:",
            f"  Source file: {record.path}",
            f"  Language: {record.language}",
            f"  Line range: 1-{len(source_lines)}",
        ]
        numbered = [f"  {line_no}: {line}" for line_no, line in enumerate(source_lines, start=1)]
        complete_text = "\n".join([*header, "  Complete file: yes", "  Code:", *numbered])
        if len(complete_text) <= max_chars:
            return [*header, "  Complete file: yes", "  Code:", *numbered]

        truncated_header = [*header, "  Complete file: no", "  Code:"]
        used = len("\n".join(truncated_header))
        kept: list[str] = []
        for line in numbered:
            next_used = used + len(line) + 1
            if next_used > max_chars - 42:
                break
            kept.append(line)
            used = next_used
        if not kept:
            return []
        kept.append("  ...[truncated by context budget]")
        return [*truncated_header, *kept]
