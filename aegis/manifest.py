from __future__ import annotations

from datetime import datetime
import json
from pathlib import Path
import subprocess
from typing import Any

from aegis import __version__
from aegis.models import AnalysisResult
from aegis.utils import file_sha256


MANIFEST_SCHEMA_VERSION = "1.1"
REQUIRED_ANALYSIS_ARTIFACTS = [
    "knowledge.json",
    "findings.json",
    "rag_index.json",
    "report.md",
    "report.html",
    "architecture.mmd",
    "manifest.json",
]


def build_manifest(
    result: AnalysisResult,
    *,
    max_files: int,
    include: list[str],
    exclude: list[str],
    use_cache: bool,
    llm_enabled: bool,
    events_count: int,
    post_run: dict[str, Any] | None = None,
) -> dict[str, Any]:
    knowledge = result.knowledge
    output_dir = result.output_dir
    artifacts = _artifact_inventory(output_dir)
    run = {
        "max_files": max_files,
        "include": include,
        "exclude": exclude,
        "use_cache": use_cache,
        "llm_enabled": llm_enabled,
        "events_count": events_count,
    }
    if post_run:
        run["post_run"] = post_run
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "aegis_version": __version__,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repo": {
            "name": knowledge.repo_name,
            "root": knowledge.root,
            "git": _git_info(Path(knowledge.root)),
        },
        "run": run,
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
        "llm_prompt.md",
    ]
    artifacts: dict[str, Any] = {}
    for name in names:
        path = output_dir / name
        artifacts[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "sha256": file_sha256(path) if path.exists() else None,
        }
    return artifacts


def verify_manifest_integrity(
    output_dir: Path,
    *,
    repo_name: str | None = None,
    required_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    required = required_artifacts or REQUIRED_ANALYSIS_ARTIFACTS
    detail: dict[str, Any] = {
        "path": str(manifest_path),
        "schema_version": None,
        "repo": None,
        "generated_at": None,
        "missing_inventory": [],
        "missing_files": [],
        "size_mismatches": [],
        "missing_hashes": [],
        "hash_mismatches": [],
        "repo_mismatch": False,
        "error": None,
    }
    if not manifest_path.exists():
        detail["error"] = "manifest.json is missing"
        return {"ok": False, "detail": detail}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        detail["error"] = f"manifest.json is not readable JSON: {exc}"
        return {"ok": False, "detail": detail}

    repo = manifest.get("repo", {})
    artifacts = manifest.get("artifacts", {})
    detail["schema_version"] = manifest.get("schema_version")
    detail["repo"] = repo.get("name") if isinstance(repo, dict) else None
    detail["generated_at"] = manifest.get("generated_at")
    if repo_name and detail["repo"] != repo_name:
        detail["repo_mismatch"] = True

    if not isinstance(artifacts, dict):
        detail["missing_inventory"] = [name for name in required if name != "manifest.json"]
    else:
        for name in required:
            if name == "manifest.json":
                continue
            entry = artifacts.get(name)
            if not isinstance(entry, dict):
                detail["missing_inventory"].append(name)
                continue
            artifact_path = output_dir / name
            if not artifact_path.exists():
                detail["missing_files"].append(name)
                continue
            expected_size = entry.get("size")
            actual_size = artifact_path.stat().st_size
            if expected_size != actual_size:
                detail["size_mismatches"].append(name)
            expected_hash = entry.get("sha256")
            if not expected_hash:
                detail["missing_hashes"].append(name)
            elif expected_hash != file_sha256(artifact_path):
                detail["hash_mismatches"].append(name)

    ok = (
        detail["schema_version"] == MANIFEST_SCHEMA_VERSION
        and not detail["repo_mismatch"]
        and not detail["error"]
        and not detail["missing_inventory"]
        and not detail["missing_files"]
        and not detail["size_mismatches"]
        and not detail["missing_hashes"]
        and not detail["hash_mismatches"]
    )
    return {"ok": ok, "detail": detail}


def format_manifest_integrity_errors(check: dict[str, Any]) -> str:
    detail = check.get("detail", {})
    pieces: list[str] = []
    if detail.get("error"):
        pieces.append(str(detail["error"]))
    if detail.get("schema_version") != MANIFEST_SCHEMA_VERSION:
        pieces.append(
            f"schema_version={detail.get('schema_version')!r}, expected {MANIFEST_SCHEMA_VERSION!r}"
        )
    if detail.get("repo_mismatch"):
        pieces.append(f"manifest repo {detail.get('repo')!r} does not match loaded repository")
    for key, label in [
        ("missing_inventory", "missing inventory"),
        ("missing_files", "missing files"),
        ("size_mismatches", "size mismatches"),
        ("missing_hashes", "missing hashes"),
        ("hash_mismatches", "hash mismatches"),
    ]:
        values = detail.get(key) or []
        if values:
            pieces.append(f"{label}: {', '.join(values)}")
    return "; ".join(pieces) or "unknown manifest integrity error"


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
