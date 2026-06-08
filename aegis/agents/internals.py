from __future__ import annotations

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records, records_by_path


class InternalsAnalyst(BaseAgent):
    name = "InternalsAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        lookup = records_by_path(knowledge)
        symbol_rich = sorted(
            knowledge.files,
            key=lambda item: (len(item.symbols), item.lines),
            reverse=True,
        )[:8]
        if not symbol_rich:
            return []
        preview = "; ".join(
            f"{record.path}: {len(record.symbols)} symbols" for record in symbol_rich[:5]
        )
        findings = [
            self.finding(
                "核心实现候选",
                f"符号密度较高的文件可能承载主要实现逻辑：{preview}。",
                evidence=evidence_from_records(symbol_rich),
                tags=["internals", "symbols"],
            )
        ]
        long_files = [record for record in knowledge.files if record.lines >= 350]
        if long_files:
            findings.append(
                self.finding(
                    "长文件实现热点",
                    f"发现 {len(long_files)} 个超过 350 行的文件，建议在报告中展开调用链和职责拆分。",
                    severity="medium",
                    evidence=evidence_from_records(long_files[:5]),
                    tags=["internals", "complexity"],
                )
            )
        return findings
