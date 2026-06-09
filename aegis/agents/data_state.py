from __future__ import annotations

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records


DATA_HINTS = (
    "model",
    "models",
    "schema",
    "schemas",
    "entity",
    "entities",
    "repository",
    "repositories",
    "dao",
    "migration",
    "migrations",
    "database",
    "db",
    "cache",
    "redis",
    "queue",
    "state",
    "store",
)


class DataStateAnalyst(BaseAgent):
    name = "DataStateAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        candidates = [
            record
            for record in knowledge.files
            if _has_data_hint(record.path)
            or any(_has_data_hint(imported.replace(".", "/")) for imported in record.imports)
        ]
        if not candidates:
            return [
                self.finding(
                    "未识别到明显数据状态层",
                    "当前静态线索没有发现数据库模型、缓存、队列或状态管理文件。若项目较小，这可能正常；若是后端项目，建议补充 ORM/框架专用解析。",
                    severity="low",
                    confidence=0.6,
                    tags=["data-state", "gap"],
                )
            ]
        preview = ", ".join(record.path for record in candidates[:8])
        return [
            self.finding(
                "数据与状态层候选",
                f"发现 {len(candidates)} 个数据/状态相关文件候选：{preview}。"
                f"CodeGraph 中有 {len(knowledge.code_graph.data_nodes)} 个 data_model 节点。",
                evidence=evidence_from_records(candidates[:6]),
                tags=["data-state", "codegraph"],
            )
        ]


def _has_data_hint(value: str) -> bool:
    normalized = value.lower().replace("\\", "/").replace("-", "_")
    tokens = set()
    for part in normalized.split("/"):
        stem = part.rsplit(".", 1)[0]
        tokens.update(piece for piece in stem.split("_") if piece)
        tokens.add(stem)
    return any(hint in tokens for hint in DATA_HINTS)
