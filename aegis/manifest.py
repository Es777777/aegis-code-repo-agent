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
TRACKED_ARTIFACTS = [
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
    "run_summary.json",
    "handoff_card.json",
]
ARTIFACT_CONTRACTS: dict[str, dict[str, Any]] = {
    "knowledge.json": {
        "produced_by": ["analyze"],
        "depends_on": [],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Structured repository knowledge produced by the analysis scan.",
    },
    "findings.json": {
        "produced_by": ["analyze"],
        "depends_on": ["knowledge.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Specialist-agent findings derived from repository knowledge.",
    },
    "rag_index.json": {
        "produced_by": ["analyze"],
        "depends_on": ["knowledge.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Stable RAG index built from repository knowledge.",
    },
    "events.json": {
        "produced_by": ["analyze"],
        "depends_on": [],
        "optional_depends_on": [],
        "reusable": False,
        "description": "Workflow event log for the current analysis run.",
    },
    "report.md": {
        "produced_by": ["analyze"],
        "depends_on": ["knowledge.json", "findings.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Evidence-first Markdown report.",
    },
    "report.html": {
        "produced_by": ["analyze"],
        "depends_on": ["knowledge.json", "findings.json", "report.md"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Browser-readable HTML report.",
    },
    "architecture.mmd": {
        "produced_by": ["analyze"],
        "depends_on": ["knowledge.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Mermaid architecture diagram derived from repository knowledge.",
    },
    "evaluation.json": {
        "produced_by": ["eval", "ready"],
        "depends_on": ["knowledge.json", "rag_index.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "RAG and CodeGraph evaluation results.",
    },
    "impact.json": {
        "produced_by": ["impact"],
        "depends_on": ["knowledge.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "CodeGraph impact analysis for changed files.",
    },
    "readiness.json": {
        "produced_by": ["ready"],
        "depends_on": ["knowledge.json", "rag_index.json", "evaluation.json"],
        "optional_depends_on": ["qa_answer.json", "context_pack.md", "llm_prompt.md"],
        "reusable": True,
        "description": "Competition readiness verdict and checks.",
    },
    "qa_answer.json": {
        "produced_by": ["ask", "ready-ask"],
        "depends_on": ["knowledge.json", "rag_index.json"],
        "optional_depends_on": [],
        "reusable": True,
        "description": "Stable QA payload with prompt-safe context metadata.",
    },
    "context_pack.md": {
        "produced_by": ["ask", "ready-ask"],
        "depends_on": ["qa_answer.json"],
        "optional_depends_on": ["knowledge.json", "rag_index.json"],
        "reusable": True,
        "description": "Human-readable prompt context pack rendered from QA payload.",
    },
    "llm_prompt.md": {
        "produced_by": ["ask", "ready-ask"],
        "depends_on": ["qa_answer.json"],
        "optional_depends_on": ["knowledge.json", "rag_index.json"],
        "reusable": True,
        "description": "Exact LLM prompt rendered from QA payload.",
    },
    "run_summary.json": {
        "produced_by": ["analyze", "ask", "eval", "impact", "ready", "ready-ask"],
        "depends_on": ["knowledge.json", "manifest.json"],
        "optional_depends_on": [
            "qa_answer.json",
            "context_pack.md",
            "llm_prompt.md",
            "evaluation.json",
            "impact.json",
            "readiness.json",
        ],
        "reusable": True,
        "description": "Compact downstream handoff summary across all outputs.",
    },
    "handoff_card.json": {
        "produced_by": ["analyze", "ask", "eval", "impact", "ready", "ready-ask"],
        "depends_on": ["knowledge.json", "run_summary.json"],
        "optional_depends_on": [
            "qa_answer.json",
            "context_pack.md",
            "llm_prompt.md",
            "evaluation.json",
            "impact.json",
            "readiness.json",
        ],
        "reusable": True,
        "description": "Unified downstream agent task card with primary action, evidence, and reusable artifact pointers.",
    },
}
COMMAND_ARTIFACT_CONTRACTS: dict[str, dict[str, Any]] = {
    "trace": {
        "required_roots": ["knowledge.json"],
        "related_artifacts": ["run_summary.json", "handoff_card.json"],
    },
    "impact": {
        "required_roots": ["knowledge.json"],
        "related_artifacts": ["run_summary.json", "handoff_card.json", "impact.json"],
    },
    "ask": {
        "required_roots": ["knowledge.json", "rag_index.json"],
        "related_artifacts": ["run_summary.json", "handoff_card.json", "qa_answer.json", "context_pack.md", "llm_prompt.md"],
    },
    "eval": {
        "required_roots": ["knowledge.json", "rag_index.json"],
        "related_artifacts": ["run_summary.json", "handoff_card.json", "evaluation.json"],
    },
    "ready": {
        "required_roots": ["knowledge.json", "rag_index.json"],
        "related_artifacts": ["run_summary.json", "handoff_card.json", "readiness.json", "qa_answer.json", "context_pack.md", "llm_prompt.md"],
    },
}


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
    artifacts: dict[str, Any] = {}
    for name in TRACKED_ARTIFACTS:
        path = output_dir / name
        contract = ARTIFACT_CONTRACTS.get(name, {})
        artifacts[name] = {
            "path": str(path),
            "exists": path.exists(),
            "size": path.stat().st_size if path.exists() else 0,
            "sha256": file_sha256(path) if path.exists() else None,
            "produced_by": list(contract.get("produced_by", [])),
            "depends_on": list(contract.get("depends_on", [])),
            "optional_depends_on": list(contract.get("optional_depends_on", [])),
            "reusable": bool(contract.get("reusable", False)),
            "description": contract.get("description", ""),
        }
    return artifacts


def verify_manifest_integrity(
    output_dir: Path,
    *,
    repo_name: str | None = None,
    required_artifacts: list[str] | None = None,
    validate_present_tracked: bool = False,
) -> dict[str, Any]:
    manifest_path = output_dir / "manifest.json"
    required = list(dict.fromkeys(required_artifacts or REQUIRED_ANALYSIS_ARTIFACTS))
    detail: dict[str, Any] = {
        "path": str(manifest_path),
        "schema_version": None,
        "repo": None,
        "generated_at": None,
        "validated_artifacts": [],
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
    if validate_present_tracked:
        required = list(
            dict.fromkeys(
                [
                    *required,
                    *_present_tracked_artifacts(output_dir, artifacts),
                ]
            )
        )
    detail["validated_artifacts"] = required

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


def verify_artifact_contracts(
    output_dir: Path,
    *,
    required_roots: list[str],
    related_artifacts: list[str] | None = None,
) -> dict[str, Any]:
    root_names = list(dict.fromkeys(required_roots))
    related_names = list(dict.fromkeys(related_artifacts or []))
    detail: dict[str, Any] = {
        "required_roots": root_names,
        "related_artifacts": related_names,
        "validated_artifacts": [],
        "missing_root_artifacts": [],
        "missing_contract_metadata": [],
        "dependency_failures": {},
        "optional_dependency_gaps": {},
    }
    validate_names = list(
        dict.fromkeys(
            [
                *root_names,
                *[
                    name
                    for name in TRACKED_ARTIFACTS
                    if (output_dir / name).exists()
                ],
                *[
                    name
                    for name in related_names
                    if (output_dir / name).exists()
                ],
            ]
        )
    )
    detail["validated_artifacts"] = validate_names
    for name in validate_names:
        contract = ARTIFACT_CONTRACTS.get(name)
        if not contract:
            detail["missing_contract_metadata"].append(name)
            continue
        path = output_dir / name
        exists = path.exists()
        if name in root_names and not exists:
            detail["missing_root_artifacts"].append(name)
            continue
        if not exists:
            continue
        required = list(contract.get("depends_on", []))
        optional = list(contract.get("optional_depends_on", []))
        missing_required = [dep for dep in required if not (output_dir / dep).exists()]
        missing_optional = [dep for dep in optional if not (output_dir / dep).exists()]
        if missing_required:
            detail["dependency_failures"][name] = missing_required
        if missing_optional:
            detail["optional_dependency_gaps"][name] = missing_optional
    ok = (
        not detail["missing_root_artifacts"]
        and not detail["missing_contract_metadata"]
        and not detail["dependency_failures"]
    )
    return {"ok": ok, "detail": detail}


def contract_targets_for_command(command: str) -> dict[str, list[str]]:
    profile = COMMAND_ARTIFACT_CONTRACTS.get(command, {})
    required_roots = list(dict.fromkeys(profile.get("required_roots", [])))
    related_artifacts = [
        name
        for name in dict.fromkeys(profile.get("related_artifacts", []))
        if name in TRACKED_ARTIFACTS
    ]
    return {
        "required_roots": required_roots,
        "related_artifacts": related_artifacts,
    }


def reuse_readiness_by_command(
    output_dir: Path,
    *,
    commands: list[str] | None = None,
) -> dict[str, Any]:
    command_list = list(dict.fromkeys(commands or list(COMMAND_ARTIFACT_CONTRACTS)))
    reusable: list[str] = []
    blocked: dict[str, str] = {}
    details: dict[str, Any] = {}
    for command in command_list:
        targets = contract_targets_for_command(command)
        check = verify_artifact_contracts(
            output_dir,
            required_roots=targets["required_roots"],
            related_artifacts=targets["related_artifacts"],
        )
        details[command] = check["detail"]
        if check["ok"]:
            reusable.append(command)
        else:
            blocked[command] = format_artifact_contract_errors(check)
    return {
        "can_reuse_for": reusable,
        "blocked_by": blocked,
        "details": details,
    }


def _present_tracked_artifacts(output_dir: Path, artifacts: Any) -> list[str]:
    present: list[str] = []
    for name in TRACKED_ARTIFACTS:
        path = output_dir / name
        if path.exists():
            present.append(name)
    return list(dict.fromkeys(present))


def format_artifact_contract_errors(check: dict[str, Any]) -> str:
    detail = check.get("detail", {})
    pieces: list[str] = []
    missing_roots = detail.get("missing_root_artifacts") or []
    if missing_roots:
        pieces.append(f"missing required roots: {', '.join(missing_roots)}")
    missing_contract_metadata = detail.get("missing_contract_metadata") or []
    if missing_contract_metadata:
        pieces.append(f"missing contract metadata: {', '.join(missing_contract_metadata)}")
    dependency_failures = detail.get("dependency_failures") or {}
    if isinstance(dependency_failures, dict):
        for artifact, missing in dependency_failures.items():
            pieces.append(f"{artifact} requires {', '.join(missing)}")
    return "; ".join(pieces) or "unknown artifact contract error"


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
