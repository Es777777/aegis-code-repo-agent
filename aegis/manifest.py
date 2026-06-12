from __future__ import annotations

from datetime import datetime
from pathlib import Path
import subprocess
from typing import Any

from aegis import __version__
from aegis.models import AnalysisResult


MANIFEST_SCHEMA_VERSION = "1.0"


def build_manifest(
    result: AnalysisResult,
    *,
    max_files: int,
    include: list[str],
    exclude: list[str],
    use_cache: bool,
    llm_enabled: bool,
    events_count: int,
) -> dict[str, Any]:
    knowledge = result.knowledge
    output_dir = result.output_dir
    artifacts = _artifact_inventory(output_dir)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "aegis_version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": {
            "name": knowledge.repo_name,
            "root": knowledge.root,
            "git": _git_info(Path(knowledge.root)),
        },
        "run": {
            "max_files": max_files,
            "include": include,
            "exclude": exclude,
            "use_cache": use_cache,
            "llm_enabled": llm_enabled,
            "events_count": events_count,
        },
        "stats": {
            "file_count": knowledge.stats.get("file_count", 0),
            "total_lines": knowledge.stats.get("total_lines", 0),
            "languages": knowledge.stats.get("languages", {}),
            "scan": knowledge.stats.get("scan", {}),
            "code_graph": knowledge.code_graph.stats,
            "rag": knowledge.stats.get("rag", {}),
            "findings_count": len(result.findings),
        },
        "artifacts": artifacts,
    }


def _artifact_inventory(output_dir: Path) -> dict[str, Any]:
    names = [
        "knowledge.json",
        "findings.json",
        "rag_index.json",
        "events.json",
        "report.md",
        "report.html",
        "architecture.mmd",
        "evaluation.json",
        "impact.json",
        "readiness.json",
        "qa_answer.json",
        "context_pack.md",
    ]
    artifacts: dict[str, Any] = {}
    for name in names:
        path = output_dir / name
        artifacts[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
        }
    return artifacts


def _git_info(root: Path) -> dict[str, Any]:
    if not (root / ".git").exists():
        return {"is_git_repo": False}
    return {
        "is_git_repo": True,
        "head": _git(root, "rev-parse", "HEAD"),
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD"),
        "dirty": bool(_git(root, "status", "--porcelain")),
    }


def _git(root: Path, *args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout.strip()
