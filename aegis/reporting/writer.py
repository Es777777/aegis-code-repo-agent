from __future__ import annotations

from collections import defaultdict
import html
from pathlib import Path

from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.models import Finding, RepoKnowledge


class ReportWriter:
    ARTIFACT_LINKS = [
        "report.md",
        "report.html",
        "knowledge.json",
        "findings.json",
        "rag_index.json",
        "manifest.json",
        "evaluation.json",
        "impact.json",
        "readiness.json",
        "qa_answer.json",
        "context_pack.md",
        "architecture.mmd",
    ]

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir

    def write(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
    ) -> Path:
        path = self.output_dir / "report.md"
        path.write_text(self._render_markdown(knowledge, findings, events), encoding="utf-8")
        mermaid = self._render_mermaid(knowledge)
        (self.output_dir / "architecture.mmd").write_text(mermaid, encoding="utf-8")
        (self.output_dir / "report.html").write_text(
            self._render_html(knowledge, findings, events, mermaid),
            encoding="utf-8",
        )
        return path

    def _render_markdown(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
    ) -> str:
        rag_stats = knowledge.stats.get("rag", {})
        by_agent = self._by_agent(findings)
        lines: list[str] = [
            f"# AEGIS Repository Analysis Report: {knowledge.repo_name}",
            "",
            "## Summary",
            "",
            f"- Repository: `{knowledge.root}`",
            f"- Files: {knowledge.stats.get('file_count', 0)}",
            f"- Lines: {knowledge.stats.get('total_lines', 0)}",
            f"- Cache hits: {knowledge.stats.get('cache_hits', 0)}",
            f"- Cache misses: {knowledge.stats.get('cache_misses', 0)}",
        ]
        lines.extend(self._scan_scope_markdown(knowledge))
        lines.extend(
            [
                f"- Languages: {self._language_summary(knowledge)}",
                f"- Frameworks: {', '.join(knowledge.frameworks) if knowledge.frameworks else 'unknown'}",
                f"- Entrypoints: {', '.join(knowledge.entrypoints[:8]) if knowledge.entrypoints else 'unknown'}",
                f"- Git changed files: {len(knowledge.changed_files)}",
                f"- RAG chunks: {rag_stats.get('chunk_count', 0) if isinstance(rag_stats, dict) else 0}",
                "",
                "## Main Flow",
                "",
                "1. Scan repository files, configs, entrypoints, and Git change hints.",
                "2. Build Repo Map, CodeGraph, interface catalog, and Evidence Store.",
                "3. Build source-backed RAG chunks for agents and LLM context packs.",
                "4. Run specialist agents and review findings for evidence.",
                "5. Write reports, machine-readable artifacts, and readiness evidence.",
                "",
                "## Repository Knowledge Layer",
                "",
                "### CodeGraph",
                "",
            ]
        )
        cg_stats = knowledge.code_graph.stats
        lines.extend(
            [
                f"- Nodes: {cg_stats.get('node_count', 0)}",
                f"- Edges: {cg_stats.get('edge_count', 0)}",
                f"- Node kinds: {self._dict_summary(cg_stats.get('node_kinds', {}))}",
                f"- Edge kinds: {self._dict_summary(cg_stats.get('edge_kinds', {}))}",
                "",
            ]
        )
        lines.extend(self._trace_summary(knowledge))
        lines.extend(self._impact_summary(knowledge))
        lines.extend(self._repo_map_markdown(knowledge))
        lines.extend(self._findings_markdown(by_agent))
        lines.extend(
            [
                "## Events",
                "",
                *[
                    f"- `{event['time']}` **{event['kind']}**: {event['message']}"
                    for event in events
                ],
                "",
                "## Visual Artifacts",
                "",
                "- `architecture.mmd`: Mermaid architecture diagram",
                "- `report.html`: browsable HTML report",
                "- `manifest.json`: analysis run manifest",
                "",
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _scan_scope_markdown(knowledge: RepoKnowledge) -> list[str]:
        scan_stats = knowledge.stats.get("scan", {})
        if not isinstance(scan_stats, dict):
            return []
        include = scan_stats.get("include") or []
        exclude = scan_stats.get("exclude") or []
        skipped = scan_stats.get("skipped") or {}
        lines: list[str] = []
        if include:
            lines.append(f"- Include 范围: {', '.join(f'`{item}`' for item in include)}")
        if exclude:
            lines.append(f"- Exclude 范围: {', '.join(f'`{item}`' for item in exclude)}")
        if isinstance(skipped, dict) and skipped:
            lines.append(f"- 跳过文件: {ReportWriter._dict_summary(skipped)}")
        return lines

    def _trace_summary(self, knowledge: RepoKnowledge) -> list[str]:
        lines: list[str] = []
        query = CodeGraphQuery(knowledge.code_graph)
        if knowledge.interface_catalog:
            first_route = next(iter(next(iter(knowledge.interface_catalog.values()))), "")
            route = first_route.split(maxsplit=1)[-1] if first_route else ""
            trace = query.trace_interface(route) if route else []
            if trace:
                lines.append(f"Interface trace example `{first_route}`:")
                for node in trace[:12]:
                    if node.path and node.line:
                        location = f" `{node.path}:{node.line}`"
                    elif node.path:
                        location = f" `{node.path}`"
                    else:
                        location = ""
                    lines.append(f"- {node.kind}: {node.name}{location}")
                lines.append("")
        return lines

    def _impact_summary(self, knowledge: RepoKnowledge) -> list[str]:
        if not knowledge.changed_files:
            return []
        impacted = CodeGraphQuery(knowledge.code_graph).impacted_by_files(knowledge.changed_files)
        if not impacted:
            return []
        lines = ["Git Diff impacted nodes:"]
        lines.extend(f"- {node.kind}: {node.name}" for node in impacted[:20])
        lines.append("")
        return lines

    def _repo_map_markdown(self, knowledge: RepoKnowledge) -> list[str]:
        lines = ["### Repo Map", ""]
        lines.extend(f"- `{item}`" for item in knowledge.repo_map[:20])
        lines.extend(["", "### Interface Catalog", ""])
        if knowledge.interface_catalog:
            for file_path, interfaces in list(knowledge.interface_catalog.items())[:20]:
                lines.append(f"- `{file_path}`: {', '.join(interfaces[:8])}")
        else:
            lines.append("- No explicit interface candidates found.")
        lines.extend(["", "### Call Graph", ""])
        call_edges = [
            (source, target)
            for source, targets in knowledge.call_graph.items()
            for target in targets
        ]
        if call_edges:
            lines.extend(f"- `{source}` -> `{target}`" for source, target in call_edges[:30])
        else:
            lines.append("- No cross-file call graph edges found.")
        lines.extend(["", "### Config & Runtime", ""])
        if knowledge.configs:
            lines.extend(f"- `{item}`" for item in knowledge.configs[:20])
        else:
            lines.append("- No common config files found.")
        lines.extend(["", "### Git Diff Scanner", ""])
        if knowledge.changed_files:
            lines.extend(f"- `{item}`" for item in knowledge.changed_files[:30])
        else:
            lines.append("- No Git changes found, or target directory is not a Git repository.")
        lines.append("")
        return lines

    @staticmethod
    def _findings_markdown(by_agent: dict[str, list[Finding]]) -> list[str]:
        lines: list[str] = []
        for agent, items in by_agent.items():
            lines.extend([f"## {agent}", ""])
            for item in items:
                lines.extend(
                    [
                        f"### [{item.severity}] {item.title}",
                        "",
                        item.summary,
                        "",
                        f"- Confidence: {item.confidence:.2f}",
                    ]
                )
                if item.tags:
                    lines.append(f"- Tags: {', '.join(item.tags)}")
                lines.append("- Evidence:")
                if item.evidence:
                    for ev in item.evidence[:6]:
                        lines.append(f"  - `{ev.path}:{ev.line}` ({ev.source}, {ev.confidence:.2f}) {ev.snippet}")
                else:
                    lines.append("  - No source evidence available; verify before relying on this finding.")
                lines.append("")
        return lines

    def _render_html(
        self,
        knowledge: RepoKnowledge,
        findings: list[Finding],
        events: list[dict[str, str]],
        mermaid: str,
    ) -> str:
        by_agent = self._by_agent(findings)
        nav_links = "".join(
            f'<a href="#agent-{html.escape(self._anchor(agent))}">{html.escape(agent)}</a>'
            for agent in by_agent
        )
        artifact_links = "".join(
            f'<a href="{html.escape(name)}">{html.escape(name)}</a>'
            for name in self.ARTIFACT_LINKS
        )
        cards = []
        for agent, items in by_agent.items():
            body = []
            for item in items:
                evidence = "".join(
                    f"<li><code>{html.escape(ev.path)}:{ev.line}</code> {html.escape(ev.snippet)}</li>"
                    for ev in item.evidence[:5]
                ) or "<li>No source evidence available</li>"
                search_text = " ".join(
                    [
                        agent,
                        item.title,
                        item.summary,
                        " ".join(ev.path for ev in item.evidence),
                        " ".join(ev.snippet for ev in item.evidence),
                    ]
                ).lower()
                body.append(
                    f"""
                    <article class="finding severity-{html.escape(item.severity)}" data-search="{html.escape(search_text)}">
                      <h3>{html.escape(item.title)}</h3>
                      <p>{html.escape(item.summary)}</p>
                      <p class="meta">severity={html.escape(item.severity)} | confidence={item.confidence:.2f}</p>
                      <ul>{evidence}</ul>
                    </article>
                    """
                )
            cards.append(
                f'<section id="agent-{html.escape(self._anchor(agent))}" class="agent-section">'
                f"<h2>{html.escape(agent)}</h2>{''.join(body)}</section>"
            )
        events_html = "".join(
            f"<li class=\"event\" data-search=\"{html.escape((event['kind'] + ' ' + event['message']).lower())}\"><code>{html.escape(event['time'])}</code> <b>{html.escape(event['kind'])}</b>: {html.escape(event['message'])}</li>"
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
    nav {{ display: flex; gap: 10px; flex-wrap: wrap; margin-top: 16px; }}
    nav a, .artifacts a {{ color: #1849a9; background: #eef4ff; border: 1px solid #b2ccff; border-radius: 6px; padding: 6px 9px; text-decoration: none; }}
    .toolbar {{ position: sticky; top: 0; z-index: 2; background: rgba(246, 248, 251, 0.96); border-bottom: 1px solid #d0d5dd; padding: 12px 28px; }}
    .toolbar-inner {{ max-width: 1180px; margin: 0 auto; display: flex; gap: 12px; align-items: center; }}
    input[type="search"] {{ flex: 1; min-width: 220px; padding: 10px 12px; border: 1px solid #98a2b3; border-radius: 6px; font-size: 15px; }}
    .summary, section {{ background: white; border: 1px solid #d0d5dd; border-radius: 8px; padding: 20px; margin-bottom: 18px; }}
    .artifacts {{ display: flex; gap: 8px; flex-wrap: wrap; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }}
    .metric {{ background: #eef4ff; border: 1px solid #b2ccff; border-radius: 6px; padding: 12px; }}
    .finding {{ border-left: 5px solid #667085; padding: 12px 14px; margin: 12px 0; background: #fcfcfd; }}
    .severity-high {{ border-left-color: #d92d20; }}
    .severity-medium {{ border-left-color: #f79009; }}
    .severity-low {{ border-left-color: #1570ef; }}
    .meta {{ color: #667085; font-size: 13px; }}
    .hidden {{ display: none; }}
    code {{ background: #f2f4f7; padding: 2px 4px; border-radius: 4px; }}
    pre {{ overflow-x: auto; background: #0b1220; color: #e5e7eb; padding: 16px; border-radius: 8px; }}
  </style>
</head>
<body>
<header>
  <h1>AEGIS Repository Analysis Report: {html.escape(knowledge.repo_name)}</h1>
  <p>Shared knowledge layer + orchestrated agents + evidence-first reports + reusable artifacts</p>
  <nav aria-label="Report sections">
    <a href="#summary">Summary</a>
    <a href="#artifacts">Artifacts</a>
    <a href="#architecture">Architecture</a>
    {nav_links}
    <a href="#events">Events</a>
  </nav>
</header>
<div class="toolbar">
  <div class="toolbar-inner">
    <label for="report-search">Search</label>
    <input id="report-search" type="search" placeholder="Filter findings and events by file, agent, symbol, route, or risk">
    <span id="search-count"></span>
  </div>
</div>
<main>
  <section id="summary" class="summary">
    <h2>Summary</h2>
    <div class="grid">
      <div class="metric"><b>Files</b><br>{knowledge.stats.get('file_count', 0)}</div>
      <div class="metric"><b>Lines</b><br>{knowledge.stats.get('total_lines', 0)}</div>
      <div class="metric"><b>Cache hits</b><br>{knowledge.stats.get('cache_hits', 0)}</div>
      <div class="metric"><b>Frameworks</b><br>{html.escape(', '.join(knowledge.frameworks) or 'unknown')}</div>
    </div>
  </section>
  <section id="artifacts">
    <h2>Artifacts</h2>
    <p class="meta">Open the machine-readable outputs used by agents, evaluation scripts, and readiness checks.</p>
    <div class="artifacts">{artifact_links}</div>
  </section>
  <section id="architecture">
    <h2>Architecture Mermaid</h2>
    <pre>{html.escape(mermaid)}</pre>
  </section>
  {''.join(cards)}
  <section id="events">
    <h2>Events</h2>
    <ul>{events_html}</ul>
  </section>
</main>
<script>
const search = document.getElementById('report-search');
const count = document.getElementById('search-count');
const searchable = Array.from(document.querySelectorAll('.finding, .event'));
function applyFilter() {{
  const term = search.value.trim().toLowerCase();
  let shown = 0;
  for (const item of searchable) {{
    const ok = !term || item.dataset.search.includes(term);
    item.classList.toggle('hidden', !ok);
    if (ok) shown += 1;
  }}
  count.textContent = term ? `${{shown}} matching items` : `${{searchable.length}} searchable items`;
}}
search.addEventListener('input', applyFilter);
applyFilter();
</script>
</body>
</html>"""

    @staticmethod
    def _by_agent(findings: list[Finding]) -> dict[str, list[Finding]]:
        by_agent: dict[str, list[Finding]] = defaultdict(list)
        for finding in findings:
            by_agent[finding.agent].append(finding)
        return by_agent

    @staticmethod
    def _language_summary(knowledge: RepoKnowledge) -> str:
        languages = knowledge.stats.get("languages", {})
        if not isinstance(languages, dict) or not languages:
            return "unknown"
        return ", ".join(f"{lang}({count})" for lang, count in list(languages.items())[:6])

    @staticmethod
    def _dict_summary(value: object) -> str:
        if not isinstance(value, dict) or not value:
            return "none"
        return ", ".join(f"{key}({count})" for key, count in list(value.items())[:10])

    @staticmethod
    def _render_mermaid(knowledge: RepoKnowledge) -> str:
        def node_id(value: str) -> str:
            cleaned = "".join(ch if ch.isalnum() else "_" for ch in value)
            return cleaned[:50] or "node"

        lines = [
            "flowchart LR",
            "  U[User Goal] --> S[Repo Scanner]",
            "  S --> K[Repository Knowledge Layer]",
            "  K --> O[Orchestrator Workflow]",
            "  O --> A[Specialist Agents]",
            "  A --> R[Evidence Reviewer]",
            "  R --> D[Document Writer]",
            "  D --> P[Reports and Artifacts]",
            "  A -.evidence.-> K",
            "  R -.feedback.-> O",
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

    @staticmethod
    def _anchor(value: str) -> str:
        cleaned = "".join(ch.lower() if ch.isalnum() else "-" for ch in value)
        return "-".join(part for part in cleaned.split("-") if part) or "section"
