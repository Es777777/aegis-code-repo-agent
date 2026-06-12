from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def load_env_file(path: str | Path = ".env", *, override: bool = False) -> None:
    env_path = Path(path)
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key:
            continue
        if override or key not in os.environ:
            os.environ[key] = value


def env_bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def env_list(name: str) -> list[str]:
    value = os.getenv(name)
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


@dataclass
class LLMConfig:
    enabled: bool = False
    api_key: str | None = None
    base_url: str = "https://api.openai.com/v1"
    model: str = "gpt-4o-mini"
    timeout_seconds: int = 120
    max_context_chars: int = 14000

    @classmethod
    def from_env(cls, *, enabled: bool = False) -> "LLMConfig":
        api_key = (
            os.getenv("AEGIS_LLM_API_KEY")
            or os.getenv("OPENAI_API_KEY")
            or os.getenv("MM_TEXT_API_KEY")
        )
        base_url = (
            os.getenv("AEGIS_LLM_BASE_URL")
            or os.getenv("OPENAI_BASE_URL")
            or os.getenv("MM_TEXT_BASE_URL")
            or "https://api.openai.com/v1"
        )
        model = os.getenv("AEGIS_LLM_MODEL") or os.getenv("OPENAI_MODEL") or "gpt-4o-mini"
        return cls(
            enabled=enabled or env_bool("AEGIS_LLM_ENABLED", False),
            api_key=api_key,
            base_url=base_url.rstrip("/"),
            model=model,
            timeout_seconds=env_int("AEGIS_LLM_TIMEOUT_SECONDS", 120),
            max_context_chars=env_int("AEGIS_LLM_MAX_CONTEXT_CHARS", 14000),
        )


@dataclass
class AegisConfig:
    repo_path: str | None = None
    output_dir: str = "output/aegis"
    max_files: int = 1500
    include: list[str] | None = None
    exclude: list[str] | None = None
    use_cache: bool = True
    serve_dir: str | None = None
    serve_host: str = "127.0.0.1"
    serve_port: int = 8765
    llm: LLMConfig | None = None

    @classmethod
    def from_env(cls) -> "AegisConfig":
        return cls(
            repo_path=os.getenv("AEGIS_REPO_PATH"),
            output_dir=os.getenv("AEGIS_OUTPUT_DIR", "output/aegis"),
            max_files=env_int("AEGIS_MAX_FILES", 1500),
            include=env_list("AEGIS_INCLUDE"),
            exclude=env_list("AEGIS_EXCLUDE"),
            use_cache=env_bool("AEGIS_USE_CACHE", True),
            serve_dir=os.getenv("AEGIS_SERVE_DIR"),
            serve_host=os.getenv("AEGIS_SERVE_HOST", "127.0.0.1"),
            serve_port=env_int("AEGIS_SERVE_PORT", 8765),
            llm=LLMConfig.from_env(),
        )
