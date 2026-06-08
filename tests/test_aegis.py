from __future__ import annotations

import json
import tempfile
import unittest
import os
from pathlib import Path

from aegis.config import AegisConfig, load_env_file
from aegis.knowledge.indexer import KnowledgeBuilder
from aegis.orchestrator.workflow import AegisWorkflow


ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_repo"


class KnowledgeBuilderTest(unittest.TestCase):
    def test_sample_repo_interfaces_and_framework(self) -> None:
        knowledge = KnowledgeBuilder(SAMPLE, max_files=100, use_cache=False).build()
        self.assertIn("FastAPI", knowledge.frameworks)
        self.assertEqual(knowledge.interface_catalog["app.py"], ["GET /health", "POST /users"])
        self.assertIn("services/user_service.py", knowledge.dependency_graph["app.py"])
        self.assertIn("repositories/user_repository.py", knowledge.dependency_graph["services/user_service.py"])


class WorkflowTest(unittest.TestCase):
    def test_workflow_writes_outputs_and_uses_cache(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "aegis"
            first = AegisWorkflow(SAMPLE, output_root=out, max_files=100).run()
            second = AegisWorkflow(SAMPLE, output_root=out, max_files=100).run()
            self.assertTrue((first.output_dir / "report.md").exists())
            self.assertTrue((first.output_dir / "report.html").exists())
            self.assertTrue((first.output_dir / "architecture.mmd").exists())
            self.assertGreater(second.knowledge.stats.get("cache_hits", 0), 0)
            data = json.loads((second.output_dir / "knowledge.json").read_text(encoding="utf-8"))
            self.assertIn("call_graph", data)


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
