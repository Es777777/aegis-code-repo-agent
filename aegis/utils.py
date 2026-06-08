from __future__ import annotations

import json
import re
import hashlib
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


IGNORED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".venv",
    "venv",
    "env",
    "node_modules",
    "dist",
    "build",
    "target",
    ".next",
    ".nuxt",
    "coverage",
    ".idea",
    ".vscode",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".ico",
    ".pdf",
    ".zip",
    ".gz",
    ".tar",
    ".7z",
    ".rar",
    ".exe",
    ".dll",
    ".so",
    ".dylib",
    ".class",
    ".jar",
    ".pyc",
}


def relpath(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def is_ignored(path: Path) -> bool:
    return any(part in IGNORED_DIRS for part in path.parts)


def is_probably_binary(path: Path) -> bool:
    return path.suffix.lower() in BINARY_EXTENSIONS


def read_text(path: Path, max_bytes: int = 750_000) -> str:
    raw = path.read_bytes()
    if b"\x00" in raw[:4096]:
        raise UnicodeDecodeError("binary", b"", 0, 1, "binary file")
    raw = raw[:max_bytes]
    for encoding in ("utf-8", "utf-8-sig", "gbk", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    def default(value: Any) -> Any:
        if is_dataclass(value):
            return asdict(value)
        if isinstance(value, Path):
            return str(value)
        raise TypeError(f"Cannot serialize {type(value)!r}")

    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=default), encoding="utf-8")


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(value: str) -> str:
    value = re.sub(r"[^\w.-]+", "-", value, flags=re.UNICODE).strip("-")
    return value or "repo"


def first_nonempty_lines(text: str, limit: int = 8) -> list[str]:
    lines: list[str] = []
    for line in text.splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned[:180])
        if len(lines) >= limit:
            break
    return lines
