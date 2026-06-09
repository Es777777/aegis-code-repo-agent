from __future__ import annotations

import json
import tempfile
import unittest
import os
from pathlib import Path

from aegis.config import AegisConfig, load_env_file
from aegis.knowledge.codegraph import CodeGraphQuery
from aegis.knowledge.indexer import KnowledgeBuilder
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.rag.index import RAGIndexBuilder
from aegis.rag.qa import RepositoryQAAgent
from aegis.rag.retriever import RAGRetriever


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_repo"


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


if __name__ == "__main__":
    unittest.main()
