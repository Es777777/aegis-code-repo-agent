from __future__ import annotations

from aegis.llm import LLMClient, LLMError
from aegis.models import Evidence, Finding, RepoKnowledge
from aegis.orchestrator.context import ContextRouter


class LLMRepositoryAnalyst:
    name = "LLMRepositoryAnalyst"

    def __init__(self, client: LLMClient, router: ContextRouter) -> None:
        self.client = client
        self.router = router

    def analyze(self, knowledge: RepoKnowledge) -> list[Finding]:
        if not self.client.available:
            return [
                Finding(
                    agent=self.name,
                    title="LLM 推理未启用",
                    summary="未配置可用文本 LLM，因此本轮只使用静态规则 Agent。配置 AEGIS_LLM_API_KEY、AEGIS_LLM_BASE_URL、AEGIS_LLM_MODEL 后可启用。",
                    severity="low",
                    confidence=0.9,
                    tags=["llm", "disabled"],
                )
            ]
        section_budget = max(1200, self.router.max_chars // 3)
        context = "\n\n".join(
            [
                "=== Architecture Context ===",
                self.router.route("architecture", max_chars=section_budget),
                "=== Interface Context ===",
                self.router.route("interface", max_chars=section_budget),
                "=== Risk Context ===",
                self.router.route("risk", max_chars=section_budget),
            ]
        )
        if len(context) > self.router.max_chars:
            context = context[: max(0, self.router.max_chars - 40)].rstrip() + "\n...[truncated by LLM context budget]"
        system = (
            "你是 AEGIS 的仓库分析 Agent。必须基于给定上下文回答，"
            "不要编造不存在的文件。输出中文，强调架构、接口、风险和待确认问题。"
        )
        user = (
            "请基于以下仓库上下文，给出一段高质量仓库阅读分析。"
            "每条关键结论都尽量引用文件路径或 evidence 行。\n\n"
            f"{context}"
        )
        try:
            text = self.client.complete(system=system, user=user)
        except LLMError as exc:
            return [
                Finding(
                    agent=self.name,
                    title="LLM 推理失败",
                    summary=str(exc),
                    severity="medium",
                    confidence=0.8,
                    tags=["llm", "error"],
                )
            ]
        evidence = [
            Evidence(
                path=item.path,
                line=item.evidence[0].line if item.evidence else 1,
                snippet=item.evidence[0].snippet if item.evidence else f"{item.language} file",
                confidence=0.6,
                source="llm-context-router",
            )
            for item in knowledge.files[:5]
        ]
        return [
            Finding(
                agent=self.name,
                title="LLM 综合分析",
                summary=text,
                severity="info",
                confidence=0.65,
                evidence=evidence,
                tags=["llm", "synthesis"],
            )
        ]
