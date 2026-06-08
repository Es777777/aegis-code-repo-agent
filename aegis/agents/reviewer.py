from __future__ import annotations

from aegis.models import Evidence, Finding, RepoKnowledge


class EvidenceReviewer:
    name = "EvidenceReviewer"

    def review(self, knowledge: RepoKnowledge, findings: list[Finding]) -> list[Finding]:
        reviewed = list(findings)
        missing = [item for item in findings if not item.evidence and item.severity != "low"]
        if missing:
            reviewed.append(
                Finding(
                    agent=self.name,
                    title="证据缺口",
                    summary=f"发现 {len(missing)} 条中高重要性结论缺少源码证据，报告中应降低置信度或触发补查。",
                    severity="medium",
                    confidence=0.8,
                    evidence=[
                        Evidence(
                            path="AEGIS_INTERNAL",
                            line=1,
                            snippet=", ".join(item.title for item in missing[:6]),
                            source="evidence-review",
                        )
                    ],
                    tags=["review", "evidence-gap"],
                )
            )
        duplicate_titles = sorted(
            {item.title for item in findings if sum(1 for other in findings if other.title == item.title) > 1}
        )
        if duplicate_titles:
            reviewed.append(
                Finding(
                    agent=self.name,
                    title="重复结论",
                    summary=f"多个 Agent 产生了相同标题的结论：{', '.join(duplicate_titles[:8])}。建议合并后再输出。",
                    severity="low",
                    confidence=0.7,
                    tags=["review", "dedupe"],
                )
            )
        return reviewed
