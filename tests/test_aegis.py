from __future__ import annotations

import json
import io
import subprocess
import sys
import tempfile
import tomllib
import unittest
import os
import urllib.error
from pathlib import Path
from unittest.mock import patch

import aegis
from aegis.artifacts import load_analysis_result, load_rag_index
from aegis.config import AegisConfig, LLMConfig, load_env_file
from aegis.doctor import Doctor
from aegis.evaluation import Evaluator, builtin_suite, load_suite
from aegis.agents.llm_agent import LLMRepositoryAnalyst
from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.knowledge.indexer import KnowledgeBuilder
from aegis.knowledge.parsers import extract_interfaces
from aegis.llm import LLMClient, LLMError
from aegis.orchestrator.context import ContextRouter
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGChunk, RAGIndex, RAGIndexBuilder
from aegis.rag.qa import RepositoryQAAgent
from aegis.rag.retriever import RAGRetriever
from aegis.readiness import ReadinessAssessor
from aegis.utils import file_sha256, write_json


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_repo"
EDA_SAMPLE = ROOT / "examples" / "eda_repo"


class KnowledgeBuilderTest(unittest.TestCase):
    def test_sample_repo_interfaces_and_framework(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        self.assertIn("FastAPI", knowledge.frameworks)
        self.assertEqual(knowledge.interface_catalog["app.py"], ["GET /health", "POST /users"])
        self.assertIn("services/user_service.py", knowledge.dependency_graph["app.py"])
        self.assertIn("repositories/user_repository.py", knowledge.dependency_graph["services/user_service.py"])
        kinds = {node.kind for node in knowledge.code_graph.nodes}
        self.assertIn("file", kinds)
        self.assertIn("module", kinds)
        self.assertIn("interface", kinds)
        self.assertIn("data_model", kinds)
        edge_kinds = {edge.kind for edge in knowledge.code_graph.edges}
        self.assertIn("imports", edge_kinds)
        self.assertIn("defines", edge_kinds)
        self.assertIn("exposes", edge_kinds)

    def test_codegraph_trace_interface(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        trace = CodeGraphQuery(knowledge.code_graph).trace_interface("/users")
        names = [node.name for node in trace]
        self.assertTrue(any("/users" in name for name in names))
        self.assertIn("app.py", names)
        users = next(node for node in trace if node.kind == "interface" and "/users" in node.name)
        self.assertEqual(users.line, 14)

    def test_rag_retrieves_repository_context(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        self.assertGreater(index.stats["chunk_count"], 0)
        results = RAGRetriever(index).search("POST /users UserRepository", top_k=5)
        titles = " ".join(result.chunk.title for result in results)
        self.assertIn("users", titles.lower())
        self.assertTrue(any("UserRepository" in result.chunk.text for result in results))

    def test_qa_agent_offline_answer(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer("用户创建接口在哪里，数据写入哪里？")
        self.assertFalse(answer.used_llm)
        self.assertIn("Offline RAG", answer.answer)
        self.assertTrue(answer.results)
        self.assertTrue(answer.context_pack.blocks)
        self.assertTrue(any(block.chunk_kind == "source" for block in answer.context_pack.blocks))

    def test_qa_agent_adds_codegraph_trace_for_route_questions(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer("POST /users 的调用链路是什么？")
        self.assertIsNotNone(answer.graph_context)
        assert answer.graph_context is not None
        self.assertEqual(answer.graph_context["route"], "/users")
        names = [node["name"] for node in answer.graph_context["nodes"]]
        self.assertIn("POST /users", names)
        self.assertIn("UserService", names)
        self.assertIn("UserRepository", names)
        self.assertIn("CodeGraph trace", answer.answer)
        source_paths = answer.context_pack.source_paths()
        self.assertIn("app.py", source_paths)
        self.assertIn("services/user_service.py", source_paths)
        self.assertIn("repositories/user_repository.py", source_paths)
        complete_paths = answer.context_pack.complete_file_paths()
        self.assertIn("app.py", complete_paths)
        self.assertIn("services/user_service.py", complete_paths)
        self.assertIn("repositories/user_repository.py", complete_paths)

    def test_include_exclude_scope_controls_scanned_files(self) -> None:
        knowledge = KnowledgeBuilder(
            SAMPLE,
            max_files=100,
            include=["*.py", "services/*.py"],
            exclude=["app.py"],
            use_cache=False,
        ).build()
        paths = {record.path for record in knowledge.files}
        self.assertIn("services/user_service.py", paths)
        self.assertIn("repositories/user_repository.py", paths)
        self.assertNotIn("app.py", paths)
        self.assertNotIn("pyproject.toml", paths)
        scan_stats = knowledge.stats["scan"]
        self.assertEqual(scan_stats["include"], ["*.py", "services/*.py"])
        self.assertEqual(scan_stats["exclude"], ["app.py"])
        self.assertGreaterEqual(scan_stats["skipped"].get("scope", 0), 1)

    def test_max_files_reports_all_unscanned_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for idx in range(5):
                (root / f"module_{idx}.py").write_text(
                    f"def fn_{idx}():\n    return {idx}\n",
                    encoding="utf-8",
                )
            knowledge = KnowledgeBuilder(root, max_files=2, use_cache=False).build()
            self.assertEqual(knowledge.stats["file_count"], 2)
            scan_stats = knowledge.stats["scan"]
            self.assertEqual(scan_stats["skipped"].get("max_files"), 3)

    def test_web_interface_parsers_cover_common_frameworks(self) -> None:
        fastapi = """
from fastapi import APIRouter
router = APIRouter(prefix="/api")
@router.post("/users")
def create_user(): pass
"""
        express = """
const router = express.Router()
router.get('/users', listUsers)
app.use('/api', router)
"""
        nest = """
@Controller('users')
export class UserController {
  @Get(':id')
  findOne() {}
}
"""
        spring = """
@RestController
@RequestMapping("/api")
class UserController {
  @PostMapping("/users")
  createUser() {}
}
"""
        go_gin = 'router.GET("/health", healthHandler)'
        aspnet = '[HttpDelete("users/{id}")]'
        laravel = "Route::patch('/users/{id}', [UserController::class, 'update']);"
        fastify = "fastify.route({ method: 'POST', url: '/api/users', handler: createUser })"
        hono = "app.get('/health', (c) => c.json({ ok: true }))"
        next_route = "export async function GET() { return Response.json([]) }"
        sveltekit_route = "export const PATCH = async () => json({ ok: true })"
        self.assertIn("POST /api/users", extract_interfaces(fastapi, "Python"))
        self.assertIn("GET /api/users", extract_interfaces(express, "JavaScript"))
        self.assertIn("GET /users/:id", extract_interfaces(nest, "TypeScript"))
        self.assertIn("POST /api/users", extract_interfaces(spring, "Java"))
        self.assertIn("GET /health", extract_interfaces(go_gin, "Go"))
        self.assertIn("DELETE /users/{id}", extract_interfaces(aspnet, "C#"))
        self.assertIn("PATCH /users/{id}", extract_interfaces(laravel, "PHP"))
        self.assertIn("POST /api/users", extract_interfaces(fastify, "JavaScript"))
        self.assertIn("GET /health", extract_interfaces(hono, "TypeScript"))
        self.assertIn(
            "GET /api/users/:id",
            extract_interfaces(next_route, "TypeScript", path="app/api/users/[id]/route.ts"),
        )
        self.assertIn(
            "PATCH /api/orders/:orderId",
            extract_interfaces(sveltekit_route, "TypeScript", path="src/routes/api/orders/[orderId]/+server.ts"),
        )

    def test_codegraph_traces_express_prefixed_router_interface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "server.js").write_text(
                "\n".join(
                    [
                        "const express = require('express')",
                        "const app = express()",
                        "const router = express.Router()",
                        "function listUsers(req, res) {",
                        "  return res.json([])",
                        "}",
                        "router.get('/users', listUsers)",
                        "app.use('/api', router)",
                    ]
                ),
                encoding="utf-8",
            )
            knowledge = KnowledgeBuilder(root, max_files=20, use_cache=False).build()

        self.assertEqual(knowledge.interface_catalog["server.js"], ["GET /api/users"])
        trace = CodeGraphQuery(knowledge.code_graph).trace_interface("/api/users")
        self.assertTrue(trace)
        self.assertEqual(trace[0].metadata["route"], "/api/users")
        self.assertEqual(trace[0].metadata["method"], "GET")

    def test_codegraph_traces_file_based_route_handlers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            route_path = root / "app" / "api" / "users" / "[id]" / "route.ts"
            route_path.parent.mkdir(parents=True)
            route_path.write_text(
                "\n".join(
                    [
                        "export async function GET(request: Request) {",
                        "  return Response.json({ ok: true })",
                        "}",
                    ]
                ),
                encoding="utf-8",
            )
            knowledge = KnowledgeBuilder(root, max_files=20, use_cache=False).build()

        relative = "app/api/users/[id]/route.ts"
        self.assertEqual(knowledge.interface_catalog[relative], ["GET /api/users/:id"])
        trace = CodeGraphQuery(knowledge.code_graph).trace_interface("/api/users/:id")
        self.assertTrue(trace)
        self.assertEqual(trace[0].metadata["route"], "/api/users/:id")
        self.assertEqual(trace[0].metadata["method"], "GET")
        self.assertTrue(any(node.name == "GET" and node.kind == "function" for node in trace))


class UtilsTest(unittest.TestCase):
    def test_write_json_uses_ascii_safe_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "payload.json"
            value = "C:\\Users\\asd\\Desktop\\火山杯\\examples"
            write_json(path, {"root": value})
            text = path.read_text(encoding="utf-8")
            self.assertTrue(text.isascii())
            self.assertIn("\\u706b\\u5c71\\u676f", text)
            self.assertEqual(json.loads(text)["root"], value)


class WorkflowTest(unittest.TestCase):
    def test_workflow_writes_outputs_and_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "aegis"
            first = AegisWorkflow(SAMPLE, output_root=out, max_files=100).run()
            second = AegisWorkflow(SAMPLE, output_root=out, max_files=100).run()
            self.assertTrue((first.output_dir / "report.md").exists())
            self.assertTrue((first.output_dir / "report.html").exists())
            self.assertTrue((first.output_dir / "architecture.mmd").exists())
            self.assertTrue((first.output_dir / "rag_index.json").exists())
            self.assertTrue((first.output_dir / "manifest.json").exists())
            self.assertTrue((first.output_dir / "run_summary.json").exists())
            self.assertTrue((first.output_dir / "handoff_card.json").exists())
            self.assertGreater(second.knowledge.stats.get("cache_hits", 0), 0)
            data = json.loads((second.output_dir / "knowledge.json").read_text(encoding="utf-8"))
            self.assertIn("call_graph", data)
            self.assertIn("rag", data["stats"])
            summary = json.loads((second.output_dir / "run_summary.json").read_text(encoding="utf-8"))
            handoff = json.loads((second.output_dir / "handoff_card.json").read_text(encoding="utf-8"))
            self.assertEqual(summary["repo"]["name"], "sample_repo")
            self.assertEqual(summary["status"], "analyzed")
            self.assertFalse(summary["qa"]["available"])
            self.assertTrue(summary["artifacts"]["knowledge.json"]["exists"])
            self.assertTrue(summary["artifacts"]["run_summary.json"]["exists"])
            self.assertTrue(summary["artifacts"]["handoff_card.json"]["exists"])
            self.assertEqual(summary["orchestration"]["recommended_command"], "ask")
            self.assertEqual(
                summary["orchestration"]["recommended_args"],
                [
                    "--from-output",
                    str(second.output_dir),
                    "--ask",
                    "<question>",
                    "--json",
                ],
            )
            self.assertIn("--ask", summary["orchestration"]["recommended_command_line"])
            self.assertFalse(summary["orchestration"]["requires_fresh_analysis"])
            self.assertTrue(summary["orchestration"]["recovery_commands"])
            self.assertIn("No QA artifact is available yet", summary["orchestration"]["why_recommended"])
            self.assertTrue(summary["orchestration"]["repair_ready"])
            self.assertEqual(summary["orchestration"]["blocking_issues_count"], 3)
            self.assertIsNotNone(summary["orchestration"]["primary_repair_step"])
            self.assertEqual(
                summary["orchestration"]["primary_repair_step"]["id"],
                "qa_missing",
            )
            self.assertEqual(
                summary["orchestration"]["repair_plan"][0]["recommended_command"],
                "ask",
            )
            self.assertIn("ask", summary["orchestration"]["can_reuse_for"])
            self.assertEqual(
                summary["artifacts"]["run_summary.json"]["size"],
                (second.output_dir / "run_summary.json").stat().st_size,
            )
            self.assertTrue(summary["artifact_contracts"]["reusable_ready"])
            self.assertIn("run_summary.json", summary["artifact_contracts"]["items"])
            self.assertEqual(
                summary["artifact_contracts"]["items"]["run_summary.json"]["depends_on"],
                ["knowledge.json", "manifest.json"],
            )
            self.assertEqual(
                summary["artifact_contracts"]["items"]["handoff_card.json"]["depends_on"],
                ["knowledge.json", "run_summary.json"],
            )
            self.assertEqual(handoff["repo"]["name"], "sample_repo")
            self.assertEqual(handoff["recommended_action"]["command"], "ask")
            self.assertEqual(handoff["primary_task"]["id"], "qa_missing")
            manifest = json.loads((second.output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["schema_version"], "1.1")
            self.assertEqual(manifest["repo"]["name"], "sample_repo")
            self.assertNotIn("post_run", manifest["run"])
            self.assertTrue(manifest["artifacts"]["knowledge.json"]["exists"])
            self.assertTrue(manifest["artifacts"]["run_summary.json"]["exists"])
            self.assertTrue(manifest["artifacts"]["handoff_card.json"]["exists"])
            self.assertEqual(
                manifest["artifacts"]["qa_answer.json"]["depends_on"],
                ["knowledge.json", "rag_index.json"],
            )
            self.assertEqual(
                manifest["artifacts"]["handoff_card.json"]["depends_on"],
                ["knowledge.json", "run_summary.json"],
            )
            self.assertEqual(
                manifest["artifacts"]["readiness.json"]["optional_depends_on"],
                ["qa_answer.json", "context_pack.md", "llm_prompt.md"],
            )
            self.assertEqual(
                manifest["artifacts"]["run_summary.json"]["sha256"],
                file_sha256(second.output_dir / "run_summary.json"),
            )
            self.assertEqual(
                manifest["artifacts"]["knowledge.json"]["sha256"],
                file_sha256(second.output_dir / "knowledge.json"),
            )
            self.assertEqual(len(manifest["artifacts"]["knowledge.json"]["sha256"]), 64)

    def test_saved_artifacts_can_be_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            loaded = load_analysis_result(result.output_dir)
            rag = load_rag_index(result.output_dir / "rag_index.json")
            self.assertEqual(loaded.knowledge.repo_name, "sample_repo")
            self.assertEqual(loaded.output_dir, result.output_dir)
            self.assertGreater(len(rag.chunks), 0)

    def test_readiness_rejects_stale_manifest_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "report.md").write_text("tampered report\n", encoding="utf-8")
            readiness = ReadinessAssessor(
                result,
                doctor_payload={"passed": True, "errors": 0, "warnings": 0},
            ).run()
            manifest_check = next(check for check in readiness["checks"] if check["name"] == "manifest")
            self.assertEqual(manifest_check["status"], "error")
            self.assertIn("report.md", manifest_check["detail"]["hash_mismatches"])
            self.assertFalse(readiness["passed"])

    def test_report_includes_scan_scope_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "aegis"
            result = AegisWorkflow(
                SAMPLE,
                output_root=out,
                max_files=100,
                include=["*.py"],
                exclude=["app.py"],
                use_cache=False,
            ).run()
            report = (result.output_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("Include 范围", report)
            self.assertIn("Exclude 范围", report)
            self.assertIn("跳过文件", report)
            html_report = (result.output_dir / "report.html").read_text(encoding="utf-8")
            self.assertIn('id="report-search"', html_report)
            self.assertIn('href="knowledge.json"', html_report)
            self.assertIn('href="rag_index.json"', html_report)
            self.assertIn('href="manifest.json"', html_report)
            self.assertIn('href="run_summary.json"', html_report)
            self.assertIn("function applyFilter()", html_report)


class ContextRouterTest(unittest.TestCase):
    def test_context_router_includes_line_numbered_source(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        context = ContextRouter(knowledge, max_chars=6000).route("interface")

        self.assertIn("FILE app.py", context)
        self.assertIn("source_context:", context)
        self.assertIn("Source file: app.py", context)
        self.assertIn("Complete file: yes", context)
        self.assertIn("1: from fastapi import FastAPI", context)
        self.assertIn("15: def create_user(payload: dict):", context)

    def test_context_router_marks_truncated_source(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "large.py").write_text(
                "\n".join(f"def fn_{idx}(): return {idx}" for idx in range(120)),
                encoding="utf-8",
            )
            knowledge = KnowledgeBuilder(root, max_files=10, use_cache=False).build()
            context = ContextRouter(knowledge, max_chars=900).route("architecture")

            self.assertIn("Source file: large.py", context)
            self.assertIn("Complete file: no", context)
            self.assertIn("truncated by context budget", context)

    def test_llm_repository_analyst_prompt_includes_source_context(self) -> None:
        class CapturingLLM:
            available = True

            def __init__(self) -> None:
                self.user = ""

            def complete(self, *, system: str, user: str) -> str:
                self.user = user
                return "基于源码的综合分析"

        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        llm = CapturingLLM()
        findings = LLMRepositoryAnalyst(llm, ContextRouter(knowledge, max_chars=8000)).analyze(
            knowledge
        )

        self.assertEqual(findings[0].title, "LLM 综合分析")
        self.assertIn("source_context:", llm.user)
        self.assertIn("Source file: app.py", llm.user)
        self.assertIn("15: def create_user(payload: dict):", llm.user)

    def test_llm_repository_analyst_marks_prompt_budget_truncation(self) -> None:
        class CapturingLLM:
            available = True

            def __init__(self) -> None:
                self.user = ""

            def complete(self, *, system: str, user: str) -> str:
                self.user = user
                return "预算内分析"

        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        llm = CapturingLLM()
        LLMRepositoryAnalyst(llm, ContextRouter(knowledge, max_chars=1400)).analyze(knowledge)

        self.assertIn("truncated by LLM context budget", llm.user)
        self.assertLess(len(llm.user), 2200)


class RAGRecallTest(unittest.TestCase):
    def test_natural_language_architecture_queries_hit_expected_files(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        retriever = RAGRetriever(index)
        cases = [
            ("项目入口在哪里", {"src/main_entrypoint.py"}),
            ("布线核心模块是什么", {"src/routing/rw_route.py"}),
            ("模块布局和硬宏布局", {"src/placement/block_placer.py"}),
            ("是否依赖 Vivado 外部工具", {"src/integrations/vivado_tools.py"}),
            ("时序分析延迟模型", {"src/timing/timing_model.py"}),
            ("partial DFX routing", {"src/routing/partial_dfx_router.py"}),
            ("项目是否支持完整 RTL 流程", {"src/rtl/rtl_flow.py"}),
            ("器件资源在哪里加载", {"src/device/device_resources.py"}),
        ]
        hits = 0
        for query, expected_paths in cases:
            results = retriever.search(query, top_k=10)
            result_paths = {result.chunk.path for result in results if result.chunk.path}
            if expected_paths.intersection(result_paths):
                hits += 1
        self.assertGreaterEqual(hits, 6)

    def test_rag_context_includes_real_source_lines_for_llm(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        retriever = RAGRetriever(index)
        context = retriever.context("项目入口在哪里", top_k=4, max_chars=6000)
        self.assertIn("kind=source", context)
        self.assertIn("Files in context:", context)
        self.assertIn("Complete files in context:", context)
        self.assertIn("Code:", context)
        self.assertIn("class MainEntrypoint", context)
        pack = retriever.context_pack("项目入口在哪里", top_k=4, max_chars=6000)
        self.assertGreater(len(pack.blocks), 0)
        self.assertIn("src/main_entrypoint.py", pack.source_paths())
        self.assertEqual(pack.blocks[0].chunk_kind, "source")
        self.assertEqual(pack.blocks[0].path, "src/main_entrypoint.py")
        self.assertEqual(pack.blocks[0].context_mode, "full_file")
        self.assertTrue(pack.blocks[0].complete_file)
        self.assertIn("src/main_entrypoint.py", pack.complete_file_paths())
        self.assertIn("src/main_entrypoint.py", pack.target_context_paths)
        self.assertEqual(pack.missing_target_context_paths(), [])
        self.assertEqual(pack.incomplete_target_context_paths(), [])
        self.assertTrue(pack.to_dict()["target_context_satisfied"])
        self.assertIn("Complete file: yes", pack.blocks[0].content)
        self.assertIn("class MainEntrypoint", pack.blocks[0].content)
        self.assertGreaterEqual(pack.blocks[0].start_line or 0, 1)
        self.assertEqual(pack.to_dict()["source_paths"][0], "src/main_entrypoint.py")
        self.assertEqual(pack.to_dict()["complete_file_paths"][0], "src/main_entrypoint.py")

    def test_rag_context_packs_whole_files_for_route_questions(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        pack = RAGRetriever(index).context_pack("POST /users 用户创建 数据写入", top_k=5, max_chars=20000)
        complete_paths = set(pack.complete_file_paths())
        self.assertIn("app.py", complete_paths)
        self.assertIn("services/user_service.py", complete_paths)
        self.assertIn("repositories/user_repository.py", complete_paths)
        self.assertIn("app.py", pack.target_context_paths)
        self.assertGreater(pack.target_context_budget_chars, 0)
        self.assertEqual(pack.missing_target_context_paths(), [])
        self.assertEqual(pack.incomplete_target_context_paths(), [])
        app_block = next(block for block in pack.blocks if block.path == "app.py")
        self.assertTrue(app_block.complete_file)
        self.assertEqual(app_block.context_mode, "full_file")
        self.assertIn("Complete file: yes", app_block.content)
        self.assertIn("def create_user", app_block.content)
        repo_block = next(block for block in pack.blocks if block.path == "repositories/user_repository.py")
        self.assertIn("self.rows.append", repo_block.content)
        payload = pack.to_dict()
        self.assertTrue(any(block["complete_file"] for block in payload["blocks"]))
        self.assertGreater(payload["target_context_budget_chars"], 0)

    def test_rag_context_expands_relation_hits_to_both_source_files(self) -> None:
        index = RAGIndex(
            repo_name="relation_repo",
            chunks=[
                RAGChunk(
                    id="source:caller.py:1-2",
                    kind="source",
                    title="caller.py:1-2",
                    text="\n".join(
                        [
                            "Source file: caller.py",
                            "Language: Python",
                            "Line range: 1-2",
                            "Code:",
                            "1: from callee import target",
                            "2: target()",
                        ]
                    ),
                    path="caller.py",
                    line=1,
                    node_ids=["file:caller.py"],
                    metadata={"start_line": 1, "end_line": 2, "language": "Python"},
                ),
                RAGChunk(
                    id="source:callee.py:1-2",
                    kind="source",
                    title="callee.py:1-2",
                    text="\n".join(
                        [
                            "Source file: callee.py",
                            "Language: Python",
                            "Line range: 1-2",
                            "Code:",
                            "1: def target():",
                            "2:     return 'called'",
                        ]
                    ),
                    path="callee.py",
                    line=1,
                    node_ids=["file:callee.py"],
                    metadata={"start_line": 1, "end_line": 2, "language": "Python"},
                ),
                RAGChunk(
                    id="edge:caller-callee",
                    kind="edge:calls_file",
                    title="caller.py calls callee.py",
                    text="needle_relation caller.py --calls_file--> callee.py",
                    path="caller.py",
                    line=2,
                    node_ids=["file:caller.py", "file:callee.py"],
                    metadata={"kind": "calls_file"},
                ),
            ],
        )
        pack = RAGRetriever(index).context_pack("needle_relation", top_k=1, max_chars=8000)

        self.assertEqual(pack.target_context_paths, ["caller.py", "callee.py"])
        self.assertEqual(pack.missing_target_context_paths(), [])
        self.assertEqual(pack.incomplete_target_context_paths(), [])
        self.assertIn("caller.py", pack.complete_file_paths())
        self.assertIn("callee.py", pack.complete_file_paths())
        self.assertIn("def target", pack.render())

    def test_qa_agent_forces_explicit_file_mentions_into_prompt_context(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer(
            "please analyze src/timing/timing_model.py",
            top_k=1,
            max_context_chars=12000,
        )
        self.assertIn("src/timing/timing_model.py", answer.required_context_paths)
        self.assertIn("src/timing/timing_model.py", answer.context_pack.complete_file_paths())
        self.assertIn("Complete file: yes", answer.llm_user_prompt)
        self.assertIn("class TimingModel", answer.llm_user_prompt)

    def test_qa_agent_forces_context_files_into_prompt_context(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer(
            "Where is the entrypoint?",
            top_k=1,
            max_context_chars=12000,
            context_files=["src/timing/timing_model.py"],
        )
        self.assertIn("src/timing/timing_model.py", answer.required_context_paths)
        self.assertIn("src/timing/timing_model.py", answer.context_pack.target_context_paths)
        self.assertIn("src/timing/timing_model.py", answer.context_pack.complete_file_paths())
        self.assertEqual(answer.context_pack.missing_required_context_paths(), [])
        self.assertEqual(answer.context_pack.incomplete_required_context_paths(), [])
        self.assertIn("class TimingModel", answer.llm_user_prompt)

    def test_qa_agent_forces_unique_symbol_mentions_into_prompt_context(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer(
            "Explain TimingModel behavior",
            top_k=1,
            max_context_chars=12000,
        )
        self.assertIn("src/timing/timing_model.py", answer.required_context_paths)
        self.assertIn("src/timing/timing_model.py", answer.context_pack.complete_file_paths())
        self.assertEqual(answer.context_pack.missing_required_context_paths(), [])
        self.assertEqual(answer.context_pack.incomplete_required_context_paths(), [])
        self.assertIn("class TimingModel", answer.llm_user_prompt)

    def test_qa_agent_expands_unique_symbol_questions_with_related_source_files(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer(
            "Explain UserService implementation chain",
            top_k=2,
            max_context_chars=20000,
        )
        self.assertIn("services/user_service.py", answer.required_context_paths)
        self.assertIn("repositories/user_repository.py", answer.required_context_paths)
        self.assertIn("services/user_service.py", answer.context_pack.complete_file_paths())
        self.assertIn("repositories/user_repository.py", answer.context_pack.complete_file_paths())
        self.assertEqual(answer.context_pack.missing_required_context_paths(), [])
        self.assertEqual(answer.context_pack.incomplete_required_context_paths(), [])
        self.assertIn("class UserService", answer.llm_user_prompt)
        self.assertIn("class UserRepository", answer.llm_user_prompt)

    def test_qa_agent_builds_investigation_brief_with_reading_order(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer(
            "POST /users 的调用链路是什么？",
            top_k=4,
            max_context_chars=24000,
        )
        brief = answer.investigation_brief
        self.assertIsNotNone(brief)
        assert brief is not None
        self.assertEqual(brief["route"], "/users")
        self.assertIn("app.py", brief["required_context_paths"])
        self.assertIn("services/user_service.py", brief["required_context_paths"])
        self.assertIn("repositories/user_repository.py", brief["required_context_paths"])
        self.assertTrue(brief["reading_order"])
        self.assertEqual(brief["reading_order"][0]["label"], "required_complete")
        self.assertIn("Read required files before supporting files.", brief["guardrails"])
        self.assertIn("Investigation Brief:", answer.llm_user_prompt)
        self.assertIn("Reading order:", answer.llm_user_prompt)

    def test_required_context_contract_reports_missing_files(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        pack = RAGRetriever(index).context_pack(
            "please analyze src/timing/timing_model.py",
            top_k=1,
            max_chars=120,
            required_paths=["src/timing/timing_model.py"],
        )
        self.assertEqual(pack.required_context_paths, ["src/timing/timing_model.py"])
        self.assertEqual(pack.missing_required_context_paths(), ["src/timing/timing_model.py"])
        self.assertIn("src/timing/timing_model.py", pack.missing_target_context_paths())
        self.assertFalse(pack.to_dict()["required_context_satisfied"])
        self.assertFalse(pack.to_dict()["target_context_satisfied"])
        self.assertIn("Missing required context paths: src/timing/timing_model.py", pack.render())
        self.assertIn("Missing target context paths: src/timing/timing_model.py", pack.render())

    def test_required_context_contract_reports_incomplete_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [f"def fn_{idx}(): return '{idx}' * 40" for idx in range(80)]
            (root / "large_module.py").write_text("\n".join(lines), encoding="utf-8")
            knowledge = KnowledgeBuilder(root, max_files=10, use_cache=False).build()
            index = RAGIndexBuilder(knowledge).build()
            pack = RAGRetriever(index).context_pack(
                "please analyze large_module.py",
                top_k=1,
                max_chars=700,
                required_paths=["large_module.py"],
            )
            self.assertEqual(pack.missing_required_context_paths(), [])
            self.assertEqual(pack.incomplete_required_context_paths(), ["large_module.py"])
            self.assertEqual(pack.incomplete_target_context_paths(), ["large_module.py"])
            self.assertGreater(pack.required_context_budget_chars, pack.max_chars)
            self.assertGreater(pack.to_dict()["required_context_budget_chars"], pack.max_chars)
            self.assertEqual(pack.unsatisfied_required_context_paths(), ["large_module.py"])
            self.assertEqual(pack.unsatisfied_target_context_paths(), ["large_module.py"])
            self.assertFalse(pack.to_dict()["required_context_satisfied"])
            self.assertFalse(pack.to_dict()["target_context_satisfied"])
            self.assertIn("Incomplete required context paths: large_module.py", pack.render())
            self.assertIn("Incomplete target context paths: large_module.py", pack.render())
            self.assertIn("Required complete-file budget estimate:", pack.render())

    def test_qa_agent_skips_llm_when_required_context_is_missing(self) -> None:
        class FailingLLM:
            @property
            def available(self) -> bool:
                return True

            def complete(self, *, system: str, user: str) -> str:
                raise AssertionError("LLM must not be called without required context")

        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index, llm=FailingLLM()).answer(
            "please analyze src/timing/timing_model.py",
            top_k=1,
            max_context_chars=120,
        )
        self.assertFalse(answer.used_llm)
        self.assertEqual(answer.context_pack.missing_required_context_paths(), ["src/timing/timing_model.py"])
        self.assertIn("Required context missing", answer.answer)
        self.assertIn("LLM request skipped", answer.answer)

    def test_qa_agent_skips_llm_when_required_context_is_incomplete(self) -> None:
        class FailingLLM:
            @property
            def available(self) -> bool:
                return True

            def complete(self, *, system: str, user: str) -> str:
                raise AssertionError("LLM must not be called with incomplete required context")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [f"def fn_{idx}(): return '{idx}' * 40" for idx in range(80)]
            (root / "large_module.py").write_text("\n".join(lines), encoding="utf-8")
            knowledge = KnowledgeBuilder(root, max_files=10, use_cache=False).build()
            index = RAGIndexBuilder(knowledge).build()
            answer = RepositoryQAAgent(knowledge, index, llm=FailingLLM()).answer(
                "please analyze large_module.py",
                top_k=1,
                max_context_chars=700,
            )
        self.assertFalse(answer.used_llm)
        self.assertEqual(answer.context_pack.incomplete_required_context_paths(), ["large_module.py"])
        self.assertIn("Required context missing or incomplete", answer.answer)
        self.assertIn("LLM request skipped", answer.answer)

    def test_qa_agent_skips_llm_when_no_source_files_reach_context(self) -> None:
        class FailingLLM:
            @property
            def available(self) -> bool:
                return True

            def complete(self, *, system: str, user: str) -> str:
                raise AssertionError("LLM must not be called without source file context")

        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        full_index = RAGIndexBuilder(knowledge).build()
        metadata_only_index = RAGIndex(
            repo_name=full_index.repo_name,
            chunks=[chunk for chunk in full_index.chunks if chunk.kind != "source"],
            stats=full_index.stats,
        )
        answer = RepositoryQAAgent(knowledge, metadata_only_index, llm=FailingLLM()).answer(
            "explain POST /users",
            top_k=3,
            max_context_chars=8000,
        )
        self.assertFalse(answer.used_llm)
        self.assertFalse(answer.context_safe_for_llm)
        self.assertIn("no real source file content", answer.llm_skip_reason)
        self.assertFalse(answer.context_pack.source_context_satisfied())
        self.assertIn("LLM request skipped", answer.answer)

    def test_qa_agent_skips_llm_when_only_partial_source_reaches_context(self) -> None:
        class FailingLLM:
            @property
            def available(self) -> bool:
                return True

            def complete(self, *, system: str, user: str) -> str:
                raise AssertionError("LLM must not be called without a complete source file")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lines = [f"def fn_{idx}(): return '{idx}' * 40" for idx in range(80)]
            (root / "large_module.py").write_text("\n".join(lines), encoding="utf-8")
            knowledge = KnowledgeBuilder(root, max_files=10, use_cache=False).build()
            index = RAGIndexBuilder(knowledge).build()
            answer = RepositoryQAAgent(knowledge, index, llm=FailingLLM()).answer(
                "what does fn_20 return",
                top_k=1,
                max_context_chars=700,
            )
        self.assertFalse(answer.used_llm)
        self.assertFalse(answer.context_safe_for_llm)
        self.assertTrue(answer.context_pack.source_context_satisfied())
        self.assertFalse(answer.context_pack.complete_file_context_satisfied())
        self.assertEqual(answer.context_pack.incomplete_target_context_paths(), ["large_module.py"])
        self.assertIn("no complete source file", answer.llm_skip_reason)
        self.assertIn("retrieved target files are missing or incomplete", answer.llm_skip_reason)
        self.assertIn("LLM request skipped", answer.answer)

    def test_offline_qa_prints_source_context(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer("布局器处理普通单元还是硬宏", top_k=4)
        self.assertIn("Source context", answer.answer)
        self.assertIn("return \"module layout and hard macro placement\"", answer.answer)

    def test_offline_qa_source_excerpt_is_centered_on_hit_line(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer("项目入口在哪里", top_k=2)
        self.assertIn("class StandaloneEntrypoint", answer.answer)
        self.assertIn("return MainEntrypoint().run()", answer.answer)


class EvaluationTest(unittest.TestCase):
    def test_builtin_evaluation_reports_recall_and_source_coverage(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        evaluation = Evaluator(knowledge, index).run(builtin_suite("eda_repo"))
        metrics = evaluation["metrics"]
        self.assertEqual(metrics["rag_cases"], 8)
        self.assertGreaterEqual(metrics["rag_recall"], 0.75)
        self.assertGreaterEqual(metrics["source_context_coverage"], 0.75)
        self.assertGreaterEqual(metrics["prompt_context_coverage"], 0.75)
        self.assertGreaterEqual(metrics["complete_file_context_coverage"], 0.75)
        self.assertGreaterEqual(metrics["prompt_context_expected_path_coverage"], 0.75)
        self.assertGreaterEqual(metrics["complete_file_expected_path_coverage"], 0.75)
        first_case = evaluation["rag"][0]
        self.assertTrue(first_case["prompt_context_available"])
        self.assertTrue(first_case["complete_file_context_available"])
        self.assertIn("src/main_entrypoint.py", first_case["complete_file_paths"])

    def test_route_eval_requires_all_expected_files_in_prompt_context(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        evaluation = Evaluator(knowledge, index).run(builtin_suite("sample_repo"))
        metrics = evaluation["metrics"]
        self.assertEqual(metrics["prompt_context_expected_path_coverage"], 1.0)
        self.assertEqual(metrics["complete_file_expected_path_coverage"], 1.0)
        route_case = next(case for case in evaluation["rag"] if "/users" in case["question"])
        self.assertTrue(route_case["prompt_context_available"])
        self.assertTrue(route_case["complete_file_context_available"])
        self.assertEqual(
            set(route_case["complete_file_matched_paths"]),
            {"app.py", "services/user_service.py", "repositories/user_repository.py"},
        )
        self.assertIn("repositories/user_repository.py", route_case["required_context_paths"])

    def test_custom_eval_suite_file_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "custom",
                        "rag": [
                            {
                                "question": "项目入口在哪里",
                                "expected_paths": ["src/main_entrypoint.py"],
                                "top_k": 5,
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            suite = load_suite(suite_path)
            self.assertEqual(suite.name, "custom")
            self.assertEqual(suite.rag[0].top_k, 5)

    def test_custom_eval_suite_accepts_utf8_sig(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "suite.json"
            suite_path.write_text(
                "\ufeff"
                + json.dumps(
                    {
                        "name": "bom-suite",
                        "rag": [
                            {
                                "question": "项目入口在哪里",
                                "expected_paths": ["src/main_entrypoint.py"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            suite = load_suite(suite_path)
            self.assertEqual(suite.name, "bom-suite")


class EnvConfigTest(unittest.TestCase):
    def test_env_example_covers_runtime_configuration(self) -> None:
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        required = {
            "AEGIS_REPO_PATH",
            "AEGIS_OUTPUT_DIR",
            "AEGIS_MAX_FILES",
            "AEGIS_RAG_CONTEXT_CHARS",
            "AEGIS_INCLUDE",
            "AEGIS_EXCLUDE",
            "AEGIS_USE_CACHE",
            "AEGIS_SERVE_DIR",
            "AEGIS_SERVE_HOST",
            "AEGIS_SERVE_PORT",
            "AEGIS_LLM_ENABLED",
            "AEGIS_LLM_API_KEY",
            "AEGIS_LLM_BASE_URL",
            "AEGIS_LLM_MODEL",
            "AEGIS_LLM_TIMEOUT_SECONDS",
            "AEGIS_LLM_MAX_CONTEXT_CHARS",
        }
        for key in required:
            self.assertIn(f"{key}=", text)
        old_values = {key: os.environ.get(key) for key in required}
        try:
            for key in required:
                os.environ.pop(key, None)
            load_env_file(ROOT / ".env.example")
            config = AegisConfig.from_env()
            self.assertEqual(config.repo_path, "examples/sample_repo")
            self.assertEqual(config.output_dir, "output/aegis")
            self.assertEqual(config.rag_context_chars, 48000)
            self.assertEqual(config.include, [])
            self.assertEqual(config.exclude, [])
            self.assertFalse(config.llm.enabled)
        finally:
            for key, value in old_values.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_load_env_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "AEGIS_REPO_PATH=examples/sample_repo\n"
                "AEGIS_MAX_FILES=42\n"
                "AEGIS_USE_CACHE=false\n",
                encoding="utf-8",
            )
            old_values = {key: os.environ.get(key) for key in ("AEGIS_REPO_PATH", "AEGIS_MAX_FILES", "AEGIS_USE_CACHE")}
            try:
                for key in old_values:
                    os.environ.pop(key, None)
                load_env_file(path)
                config = AegisConfig.from_env()
                self.assertEqual(config.repo_path, "examples/sample_repo")
                self.assertEqual(config.max_files, 42)
                self.assertFalse(config.use_cache)
            finally:
                for key, value in old_values.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value

    def test_scan_scope_from_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(
                "AEGIS_INCLUDE=*.py, services/*.py\n"
                "AEGIS_EXCLUDE=app.py\n",
                encoding="utf-8",
            )
            old_values = {key: os.environ.get(key) for key in ("AEGIS_INCLUDE", "AEGIS_EXCLUDE")}
            try:
                for key in old_values:
                    os.environ.pop(key, None)
                load_env_file(path)
                config = AegisConfig.from_env()
                self.assertEqual(config.include, ["*.py", "services/*.py"])
                self.assertEqual(config.exclude, ["app.py"])
            finally:
                for key, value in old_values.items():
                    if value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = value


class FakeHTTPResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    def __enter__(self) -> "FakeHTTPResponse":
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class LLMClientTest(unittest.TestCase):
    def test_llm_client_sends_openai_compatible_chat_request(self) -> None:
        config = LLMConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="test-model",
        )
        captured = {}

        def fake_urlopen(request: object, timeout: int) -> FakeHTTPResponse:
            captured["url"] = request.full_url
            captured["timeout"] = timeout
            captured["headers"] = dict(request.header_items())
            captured["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeHTTPResponse(
                {"choices": [{"message": {"content": [{"text": "hello"}, {"text": " world"}]}}]}
            )

        with patch("urllib.request.urlopen", fake_urlopen):
            answer = LLMClient(config).complete(system="system prompt", user="user prompt")

        self.assertEqual(answer, "hello world")
        self.assertEqual(captured["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(captured["timeout"], 120)
        self.assertEqual(captured["headers"]["Authorization"], "Bearer test-key")
        self.assertEqual(captured["payload"]["model"], "test-model")
        self.assertEqual(captured["payload"]["messages"][0]["role"], "system")
        self.assertEqual(captured["payload"]["messages"][1]["content"], "user prompt")

    def test_llm_client_reports_http_error_body(self) -> None:
        config = LLMConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="test-model",
        )

        def fake_urlopen(request: object, timeout: int) -> FakeHTTPResponse:
            raise urllib.error.HTTPError(
                url=request.full_url,
                code=401,
                msg="Unauthorized",
                hdrs={},
                fp=io.BytesIO(b'{"error":"bad key"}'),
            )

        with patch("urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(LLMError) as raised:
                LLMClient(config).complete(system="system", user="user")

        self.assertIn("HTTP 401", str(raised.exception))
        self.assertIn("bad key", str(raised.exception))

    def test_llm_client_rejects_empty_completion(self) -> None:
        config = LLMConfig(
            enabled=True,
            api_key="test-key",
            base_url="https://llm.example/v1",
            model="test-model",
        )

        with patch(
            "urllib.request.urlopen",
            lambda request, timeout: FakeHTTPResponse({"choices": [{"message": {"content": ""}}]}),
        ):
            with self.assertRaisesRegex(LLMError, "empty"):
                LLMClient(config).complete(system="system", user="user")

    def test_llm_client_reports_invalid_base_url(self) -> None:
        config = LLMConfig(
            enabled=True,
            api_key="test-key",
            base_url="not-a-url",
            model="test-model",
        )

        with self.assertRaisesRegex(LLMError, "URL is invalid"):
            LLMClient(config).complete(system="system", user="user")


class PackagingTest(unittest.TestCase):
    def test_console_script_module_is_packaged(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(data["project"]["scripts"]["aegis"], "main:main")
        self.assertIn("main", data["tool"]["setuptools"]["py-modules"])

    def test_package_metadata_is_release_ready(self) -> None:
        data = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        project = data["project"]
        self.assertEqual(project["version"], aegis.__version__)
        self.assertIn("AEGIS 2.0", project["description"])
        self.assertIn("Volcano Cup", project["description"])
        self.assertNotIn("MVP", aegis.__doc__ or "")
        self.assertIn("Development Status :: 4 - Beta", project["classifiers"])
        mojibake_markers = ["鐏", "鏉", "櫤", "鑳", "\ufffd"]
        for marker in mojibake_markers:
            self.assertNotIn(marker, project["description"])

    def test_skill_wrapper_defaults_match_runtime_context_budget(self) -> None:
        script = (ROOT / "skills" / "aegis-repo-analyst" / "scripts" / "run_aegis.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('ask.add_argument("--context-chars", default="48000")', script)
        self.assertIn('ask.add_argument("--context-file", action="append", default=[])', script)
        self.assertIn('ready.add_argument("--context-chars", default="48000")', script)
        self.assertIn('ready.add_argument("--context-file", action="append", default=[])', script)

    def test_skill_wrapper_exposes_optional_llm_flag(self) -> None:
        script = (ROOT / "skills" / "aegis-repo-analyst" / "scripts" / "run_aegis.py").read_text(
            encoding="utf-8"
        )
        self.assertIn('analyze.add_argument("--llm", action="store_true")', script)
        self.assertIn('ready.add_argument("--llm", action="store_true")', script)
        self.assertIn('status = sub.add_parser("status")', script)
        self.assertIn('ready.add_argument("--ask")', script)
        self.assertIn('if getattr(args, "llm", False):', script)

    def test_skill_docs_promote_handoff_card_entrypoint(self) -> None:
        skill = (ROOT / "skills" / "aegis-repo-analyst" / "SKILL.md").read_text(
            encoding="utf-8"
        )
        self.assertIn("handoff_card.json", skill)
        self.assertIn("Inspect `handoff_card.json` first", skill)
        self.assertIn("primary_task.recommended_command_line", skill)
        self.assertIn("docs/DEMO.md", skill)
        self.assertIn("run_summary.json", skill)
        self.assertIn("`status` tells whether", skill)
        self.assertIn("`next_actions`", skill)

    def test_readme_mentions_handoff_interface(self) -> None:
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn("handoff_card.json", readme)
        self.assertIn("--handoff --json", readme)
        self.assertIn("default machine interface", readme)
        self.assertIn("primary_task.recommended_command_line", readme)
        self.assertIn("docs/DEMO.md", readme)
        self.assertIn("docs/RELEASE_CHECKLIST.md", readme)

    def test_demo_and_release_docs_exist(self) -> None:
        demo = (ROOT / "docs" / "DEMO.md").read_text(encoding="utf-8")
        demo_results = (ROOT / "docs" / "DEMO_RESULTS.md").read_text(encoding="utf-8")
        release = (ROOT / "docs" / "RELEASE_CHECKLIST.md").read_text(encoding="utf-8")
        self.assertIn("--doctor --json", demo)
        self.assertIn("--handoff --json", demo)
        self.assertIn("primary_task.recommended_command_line", demo)
        self.assertIn("output/demo_release/sample_repo", demo_results)
        self.assertIn("primary task replay command", demo_results.lower())
        self.assertIn("python -m unittest discover -s tests -v", release)
        self.assertIn("handoff_card.json", release)


class DoctorTest(unittest.TestCase):
    def test_doctor_json_passes_for_valid_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--doctor",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["doctor"]["passed"])

    def test_doctor_fails_without_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--out",
                    tmp,
                    "--doctor",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["doctor"]["passed"])

    def test_doctor_rejects_invalid_llm_url(self) -> None:
        payload = Doctor(
            repo=SAMPLE,
            output_root=ROOT / "output" / "test-doctor",
            llm_config=LLMConfig(
                enabled=True,
                api_key="test-key",
                base_url="not-a-url",
                model="test-model",
            ),
        ).run()

        self.assertFalse(payload["passed"])
        llm_check = next(check for check in payload["checks"] if check["name"] == "llm")
        self.assertEqual(llm_check["status"], "error")
        self.assertIn("absolute http(s) URL", llm_check["message"])

    def test_doctor_warns_for_tiny_llm_context_budget(self) -> None:
        payload = Doctor(
            repo=SAMPLE,
            output_root=ROOT / "output" / "test-doctor",
            llm_config=LLMConfig(
                enabled=True,
                api_key="test-key",
                base_url="https://llm.example/v1",
                model="test-model",
                max_context_chars=1200,
            ),
        ).run()

        self.assertTrue(payload["passed"])
        self.assertEqual(payload["warnings"], 1)
        llm_check = next(check for check in payload["checks"] if check["name"] == "llm")
        self.assertEqual(llm_check["status"], "warning")
        self.assertIn("context budget", llm_check["message"])

    def test_skill_wrapper_doctor_json_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "doctor",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["doctor"]["passed"])


class CLITest(unittest.TestCase):
    def test_ask_json_output_is_machine_readable_with_source_excerpt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ask",
                    "项目入口在哪里",
                    "--top-k",
                    "2",
                    "--context-file",
                    "src/timing/timing_model.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["repo"], "eda_repo")
            self.assertIn("qa", payload)
            self.assertIn("handoff_card", payload)
            self.assertFalse(payload["qa"]["used_llm"])
            self.assertIn("context_pack", payload["qa"])
            self.assertIn("llm_prompt", payload["qa"])
            self.assertIn("investigation_brief", payload["qa"])
            self.assertTrue(payload["qa"]["required_context_satisfied"])
            self.assertTrue(payload["qa"]["target_context_satisfied"])
            self.assertIn("src/timing/timing_model.py", payload["qa"]["required_context_paths"])
            self.assertIn("reading_order", payload["qa"])
            self.assertTrue(payload["qa"]["reading_order"])
            self.assertIn("supporting_context_paths", payload["qa"])
            self.assertIn(
                "src/timing/timing_model.py",
                payload["qa"]["context_pack"]["complete_file_paths"],
            )
            self.assertEqual(payload["qa"]["missing_required_context_paths"], [])
            self.assertEqual(payload["qa"]["missing_target_context_paths"], [])
            self.assertTrue(Path(payload["outputs"]["qa_answer"]).exists())
            self.assertTrue(Path(payload["outputs"]["context_pack"]).exists())
            self.assertTrue(Path(payload["outputs"]["llm_prompt"]).exists())
            self.assertTrue(Path(payload["outputs"]["handoff_card"]).exists())
            qa_artifact = json.loads(Path(payload["outputs"]["qa_answer"]).read_text(encoding="utf-8"))
            handoff_artifact = json.loads(Path(payload["outputs"]["handoff_card"]).read_text(encoding="utf-8"))
            self.assertEqual(qa_artifact["question"], "项目入口在哪里")
            self.assertEqual(handoff_artifact["primary_task"]["source"], "qa")
            self.assertEqual(
                handoff_artifact["primary_task"]["investigation_brief"]["question"],
                qa_artifact["question"],
            )
            context_pack_artifact = Path(payload["outputs"]["context_pack"]).read_text(encoding="utf-8")
            self.assertIn("AEGIS RAG CONTEXT PACK", context_pack_artifact)
            self.assertIn("Reading order:", context_pack_artifact)
            self.assertIn("Target context satisfied: true", context_pack_artifact)
            self.assertIn("class StandaloneEntrypoint", context_pack_artifact)
            self.assertIn("class TimingModel", context_pack_artifact)
            llm_prompt_artifact = Path(payload["outputs"]["llm_prompt"]).read_text(encoding="utf-8")
            self.assertIn("## User Prompt", llm_prompt_artifact)
            self.assertIn("## Investigation Brief", llm_prompt_artifact)
            self.assertIn("Read required files before supporting files.", llm_prompt_artifact)
            self.assertIn("Target context satisfied: true", llm_prompt_artifact)
            self.assertIn("class StandaloneEntrypoint", llm_prompt_artifact)
            self.assertIn("class TimingModel", llm_prompt_artifact)
            manifest = json.loads(Path(payload["outputs"]["manifest"]).read_text(encoding="utf-8"))
            self.assertTrue(manifest["artifacts"]["qa_answer.json"]["exists"])
            self.assertTrue(manifest["artifacts"]["context_pack.md"]["exists"])
            self.assertTrue(manifest["artifacts"]["llm_prompt.md"]["exists"])
            self.assertTrue(manifest["artifacts"]["run_summary.json"]["exists"])
            self.assertTrue(manifest["artifacts"]["handoff_card.json"]["exists"])
            self.assertEqual(
                manifest["artifacts"]["context_pack.md"]["depends_on"],
                ["qa_answer.json"],
            )
            self.assertEqual(
                manifest["artifacts"]["llm_prompt.md"]["depends_on"],
                ["qa_answer.json"],
            )
            self.assertEqual(
                manifest["artifacts"]["run_summary.json"]["sha256"],
                file_sha256(Path(payload["outputs"]["run_summary"])),
            )
            self.assertEqual(
                manifest["artifacts"]["handoff_card.json"]["sha256"],
                file_sha256(Path(payload["outputs"]["handoff_card"])),
            )
            self.assertEqual(manifest["run"]["post_run"]["ask"], qa_artifact["question"])
            self.assertEqual(manifest["run"]["post_run"]["top_k"], 2)
            self.assertEqual(manifest["run"]["post_run"]["context_chars"], 48000)
            self.assertEqual(
                manifest["run"]["post_run"]["context_files"],
                ["src/timing/timing_model.py"],
            )
            context_blocks = payload["qa"]["context_pack"]["blocks"]
            self.assertTrue(context_blocks)
            self.assertTrue(any("class StandaloneEntrypoint" in block["content"] for block in context_blocks))
            summary = json.loads(Path(payload["outputs"]["run_summary"]).read_text(encoding="utf-8"))
            self.assertTrue(summary["artifact_contracts"]["reusable_ready"])
            self.assertEqual(
                summary["artifact_contracts"]["items"]["qa_answer.json"]["depends_on"],
                ["knowledge.json", "rag_index.json"],
            )
            self.assertEqual(
                summary["artifact_contracts"]["items"]["context_pack.md"]["depends_on"],
                ["qa_answer.json"],
            )
            self.assertEqual(summary["orchestration"]["recommended_command"], "eval")
            self.assertEqual(payload["orchestration"]["recommended_command"], "eval")
            self.assertEqual(payload["summary"]["orchestration"]["recommended_command"], "eval")
            self.assertIn("source_paths", payload["qa"])
            self.assertIn("complete_file_paths", payload["qa"])
            self.assertTrue(payload["qa"]["source_paths"])
            self.assertTrue(payload["qa"]["complete_file_paths"])
            self.assertEqual(payload["handoff_card"]["qa"]["question"], qa_artifact["question"])
            self.assertIn("ask", summary["orchestration"]["can_reuse_for"])
            excerpts = "\n".join(
                line
                for result in payload["qa"]["results"]
                for line in result["source_excerpt"]
            )
            self.assertIn("class StandaloneEntrypoint", excerpts)

    def test_ask_json_includes_graph_context_for_route_questions(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ask",
                    "POST /users 的调用链路是什么？",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            graph_context = payload["qa"]["graph_context"]
            self.assertEqual(graph_context["route"], "/users")
            names = {node["name"] for node in graph_context["nodes"]}
            self.assertIn("POST /users", names)
            self.assertIn("UserRepository", names)
            self.assertEqual(payload["qa"]["investigation_brief"]["route"], "/users")
            self.assertIn(
                "repositories/user_repository.py",
                payload["qa"]["investigation_brief"]["required_context_paths"],
            )
            source_paths = payload["qa"]["context_pack"]["source_paths"]
            self.assertIn("repositories/user_repository.py", source_paths)
            context_pack_artifact = Path(payload["outputs"]["context_pack"]).read_text(encoding="utf-8")
            self.assertIn("## Investigation Brief", context_pack_artifact)
            self.assertIn("## CodeGraph Context", context_pack_artifact)
            self.assertIn("repositories/user_repository.py", context_pack_artifact)

    def test_trace_json_output_is_machine_readable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--trace-interface",
                    "/users",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("trace", payload)
            names = [node["name"] for node in payload["trace"]["nodes"]]
            self.assertTrue(any("/users" in name for name in names))

    def test_status_json_reports_handoff_and_reuse_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--status",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("summary", payload)
            self.assertIn("status_report", payload)
            self.assertIn("handoff_card", payload)
            self.assertEqual(payload["summary"]["status"], "analyzed")
            self.assertEqual(payload["orchestration"]["recommended_command"], "ask")
            self.assertTrue(payload["status_report"]["manifest_integrity"]["ok"])
            self.assertEqual(
                payload["status_report"]["run_summary_artifact"]["source_used"],
                "artifact",
            )
            self.assertTrue(payload["status_report"]["run_summary_artifact"]["trusted"])
            self.assertTrue(payload["status_report"]["handoff_card_artifact"]["trusted"])
            self.assertTrue(payload["status_report"]["handoff_card_validation"]["ok"])
            self.assertIn("ask", payload["status_report"]["reuse_by_command"]["can_reuse_for"])
            self.assertTrue(payload["status_report"]["repair_plan"])
            self.assertEqual(
                payload["status_report"]["primary_repair_step"]["id"],
                "qa_missing",
            )
            self.assertEqual(payload["handoff_card"]["primary_task"]["id"], "qa_missing")
            self.assertIn(
                "investigation_brief",
                payload["status_report"]["primary_repair_step"],
            )
            self.assertEqual(
                payload["status_report"]["primary_repair_step"]["investigation_brief"]["issue_id"],
                "qa_missing",
            )

    def test_handoff_json_returns_minimal_machine_interface(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--handoff",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("handoff_card", payload)
            self.assertIn("status_report", payload)
            self.assertNotIn("summary", payload)
            self.assertNotIn("orchestration", payload)
            self.assertEqual(payload["handoff_card"]["primary_task"]["id"], "qa_missing")
            self.assertTrue(payload["status_report"]["handoff_card_validation"]["ok"])

    def test_handoff_text_output_includes_primary_task_command(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--handoff",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("primary task command:", completed.stdout)
            self.assertIn("--from-output", completed.stdout)
            self.assertIn("--ask", completed.stdout)

    def test_status_json_rebuilds_handoff_card_when_saved_handoff_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "handoff_card.json").write_text("{\"schema_version\":\"0.0\"}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--status",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["status_report"]["manifest_integrity"]["ok"])
            self.assertEqual(
                payload["status_report"]["handoff_card_artifact"]["source_used"],
                "rebuilt",
            )
            self.assertFalse(payload["status_report"]["handoff_card_artifact"]["trusted"])
            self.assertTrue(payload["status_report"]["handoff_card_validation"]["ok"])
            self.assertEqual(payload["handoff_card"]["schema_version"], "1.0")
            self.assertIn("primary_task", payload["handoff_card"])

    def test_status_json_rebuilds_handoff_card_when_primary_task_contract_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            handoff_path = result.output_dir / "handoff_card.json"
            handoff = json.loads(handoff_path.read_text(encoding="utf-8"))
            handoff["primary_task"]["recommended_args"] = "not-a-list"
            handoff_path.write_text(json.dumps(handoff, ensure_ascii=False), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--status",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["status_report"]["manifest_integrity"]["ok"])
            self.assertEqual(
                payload["status_report"]["handoff_card_artifact"]["source_used"],
                "rebuilt",
            )
            self.assertFalse(payload["status_report"]["handoff_card_artifact"]["trusted"])
            self.assertTrue(payload["status_report"]["handoff_card_validation"]["ok"])
            self.assertIsInstance(payload["handoff_card"]["primary_task"]["recommended_args"], list)

    def test_status_json_rebuilds_summary_when_saved_summary_is_untrusted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "run_summary.json").write_text("{\"tampered\": true}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--status",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["status_report"]["manifest_integrity"]["ok"])
            self.assertEqual(
                payload["status_report"]["run_summary_artifact"]["source_used"],
                "rebuilt",
            )
            self.assertFalse(payload["status_report"]["run_summary_artifact"]["trusted"])
            self.assertEqual(payload["summary"]["repo"]["name"], "eda_repo")
            self.assertEqual(payload["orchestration"]["recommended_command"], "ask")
            self.assertTrue(payload["status_report"]["repair_plan"])

    def test_run_summary_repair_plan_includes_failing_eval_cases(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "bad_suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "bad",
                        "rag": [
                            {
                                "question": "Where is the entrypoint?",
                                "expected_paths": ["missing/file.py"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    str(Path(tmp) / "out"),
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval-suite",
                    str(suite_path),
                    "--eval-fail-under",
                    "0.9",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            payload = json.loads(completed.stdout)
            repair_issue = next(
                item
                for item in payload["summary"]["orchestration"]["repair_plan"]
                if item["id"] == "evaluation_failed"
            )
            self.assertTrue(repair_issue["failing_rag_cases"])
            failing_case = repair_issue["failing_rag_cases"][0]
            self.assertEqual(failing_case["question"], "Where is the entrypoint?")
            self.assertIn("missing/file.py", failing_case["missing_expected_paths"])
            self.assertIn("missing/file.py", repair_issue["focus_paths"])
            self.assertIn("missing/file.py", repair_issue["suggested_context_files"])
            self.assertIn("--context-file", repair_issue["investigation_command_line"])
            self.assertIn("Where is the entrypoint?", repair_issue["investigation_command_line"])
            self.assertIn("--context-chars", repair_issue["investigation_command_line"])
            self.assertGreaterEqual(repair_issue["suggested_context_chars"], 12000)
            self.assertIn("investigation_brief", repair_issue)
            self.assertEqual(
                repair_issue["investigation_brief"]["question"],
                "Where is the entrypoint?",
            )
            self.assertIn(
                "missing/file.py",
                repair_issue["investigation_brief"]["focus_paths"],
            )
            self.assertTrue(repair_issue["investigation_brief"]["reading_order"])
            self.assertTrue(repair_issue["investigation_brief"]["guardrails"])

    def test_run_summary_repair_plan_prefers_route_question_for_trace_failures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "bad_trace_suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "bad-trace",
                        "trace": [
                            {
                                "route": "/missing",
                                "expected_paths": ["app.py"],
                                "expected_names": ["GET /missing"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    str(Path(tmp) / "out"),
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval-suite",
                    str(suite_path),
                    "--eval-fail-under",
                    "0.9",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            payload = json.loads(completed.stdout)
            repair_issue = next(
                item
                for item in payload["summary"]["orchestration"]["repair_plan"]
                if item["id"] == "evaluation_failed"
            )
            self.assertTrue(repair_issue["failing_trace_cases"])
            self.assertIn("/missing", repair_issue["suggested_question"])
            self.assertIn("/missing", repair_issue["investigation_command_line"])
            self.assertEqual(
                repair_issue["investigation_brief"]["question"],
                "Explain the implementation chain for /missing",
            )
            self.assertIn(
                "app.py",
                repair_issue["investigation_brief"]["reading_order"][0]["paths"],
            )

    def test_run_summary_repair_plan_includes_failing_readiness_checks(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "bad_suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "bad",
                        "rag": [
                            {
                                "question": "Where is the entrypoint?",
                                "expected_paths": ["missing/file.py"],
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    str(Path(tmp) / "out"),
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ready",
                    "--ready-fail-under",
                    "0.9",
                    "--eval-suite",
                    str(suite_path),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            payload = json.loads(completed.stdout)
            repair_issue = next(
                item
                for item in payload["summary"]["orchestration"]["repair_plan"]
                if item["id"] == "readiness_failed"
            )
            self.assertTrue(repair_issue["failing_checks"])
            check_names = {item["name"] for item in repair_issue["failing_checks"]}
            self.assertIn("evaluation", check_names)
            self.assertIn("missing/file.py", repair_issue["focus_paths"])
            self.assertIn("--ask", repair_issue["investigation_command_line"])
            self.assertIn("--context-chars", repair_issue["investigation_command_line"])
            self.assertIn("investigation_brief", repair_issue)
            self.assertTrue(repair_issue["investigation_brief"]["failure_evidence"]["failing_checks"])
            self.assertIn(
                "Clear every error-status readiness check before treating the output as competition-ready.",
                repair_issue["investigation_brief"]["guardrails"],
            )

    def test_run_summary_repair_plan_uses_saved_rag_index_for_large_context_budget(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            lines = [f"def fn_{idx}(): return '{idx}' * 80" for idx in range(400)]
            (root / "large_module.py").write_text("\n".join(lines), encoding="utf-8")
            out = Path(tmp) / "out"
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    str(root),
                    "--out",
                    str(out),
                    "--max-files",
                    "20",
                    "--no-cache",
                    "--ask",
                    "please analyze large_module.py",
                    "--context-chars",
                    "700",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            repair_issue = next(
                item
                for item in payload["summary"]["orchestration"]["repair_plan"]
                if item["id"] == "qa_unsafe"
            )
            qa_payload = payload["qa"]
            self.assertGreater(
                repair_issue["suggested_context_chars"],
                12000,
            )
            self.assertGreaterEqual(
                repair_issue["suggested_context_chars"],
                qa_payload["context_pack"]["target_context_budget_chars"],
            )
            self.assertIn("--context-chars", repair_issue["investigation_command_line"])

    def test_run_summary_repair_plan_falls_back_when_rag_index_is_invalid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "repo"
            root.mkdir()
            lines = [f"def fn_{idx}(): return '{idx}' * 80" for idx in range(120)]
            (root / "large_module.py").write_text("\n".join(lines), encoding="utf-8")
            out = Path(tmp) / "out"
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    str(root),
                    "--out",
                    str(out),
                    "--max-files",
                    "20",
                    "--no-cache",
                    "--ask",
                    "please analyze large_module.py",
                    "--context-chars",
                    "700",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            rag_index_path = Path(payload["outputs"]["rag_index"])
            rag_index_path.write_text("{bad json\n", encoding="utf-8")
            status_completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(rag_index_path.parent),
                    "--status",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(status_completed.returncode, 2, status_completed.stderr)
            status_payload = json.loads(status_completed.stdout)
            repair_issue = next(
                item
                for item in status_payload["summary"]["orchestration"]["repair_plan"]
                if item["id"] == "qa_unsafe"
            )
            self.assertEqual(repair_issue["suggested_context_chars"], 12000)

    def test_impact_json_output_is_machine_readable_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--impact",
                    "--impact-file",
                    "services/user_service.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("impact", payload)
            self.assertEqual(payload["impact"]["source"], "explicit")
            self.assertEqual(payload["impact"]["input_paths"], ["services/user_service.py"])
            affected_files = set(payload["impact"]["affected_files"])
            self.assertIn("services/user_service.py", affected_files)
            self.assertIn("app.py", affected_files)
            affected_names = {node["name"] for node in payload["impact"]["nodes"]}
            self.assertIn("UserService", affected_names)
            users = next(node for node in payload["impact"]["nodes"] if node["name"] == "POST /users")
            self.assertEqual(users["line"], 14)
            impact_path = Path(payload["outputs"]["impact"])
            self.assertTrue(impact_path.exists())

    def test_cli_include_exclude_scope_changes_scan_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--include",
                    "*.py",
                    "--exclude",
                    "app.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["stats"]["file_count"], 2)

    def test_ask_from_output_reuses_saved_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "项目入口在哪里",
                    "--top-k",
                    "2",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["repo"], "eda_repo")
            self.assertIn("qa", payload)
            excerpts = "\n".join(
                line
                for result in payload["qa"]["results"]
                for line in result["source_excerpt"]
            )
            self.assertIn("class StandaloneEntrypoint", excerpts)
            qa_artifact = json.loads((result.output_dir / "qa_answer.json").read_text(encoding="utf-8"))
            manifest = json.loads((result.output_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["run"]["max_files"], 100)
            self.assertFalse(manifest["run"]["use_cache"])
            self.assertEqual(manifest["run"]["post_run"]["ask"], qa_artifact["question"])
            self.assertEqual(manifest["run"]["post_run"]["top_k"], 2)
            self.assertEqual(manifest["run"]["post_run"]["from_output"], str(result.output_dir))

    def test_from_output_missing_knowledge_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_dir = Path(tmp) / "broken_output"
            output_dir.mkdir()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Required artifact is missing", completed.stderr)
            self.assertIn("knowledge.json", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_corrupt_saved_rag_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "rag_index.json").write_text("{not-json", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Manifest integrity check failed", completed.stderr)
            self.assertIn("rag_index.json", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_missing_saved_rag_has_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "rag_index.json").unlink()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Manifest integrity check failed", completed.stderr)
            self.assertIn("rag_index.json", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_rejects_stale_manifest_artifact_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "report.md").write_text("tampered report\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Manifest integrity check failed", completed.stderr)
            self.assertIn("report.md", completed.stderr)
            self.assertIn("hash mismatches", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_rejects_tampered_run_summary_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            (result.output_dir / "run_summary.json").write_text("{\"tampered\": true}\n", encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Manifest integrity check failed", completed.stderr)
            self.assertIn("run_summary.json", completed.stderr)
            self.assertIn("hash mismatches", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_rejects_missing_saved_qa_artifact_when_present_in_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            ask_completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(ask_completed.returncode, 0, ask_completed.stderr)
            (result.output_dir / "qa_answer.json").unlink()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--impact",
                    "--impact-file",
                    "src/main_entrypoint.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Artifact contract check failed", completed.stderr)
            self.assertIn("context_pack.md requires qa_answer.json", completed.stderr)
            self.assertIn("llm_prompt.md requires qa_answer.json", completed.stderr)
            self.assertIn("Suggested recovery commands:", completed.stderr)
            self.assertIn("--ask", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_ask_rejects_present_context_pack_without_qa_root(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            ask_completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(ask_completed.returncode, 0, ask_completed.stderr)
            (result.output_dir / "qa_answer.json").unlink()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertIn("Artifact contract check failed", completed.stderr)
            self.assertIn("context_pack.md requires qa_answer.json", completed.stderr)
            self.assertIn("Blocked commands:", completed.stderr)
            self.assertIn("- ask:", completed.stderr)
            self.assertIn("--ask", completed.stderr)
            self.assertNotIn("Traceback", completed.stderr)

    def test_from_output_impact_ignores_missing_qa_contracts_when_not_needed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            ask_completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ask",
                    "POST /users call chain",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(ask_completed.returncode, 0, ask_completed.stderr)
            (result.output_dir / "qa_answer.json").unlink()
            (result.output_dir / "context_pack.md").unlink()
            (result.output_dir / "llm_prompt.md").unlink()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--impact",
                    "--impact-file",
                    "services/user_service.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("impact", payload)
            self.assertEqual(payload["impact"]["input_paths"], ["services/user_service.py"])

    def test_from_output_ready_reuses_without_saved_evaluation_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "--from-output",
                    str(result.output_dir),
                    "--ready",
                    "--ready-fail-under",
                    "1.0",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["readiness"]["passed"])
            self.assertTrue(Path(payload["outputs"]["evaluation"]).exists())
            summary = json.loads(Path(payload["outputs"]["run_summary"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["orchestration"]["recommended_command"], "ask")
            self.assertIn("ready", summary["orchestration"]["can_reuse_for"])

    def test_run_summary_reports_missing_dependency_contract_for_present_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            qa_path = Path(payload["outputs"]["qa_answer"])
            qa_path.unlink()
            summary_path = Path(payload["outputs"]["run_summary"])
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            self.assertTrue(summary["artifact_contracts"]["reusable_ready"])
            from aegis.summary import write_run_summary
            result = load_analysis_result(Path(payload["outputs"]["output_dir"]))
            refreshed = write_run_summary(result)
            self.assertFalse(refreshed["artifact_contracts"]["reusable_ready"])
            self.assertEqual(
                refreshed["artifact_contracts"]["present_artifacts_with_missing_dependencies"]["context_pack.md"],
                ["qa_answer.json"],
            )
            self.assertEqual(
                refreshed["artifact_contracts"]["items"]["context_pack.md"]["missing_required_dependencies"],
                ["qa_answer.json"],
            )
            self.assertIn("ask", refreshed["orchestration"]["blocked_by"])
            self.assertFalse(refreshed["orchestration"]["requires_fresh_analysis"])
            self.assertTrue(refreshed["orchestration"]["recovery_commands"])
            self.assertIn("--ask", refreshed["orchestration"]["recovery_commands"][0])
            self.assertIn("Reuse is currently blocked", refreshed["orchestration"]["why_recommended"])

    def test_skill_wrapper_ask_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(EDA_SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "ask",
                    "项目入口在哪里",
                    "--from-output",
                    str(result.output_dir),
                    "--top-k",
                    "2",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["repo"], "eda_repo")

    def test_skill_wrapper_impact_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "impact",
                    "--from-output",
                    str(result.output_dir),
                    "--path",
                    "services/user_service.py",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("impact", payload)
            self.assertIn("app.py", set(payload["impact"]["affected_files"]))
            users = next(node for node in payload["impact"]["nodes"] if node["name"] == "POST /users")
            self.assertEqual(users["line"], 14)

    def test_skill_wrapper_status_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "status",
                    "--from-output",
                    str(result.output_dir),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["summary"]["repo"]["name"], "sample_repo")
            self.assertEqual(payload["orchestration"]["recommended_command"], "ask")

    def test_skill_wrapper_handoff_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "handoff",
                    "--from-output",
                    str(result.output_dir),
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["handoff_card"]["repo"]["name"], "sample_repo")
            self.assertTrue(payload["status_report"]["handoff_card_validation"]["ok"])
            primary_task = payload["handoff_card"]["primary_task"]
            self.assertEqual(primary_task["recommended_command"], "ask")
            self.assertIn("--from-output", primary_task["recommended_command_line"])
            self.assertIn("--ask", primary_task["recommended_command_line"])
            self.assertTrue(primary_task["recommended_args"])

    def test_eval_json_output_is_machine_readable_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("evaluation", payload)
            self.assertGreaterEqual(payload["evaluation"]["metrics"]["rag_recall"], 0.75)
            evaluation_path = Path(payload["outputs"]["evaluation"])
            self.assertTrue(evaluation_path.exists())

    def test_eval_text_output_includes_prompt_context_gates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval",
                    "--eval-fail-under",
                    "1.0",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            self.assertIn("prompt context coverage", completed.stdout)
            self.assertIn("prompt expected-path coverage", completed.stdout)
            self.assertIn("complete-file context coverage", completed.stdout)
            self.assertIn("complete-file expected-path coverage", completed.stdout)

    def test_ready_json_output_is_machine_readable_and_written(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/sample_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ready",
                    "--ready-fail-under",
                    "1.0",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertIn("readiness", payload)
            self.assertTrue(payload["readiness"]["passed"])
            check_names = {check["name"] for check in payload["readiness"]["checks"]}
            self.assertGreaterEqual(
                check_names,
                {"doctor", "artifacts", "manifest", "knowledge", "codegraph", "rag", "evaluation"},
            )
            evaluation_check = next(
                check for check in payload["readiness"]["checks"] if check["name"] == "evaluation"
            )
            self.assertEqual(evaluation_check["detail"]["prompt_context_coverage"], 1.0)
            self.assertEqual(evaluation_check["detail"]["prompt_context_expected_path_coverage"], 1.0)
            self.assertEqual(evaluation_check["detail"]["complete_file_context_coverage"], 1.0)
            self.assertEqual(evaluation_check["detail"]["complete_file_expected_path_coverage"], 1.0)
            self.assertEqual(payload["readiness"]["threshold"], 1.0)
            self.assertTrue(Path(payload["outputs"]["readiness"]).exists())
            self.assertTrue(Path(payload["outputs"]["manifest"]).exists())
            self.assertTrue(Path(payload["outputs"]["run_summary"]).exists())
            summary = json.loads(Path(payload["outputs"]["run_summary"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "ready")
            self.assertTrue(summary["evaluation"]["available"])
            self.assertTrue(summary["readiness"]["passed"])

    def test_ready_ask_verifies_qa_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--ready",
                    "--ready-fail-under",
                    "1.0",
                    "--ready-ask",
                    "Where is the entrypoint?",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["readiness"]["passed"])
            qa_check = next(check for check in payload["readiness"]["checks"] if check["name"] == "qa")
            self.assertEqual(qa_check["status"], "ok")
            self.assertTrue(qa_check["detail"]["required_context_satisfied"])
            self.assertTrue(qa_check["detail"]["target_context_satisfied"])
            self.assertTrue(Path(payload["outputs"]["qa_answer"]).exists())
            self.assertTrue(Path(payload["outputs"]["context_pack"]).exists())
            self.assertTrue(Path(payload["outputs"]["llm_prompt"]).exists())
            self.assertTrue(Path(payload["outputs"]["run_summary"]).exists())
            summary = json.loads(Path(payload["outputs"]["run_summary"]).read_text(encoding="utf-8"))
            self.assertEqual(summary["status"], "ready")
            self.assertTrue(summary["qa"]["available"])
            self.assertTrue(summary["qa"]["context_safe_for_llm"])
            self.assertTrue(summary["qa"]["complete_file_paths"])
            self.assertEqual(summary["next_actions"], ["Ready for demo or downstream agent consumption."])
            self.assertEqual(summary["orchestration"]["recommended_command"], "none")
            self.assertEqual(summary["orchestration"]["recommended_args"], [])
            self.assertEqual(summary["orchestration"]["recommended_command_line"], "")
            self.assertEqual(summary["orchestration"]["recovery_commands"], [])
            self.assertFalse(summary["orchestration"]["repair_ready"])
            self.assertEqual(summary["orchestration"]["blocking_issues_count"], 0)
            self.assertEqual(summary["orchestration"]["repair_plan"], [])
            self.assertIsNone(summary["orchestration"]["primary_repair_step"])
            self.assertEqual(
                summary["orchestration"]["why_recommended"],
                "All tracked QA, evaluation, and readiness checks are satisfied.",
            )

    def test_skill_wrapper_ready_from_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            result = AegisWorkflow(SAMPLE, output_root=Path(tmp), max_files=100, use_cache=False).run()
            completed = subprocess.run(
                [
                    sys.executable,
                    "skills/aegis-repo-analyst/scripts/run_aegis.py",
                    "ready",
                    "--from-output",
                    str(result.output_dir),
                    "--fail-under",
                    "1.0",
                    "--ask",
                    "POST /users call chain",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["readiness"]["passed"])
            qa_check = next(check for check in payload["readiness"]["checks"] if check["name"] == "qa")
            self.assertEqual(qa_check["status"], "ok")

    def test_eval_quality_gate_passes_when_score_meets_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    tmp,
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval",
                    "--eval-fail-under",
                    "1.0",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertTrue(payload["quality_gate"]["passed"])

    def test_eval_quality_gate_fails_when_score_is_below_threshold(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            suite_path = Path(tmp) / "bad_suite.json"
            suite_path.write_text(
                json.dumps(
                    {
                        "name": "bad",
                        "rag": [
                            {
                                "question": "项目入口在哪里",
                                "expected_paths": ["missing/file.py"],
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    "main.py",
                    "examples/eda_repo",
                    "--out",
                    str(Path(tmp) / "out"),
                    "--max-files",
                    "100",
                    "--no-cache",
                    "--eval-suite",
                    str(suite_path),
                    "--eval-fail-under",
                    "0.9",
                    "--json",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
            self.assertEqual(completed.returncode, 2, completed.stdout)
            payload = json.loads(completed.stdout)
            self.assertFalse(payload["quality_gate"]["passed"])
            self.assertEqual(payload["quality_gate"]["threshold"], 0.9)


if __name__ == "__main__":
    unittest.main()
