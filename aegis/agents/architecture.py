from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path

from aegis.models import Finding, RepoKnowledge

from .base import BaseAgent, evidence_from_records, records_by_path, top_records


class ArchitectureAnalyst(BaseAgent):
    name = "ArchitectureAnalyst"

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        findings: list[Finding] = []
        lookup = records_by_path(knowledge)
        important = top_records(knowledge, knowledge.repo_map, limit=8)
        layers = self._layers(knowledge)
        frameworks = ", ".join(knowledge.frameworks) if knowledge.frameworks else "未识别到明显框架"
        findings.append(
            self.finding(
                "仓库主干结构",
                f"仓库包含 {knowledge.stats.get('file_count', 0)} 个可分析文件，主要框架/技术线索：{frameworks}。"
                f"高优先级文件集中在：{', '.join(knowledge.repo_map[:8]) or '暂无'}。"
                f"CodeGraph 包含 {knowledge.code_graph.stats.get('node_count', 0)} 个节点、"
                f"{knowledge.code_graph.stats.get('edge_count', 0)} 条边。",
                evidence=evidence_from_records(important),
                tags=["architecture", "repo-map", "codegraph"],
            )
        )
        if layers:
            summary = "; ".join(f"{name}: {count} 个文件" for name, count in layers.items())
            findings.append(
                self.finding(
                    "模块分层线索",
                    f"按目录命名推断的分层为：{summary}。这可作为初版架构图的模块边界。",
                    evidence=evidence_from_records([lookup[p] for p in knowledge.repo_map[:8] if p in lookup]),
                    tags=["architecture", "layers"],
                )
            )
        high_fanout = sorted(
            knowledge.dependency_graph.items(),
            key=lambda item: len(item[1]),
            reverse=True,
        )[:5]
        if high_fanout:
            paths = [path for path, _ in high_fanout if path in lookup]
            summary = "; ".join(f"{path} -> {len(deps)} deps" for path, deps in high_fanout)
            findings.append(
                self.finding(
                    "依赖中心文件",
                    f"这些文件依赖较多，可能是架构入口或耦合热点：{summary}。",
                    severity="medium" if any(len(deps) > 15 for _, deps in high_fanout) else "info",
                    evidence=evidence_from_records([lookup[p] for p in paths]),
                    tags=["architecture", "dependency-graph"],
                )
            )
        return findings

    @staticmethod
    def _layers(knowledge: RepoKnowledge) -> dict[str, int]:
        counters: Counter[str] = Counter()
        markers = {
            "api/interface": ["api", "route", "controller", "handler"],
            "domain/service": ["service", "domain", "usecase", "application"],
            "data/repository": ["repository", "dao", "model", "entity", "schema"],
            "ui/frontend": ["component", "pages", "views", "frontend", "client"],
            "tests": ["test", "spec"],
            "config/runtime": ["config", "deploy", "docker", "workflow"],
        }
        for record in knowledge.files:
            parts = {part.lower() for part in Path(record.path).parts}
            for layer, hints in markers.items():
                if any(hint in part for hint in hints for part in parts):
                    counters[layer] += 1
        return dict(counters.most_common())
