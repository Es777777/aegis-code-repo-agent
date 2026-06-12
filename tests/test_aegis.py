from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
import os
from pathlib import Path

from aegis.config import AegisConfig, load_env_file
from aegis.evaluation import Evaluator, builtin_suite, load_suite
from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.knowledge.indexer import KnowledgeBuilder
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGIndexBuilder
from aegis.rag.qa import RepositoryQAAgent
from aegis.rag.retriever import RAGRetriever


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
        self.assertIn("离线 RAG", answer.answer)
        self.assertTrue(answer.results)


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
            self.assertGreater(second.knowledge.stats.get("cache_hits", 0), 0)
            data = json.loads((second.output_dir / "knowledge.json").read_text(encoding="utf-8"))
            self.assertIn("call_graph", data)
            self.assertIn("rag", data["stats"])


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
        context = RAGRetriever(index).context("项目入口在哪里", top_k=4, max_chars=6000)
        self.assertIn("kind=source", context)
        self.assertIn("Code:", context)
        self.assertIn("class MainEntrypoint", context)

    def test_offline_qa_prints_source_context(self) -> None:
        knowledge = KnowledgeBuilder(EDA_SAMPLE, max_files=100, use_cache=False).build()
        index = RAGIndexBuilder(knowledge).build()
        answer = RepositoryQAAgent(knowledge, index).answer("布局器处理普通单元还是硬宏", top_k=4)
        self.assertIn("源码上下文", answer.answer)
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
            self.assertFalse(payload["qa"]["used_llm"])
            excerpts = "\n".join(
                line
                for result in payload["qa"]["results"]
                for line in result["source_excerpt"]
            )
            self.assertIn("class StandaloneEntrypoint", excerpts)

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
