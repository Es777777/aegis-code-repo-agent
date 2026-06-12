from __future__ import annotations

from datetime import datetime
from pathlib import Path

from aegis.agents import (
    ArchitectureAnalyst,
    BuildRuntimeAnalyst,
    DataStateAnalyst,
    InterfaceAnalyst,
    InternalsAnalyst,
    RiskAnalyst,
)
from aegis.agents.reviewer import EvidenceReviewer
from aegis.agents.llm_agent import LLMRepositoryAnalyst
from aegis.config import LLMConfig
from aegis.knowledge.indexer import KnowledgeBuilder
from aegis.llm import LLMClient
from aegis.models import AnalysisResult, Finding
from aegis.orchestrator.context import ContextRouter
from aegis.rag.index import RAGIndexBuilder
from aegis.reporting.writer import ReportWriter
from aegis.utils import slugify, write_json


class AegisWorkflow:
    def __init__(
        self,
        repo: Path,
        *,
        output_root: Path = Path("output/aegis"),
        max_files: int = 1500,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        use_cache: bool = True,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.repo = repo.resolve()
        self.max_files = max_files
        self.include = include or []
        self.exclude = exclude or []
        self.use_cache = use_cache
        self.llm_config = llm_config or LLMConfig.from_env(enabled=False)
        self.output_dir = output_root / slugify(self.repo.name)
        self.events: list[dict[str, str]] = []

    def run(self) -> AnalysisResult:
        self._event("start", f"分析仓库 {self.repo}")
        self.output_dir.mkdir(parents=True, exist_ok=True)
        knowledge = KnowledgeBuilder(
            self.repo,
            max_files=self.max_files,
            include=self.include,
            exclude=self.exclude,
            cache_dir=self.output_dir / ".cache",
            use_cache=self.use_cache,
        ).build()
        rag_index = RAGIndexBuilder(knowledge).build()
        knowledge.stats["rag"] = rag_index.stats
        write_json(self.output_dir / "knowledge.json", knowledge.to_dict())
        write_json(self.output_dir / "rag_index.json", rag_index.to_dict())
        self._event("knowledge-built", f"扫描 {knowledge.stats.get('file_count')} 个文件")

        findings: list[Finding] = []
        agents = [
            ArchitectureAnalyst(),
            InterfaceAnalyst(),
            InternalsAnalyst(),
            DataStateAnalyst(),
            BuildRuntimeAnalyst(),
            RiskAnalyst(),
        ]
        for agent in agents:
            self._event("agent-start", agent.name)
            agent_findings = agent.analyze(knowledge)
            findings.extend(agent_findings)
            self._event("agent-finish", f"{agent.name}: {len(agent_findings)} findings")

        if self.llm_config.enabled:
            router = ContextRouter(knowledge, max_chars=self.llm_config.max_context_chars)
            llm_agent = LLMRepositoryAnalyst(LLMClient(self.llm_config), router)
            self._event("agent-start", llm_agent.name)
            agent_findings = llm_agent.analyze(knowledge)
            findings.extend(agent_findings)
            self._event("agent-finish", f"{llm_agent.name}: {len(agent_findings)} findings")

        reviewer = EvidenceReviewer()
        reviewed = reviewer.review(knowledge, findings)
        self._event("review-finish", f"{len(reviewed)} reviewed findings")
        write_json(self.output_dir / "findings.json", [item for item in reviewed])
        write_json(self.output_dir / "events.json", self.events)

        ReportWriter(self.output_dir).write(knowledge, reviewed, self.events)
        self._event("report-written", str(self.output_dir / "report.md"))
        write_json(self.output_dir / "events.json", self.events)
        return AnalysisResult(knowledge=knowledge, findings=reviewed, output_dir=self.output_dir)

    def _event(self, kind: str, message: str) -> None:
        self.events.append(
            {
                "time": datetime.now().isoformat(timespec="seconds"),
                "kind": kind,
                "message": message,
            }
        )
