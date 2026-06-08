from __future__ import annotations

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records, records_by_path


class InterfaceAnalyst(BaseAgent):
    name = "InterfaceAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        findings: list[Finding] = []
        lookup = records_by_path(knowledge)
        interface_files = [lookup[path] for path in knowledge.interface_catalog if path in lookup]
        count = sum(len(items) for items in knowledge.interface_catalog.values())
        if not interface_files:
            return [
                self.finding(
                    "未发现显式接口目录",
                    "静态扫描没有识别到明显 HTTP/RPC/CLI 接口。若项目使用动态注册或框架约定路由，需要补充框架专用解析器。",
                    severity="low",
                    confidence=0.6,
                    tags=["interface", "gap"],
                )
            ]
        top = sorted(interface_files, key=lambda item: len(item.interfaces), reverse=True)[:8]
        catalog_preview = []
        for record in top:
            catalog_preview.append(f"{record.path}: {', '.join(record.interfaces[:5])}")
        findings.append(
            self.finding(
                "接口目录",
                f"共识别 {count} 个接口候选，主要分布在 {len(interface_files)} 个文件中。"
                f"示例：{' ; '.join(catalog_preview)}。",
                evidence=evidence_from_records(top),
                tags=["interface", "catalog"],
            )
        )
        return findings
