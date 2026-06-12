from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import shutil
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse

from aegis.config import LLMConfig


@dataclass
class DoctorCheck:
    name: str
    status: str
    message: str
    detail: dict[str, Any] | None = None


class Doctor:
    def __init__(
        self,
        *,
        repo: Path | None,
        output_root: Path,
        llm_config: LLMConfig,
    ) -> None:
        self.repo = repo
        self.output_root = output_root
        self.llm_config = llm_config

    def run(self) -> dict[str, Any]:
        checks = [
            self._python_check(),
            self._repo_check(),
            self._output_check(),
            self._git_check(),
            self._llm_check(),
        ]
        errors = sum(1 for check in checks if check.status == "error")
        warnings = sum(1 for check in checks if check.status == "warning")
        return {
            "passed": errors == 0,
            "errors": errors,
            "warnings": warnings,
            "checks": [asdict(check) for check in checks],
        }

    def _python_check(self) -> DoctorCheck:
        version = sys.version_info
        ok = version >= (3, 11)
        return DoctorCheck(
            name="python",
            status="ok" if ok else "error",
            message=(
                f"Python {version.major}.{version.minor}.{version.micro} is supported"
                if ok
                else "AEGIS requires Python 3.11+"
            ),
            detail={"version": sys.version.split()[0], "required": ">=3.11"},
        )

    def _repo_check(self) -> DoctorCheck:
        if self.repo is None:
            return DoctorCheck(
                name="repo",
                status="error",
                message="No repository path configured. Pass <repo-path> or set AEGIS_REPO_PATH.",
            )
        if not self.repo.exists():
            return DoctorCheck(
                name="repo",
                status="error",
                message=f"Repository path does not exist: {self.repo}",
                detail={"path": str(self.repo)},
            )
        if not self.repo.is_dir():
            return DoctorCheck(
                name="repo",
                status="error",
                message=f"Repository path is not a directory: {self.repo}",
                detail={"path": str(self.repo)},
            )
        return DoctorCheck(
            name="repo",
            status="ok",
            message=f"Repository path is readable: {self.repo}",
            detail={"path": str(self.repo.resolve())},
        )

    def _output_check(self) -> DoctorCheck:
        try:
            self.output_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=self.output_root, delete=True):
                pass
        except OSError as exc:
            return DoctorCheck(
                name="output",
                status="error",
                message=f"Output directory is not writable: {self.output_root}",
                detail={"path": str(self.output_root), "error": str(exc)},
            )
        return DoctorCheck(
            name="output",
            status="ok",
            message=f"Output directory is writable: {self.output_root}",
            detail={"path": str(self.output_root.resolve())},
        )

    def _git_check(self) -> DoctorCheck:
        git = shutil.which("git")
        if not git:
            return DoctorCheck(
                name="git",
                status="warning",
                message="git was not found. AEGIS can still analyze files, but changed-file detection will be disabled.",
            )
        return DoctorCheck(
            name="git",
            status="ok",
            message="git is available",
            detail={"path": git},
        )

    def _llm_check(self) -> DoctorCheck:
        if not self.llm_config.enabled:
            return DoctorCheck(
                name="llm",
                status="ok",
                message="LLM is disabled; offline analysis, CodeGraph, RAG, and evaluation are available.",
            )
        detail = {
            "model": self.llm_config.model,
            "base_url": self.llm_config.base_url,
            "timeout_seconds": self.llm_config.timeout_seconds,
            "max_context_chars": self.llm_config.max_context_chars,
        }
        if not self.llm_config.api_key:
            return DoctorCheck(
                name="llm",
                status="error",
                message="LLM is enabled but no API key is configured.",
                detail=detail,
            )
        parsed = urlparse(self.llm_config.base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return DoctorCheck(
                name="llm",
                status="error",
                message="LLM base URL must be an absolute http(s) URL.",
                detail=detail,
            )
        if not self.llm_config.model.strip():
            return DoctorCheck(
                name="llm",
                status="error",
                message="LLM model is empty.",
                detail=detail,
            )
        if self.llm_config.timeout_seconds <= 0:
            return DoctorCheck(
                name="llm",
                status="error",
                message="LLM timeout must be greater than zero seconds.",
                detail=detail,
            )
        if self.llm_config.max_context_chars < 4000:
            return DoctorCheck(
                name="llm",
                status="warning",
                message="LLM context budget is very small; RAG may not fit complete required files.",
                detail=detail,
            )
        return DoctorCheck(
            name="llm",
            status="ok",
            message="LLM configuration is present",
            detail=detail,
        )
