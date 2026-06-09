from __future__ import annotations

from collections import defaultdict
import html
from pathlib import Path

from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.models import Finding, RepoKnowledge


class ReportWriter:
    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
    ) -> Path:
        path = self.output_dir / "report.md"
        path.write_text(self._render(knowledge, findings, events), encoding="utf-8")
        mermaid = self._render_mermaid(knowledge)
        (self.output_dir / "architecture.mmd").write_text(mermaid, encoding="utf-8")
        (self.output_dir / "report.html").write_text(
            self._render_html(knowledge, findings, events, mermaid),
            encoding="utf-8",
        )
        return path

    def _render(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
    ) -> str:
        by_agent: dict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            by_agent[finding.agent].append(finding)
        lines: list[str] = []
        lines.append(f"# AEGIS 2.0 仓库分析报告：{knowledge.repo_name}")
        lines.append("")
        lines.append("## 摘要")
        lines.append("")
        lines.append(f"- 仓库路径：`{knowledge.root}`")
        lines.append(f"- 可分析文件：{knowledge.stats.get('file_count', 0)}")
        lines.append(f"- 总行数：{knowledge.stats.get('total_lines', 0)}")
        lines.append(f"- 缓存命中：{knowledge.stats.get('cache_hits', 0)}")
        lines.append(f"- 缓存未命中：{knowledge.stats.get('cache_misses', 0)}")
        lines.append(f"- 主要语言：{self._language_summary(knowledge)}")
        lines.append(f"- 框架线索：{', '.join(knowledge.frameworks) if knowledge.frameworks else '未识别'}")
        lines.append(f"- 入口候选：{', '.join(knowledge.entrypoints[:8]) if knowledge.entrypoints else '未识别'}")
        lines.append(f"- Git 变更文件：{len(knowledge.changed_files)}")
        lines.append("")
        lines.append("## 主流程")
        lines.append("")
        lines.append("1. 导入仓库与用户目标")
        lines.append("2. 扫描文件、配置、入口和 Git 线索")
        lines.append("3. 构建 Repo Map、符号图、依赖图、接口目录与证据库")
        lines.append("4. Orchestrator 分派上下文给专项分析 Agent")
        lines.append("5. 各 Agent 产出带证据的结论")
        lines.append("6. Evidence Reviewer 检查证据缺口与冲突")
        lines.append("7. Document Writer 生成可追溯报告")
        lines.append("")
        lines.append("## Repository Knowledge Layer")
        lines.append("")
        lines.append("### CodeGraph")
        lines.append("")
        cg_stats = knowledge.code_graph.stats
        lines.append(f"- 节点数：{cg_stats.get('node_count', 0)}")
        lines.append(f"- 边数：{cg_stats.get('edge_count', 0)}")
        lines.append(f"- 节点类型：{self._dict_summary(cg_stats.get('node_kinds', {}))}")
        lines.append(f"- 边类型：{self._dict_summary(cg_stats.get('edge_kinds', {}))}")
        lines.append("")
        query = CodeGraphQuery(knowledge.code_graph)
        if knowledge.interface_catalog:
            first_route = next(iter(next(iter(knowledge.interface_catalog.values()))), "")
            route = first_route.split(maxsplit=1)[-1] if first_route else ""
            trace = query.trace_interface(route) if route else []
            if trace:
                lines.append(f"接口链路示例 `{first_route}`：")
                for node in trace[:12]:
                    location = (
                        f" `{node.path}:{node.line}`"
                        if node.path and node.line
                        else f" `{node.path}`"
                        if node.path
                        else ""
                    )
                    lines.append(f"- {node.kind}: {node.name}{location}")
                lines.append("")
        if knowledge.changed_files:
            impacted = query.impacted_by_files(knowledge.changed_files)
            if impacted:
                lines.append("Git Diff 影响节点：")
                for node in impacted[:20]:
                    lines.append(f"- {node.kind}: {node.name}")
                lines.append("")
        lines.append("### Repo Map")
        lines.append("")
        for item in knowledge.repo_map[:20]:
            lines.append(f"- `{item}`")
        lines.append("")
        lines.append("### Interface Catalog")
        lines.append("")
        if knowledge.interface_catalog:
            for file_path, interfaces in list(knowledge.interface_catalog.items())[:20]:
                lines.append(f"- `{file_path}`: {', '.join(interfaces[:8])}")
        else:
            lines.append("- 未发现显式接口候选。")
        lines.append("")
        lines.append("### Call Graph")
        lines.append("")
        call_edges = [
            (source, target)
            for source, targets in knowledge.call_graph.items()
            for target in targets
        ]
        if call_edges:
            for source, target in call_edges[:30]:
                lines.append(f"- `{source}` -> `{target}`")
        else:
            lines.append("- 未发现跨文件调用关系，当前调用图可能仍停留在 import 级依赖。")
        lines.append("")
        lines.append("### Config & Runtime")
        lines.append("")
        if knowledge.configs:
            for item in knowledge.configs[:20]:
                lines.append(f"- `{item}`")
        else:
            lines.append("- 未发现常见配置文件。")
        lines.append("")
        lines.append("### Git Diff Scanner")
        lines.append("")
        if knowledge.changed_files:
            for item in knowledge.changed_files[:30]:
                lines.append(f"- `{item}`")
        else:
            lines.append("- 未发现 Git 变更，或目标目录不是 Git 仓库。")
        lines.append("")

        for agent, items in by_agent.items():
            lines.append(f"## {agent}")
            lines.append("")
            for item in items:
                lines.append(f"### [{item.severity}] {item.title}")
                lines.append("")
                lines.append(item.summary)
                lines.append("")
                lines.append(f"- 置信度：{item.confidence:.2f}")
                if item.tags:
                    lines.append(f"- 标签：{', '.join(item.tags)}")
                lines.append("- 证据：")
                if item.evidence:
                    for ev in item.evidence[:6]:
                        lines.append(
                            f"  - `{ev.path}:{ev.line}` ({ev.source}, {ev.confidence:.2f}) "
                            f"{ev.snippet}"
                        )
                else:
                    lines.append("  - 暂无源码证据，需补查或降低置信度。")
                lines.append("")

        lines.append("## 事件日志")
        lines.append("")
        for event in events:
            lines.append(f"- `{event['time']}` **{event['kind']}**: {event['message']}")
        lines.append("")
        lines.append("## 后续建议")
        lines.append("")
        lines.append("- 为目标语言接入 Tree-sitter 或 LSP，提升符号图和引用关系准确度。")
        lines.append("- 为 FastAPI、Express、Spring、Django 等框架补充专用接口解析器。")
        lines.append("- 接入 LLM 作为 Agent 推理层，但保留当前 Evidence Store 作为事实约束。")
        lines.append("- 使用 Git Diff 驱动增量分析，只重建受影响文件和报告章节。")
        lines.append("")
        lines.append("## 可视化文件")
        lines.append("")
        lines.append("- `architecture.mmd`：Mermaid 架构图")
        lines.append("- `report.html`：可浏览 HTML 报告")
        lines.append("")
        return "\n".join(lines)

    @staticmethod
    def _language_summary(knowledge: RepoKnowledge) -> str:
        languages = knowledge.stats.get("languages", {})
        if not isinstance(languages, dict) or not languages:
            return "未知"
        return ", ".join(f"{lang}({count})" for lang, count in list(languages.items())[:6])

    @staticmethod
    def _dict_summary(value: object) -> str:
        if not isinstance(value, dict) or not value:
            return "无"
        return ", ".join(f"{key}({count})" for key, count in list(value.items())[:10])

    @staticmethod
    def _render_mermaid(knowledge: RepoKnowledge) -> str:
        def node_id(value: str) -> str:
            cleaned = "".join(ch if ch.isalnum() else "_" for ch in value)
            return cleaned[:50] or "node"

        lines = [
            "flowchart LR",
            "  U[用户目标] --> S[Repo Scanner]",
            "  S --> K[Repository Knowledge Layer]",
            "  K --> O[Orchestrator Workflow]",
            "  O --> A[Specialist Agents]",
            "  A --> R[Evidence Reviewer]",
            "  R --> D[Document Writer]",
            "  D --> P[Reports]",
            "  A -.证据回写.-> K",
            "  R -.冲突返工.-> O",
        ]
        nodes_by_id = {node.id: node for node in knowledge.code_graph.nodes}
        for interface_id in knowledge.code_graph.interfaces[:6]:
            node = nodes_by_id.get(interface_id)
            if node:
                lines.append(f'  K --> {node_id(node.id)}["{node.name}"]')
        for path in knowledge.repo_map[:8]:
            nid = node_id(path)
            lines.append(f'  K --> {nid}["{path}"]')
        for source, targets in list(knowledge.dependency_graph.items())[:12]:
            source_id = node_id(source)
            for target in targets[:4]:
                if target in knowledge.dependency_graph:
                    lines.append(f"  {source_id} --> {node_id(target)}")
        return "\n".join(lines)

    def _render_html(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
        mermaid: str,
    ) -> str:
        by_agent: dict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            by_agent[finding.agent].append(finding)
        cards = []
        for agent, items in by_agent.items():
            body = []
            for item in items:
                evidence = "".join(
                    f"<li><code>{html.escape(ev.path)}:{ev.line}</code> {html.escape(ev.snippet)}</li>"
                    for ev in item.evidence[:5]
                ) or "<li>暂无源码证据</li>"
                body.append(
                    f"""
                    <article class="finding severity-{html.escape(item.severity)}">
                      <h3>{html.escape(item.title)}</h3>
                      <p>{html.escape(item.summary)}</p>
                      <p class="meta">severity={html.escape(item.severity)} · confidence={item.confidence:.2f}</p>
                      <ul>{evidence}</ul>
                    </article>
                    """
                )
            cards.append(f"<section><h2>{html.escape(agent)}</h2>{''.join(body)}</section>")
        events_html = "".join(
            f"<li><code>{html.escape(event['time'])}</code> <b>{html.escape(event['kind'])}</b>: {html.escape(event['message'])}</li>"
            for event in events
        )
        return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>AEGIS Report - {html.escape(knowledge.repo_name)}</title>
  <style>
    body {{ margin: 0; font-family: "Microsoft YaHei", system-ui, sans-serif; background: #f6f8fb; color: #101828; }}
    header {{ padding: 28px 40px; background: #111827; color: white; }}
    main {{ max-width: 1180px; margin: 0 auto; padding: 28px; }}
    .summary, section {{ background: white; border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin-bottom: 18px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .metric {{ background: #eef4ff; border: 1px solid #b2ccff; border-radius: 6px; padding: 12px; }}
    .finding {{ border-left: 5px solid #667085; padding: 12px 14px; margin: 12px 0; background: #fcfcfd; }}
    .severity-high {{ border-left-color: #d92d20; }}
    .severity-medium {{ border-left-color: #f79009; }}
    .severity-low {{ border-left-color: #1570ef; }}
    .meta {{ color: #667085; font-size: 13px; }}
    code {{ background: #f2f4f7; padding: 2px 4px; border-radius: 4px; }}
    pre {{ overflow-x: auto; background: #0b1220; color: #e5e7eb; padding: 16px; border-radius: 8px; }}
  </style>
</head>
<body>
<header>
  <h1>AEGIS 2.0 仓库分析报告：{html.escape(knowledge.repo_name)}</h1>
  <p>共享知识底座 + 编排式多 Agent + 证据化报告 + 增量更新</p>
</header>
<main>
  <section class="summary">
    <h2>摘要</h2>
    <div class="grid">
      <div class="metric"><b>文件</b><br>{knowledge.stats.get('file_count', 0)}</div>
      <div class="metric"><b>行数</b><br>{knowledge.stats.get('total_lines', 0)}</div>
      <div class="metric"><b>缓存命中</b><br>{knowledge.stats.get('cache_hits', 0)}</div>
      <div class="metric"><b>框架</b><br>{html.escape(', '.join(knowledge.frameworks) or '未识别')}</div>
    </div>
  </section>
  <section>
    <h2>架构图 Mermaid</h2>
    <pre>{html.escape(mermaid)}</pre>
  </section>
  {''.join(cards)}
  <section>
    <h2>事件日志</h2>
    <ul>{events_html}</ul>
  </section>
</main>
</body>
</html>"""
