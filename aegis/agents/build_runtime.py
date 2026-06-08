from __future__ import annotations

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records, records_by_path


class BuildRuntimeAnalyst(BaseAgent):
    name = "BuildRuntimeAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        lookup = records_by_path(knowledge)
        config_records = [lookup[path] for path in knowledge.configs if path in lookup]
        entry_records = [lookup[path] for path in knowledge.entrypoints if path in lookup]
        findings: list[Finding] = []
        if config_records:
            findings.append(
                self.finding(
                    "构建与运行配置",
                    f"发现 {len(config_records)} 个配置/构建文件：{', '.join(knowledge.configs[:12])}。",
                    evidence=evidence_from_records(config_records[:6]),
                    tags=["runtime", "config"],
                )
            )
        else:
            findings.append(
                self.finding(
                    "缺少显式运行配置",
                    "没有发现常见 package、构建、Docker 或 CI 配置文件，运行方式可能依赖文档或外部环境。",
                    severity="medium",
                    confidence=0.65,
                    tags=["runtime", "gap"],
                )
            )
        if entry_records:
            findings.append(
                self.finding(
                    "入口文件候选",
                    f"入口文件候选：{', '.join(knowledge.entrypoints[:12])}。",
                    evidence=evidence_from_records(entry_records[:6]),
                    tags=["runtime", "entrypoint"],
                )
            )
        return findings
