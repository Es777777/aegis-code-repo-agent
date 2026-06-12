from __future__ import annotations

from collections import Counter
from fnmatch import fnmatch
from pathlib import Path

from aegis.models import Evidence, FileRecord
from aegis.utils import file_sha256, is_ignored, is_probably_binary, read_text, relpath

from .language import detect_language
from .parsers import extract_calls, extract_imports, extract_interfaces, extract_symbols


class RepoScanner:
    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 1500,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
    ) -> None:
        self.root = root.resolve()
        self.max_files = max_files
        self.include = include or []
        self.exclude = exclude or []
        self.skipped: Counter[str] = Counter()

    def scan(self, cached_records: dict[str, FileRecord] | None = None) -> list[FileRecord]:
        cached_records = cached_records or {}
        files: list[FileRecord] = []
        for path in self.root.rglob("*"):
            if len(files) >= self.max_files:
                self.skipped["max_files"] += 1
                break
            if not path.is_file():
                continue
            if is_ignored(path):
                self.skipped["ignored_dir"] += 1
                continue
            if is_probably_binary(path):
                self.skipped["binary"] += 1
                continue
            relative = relpath(path, self.root)
            if not self._matches_scope(relative):
                self.skipped["scope"] += 1
                continue
            try:
                content_hash = file_sha256(path)
            except OSError:
                self.skipped["hash_error"] += 1
                continue
            cached = cached_records.get(relative)
            if cached and cached.content_hash == content_hash and cached.size == path.stat().st_size:
                cached.cached = True
                files.append(cached)
                continue
            try:
                text = read_text(path)
            except (OSError, UnicodeDecodeError):
                self.skipped["read_error"] += 1
                continue

            language = detect_language(path)
            lines = text.splitlines()
            imports = extract_imports(text, language)
            symbols = extract_symbols(text, language)
            interfaces = extract_interfaces(text, language)
            calls = extract_calls(text, language)
            evidence = self._file_evidence(relative, lines, symbols, interfaces)
            files.append(
                FileRecord(
                    path=relative,
                    language=language,
                    size=path.stat().st_size,
                    lines=len(lines),
                    content_hash=content_hash,
                    cached=False,
                    imports=imports,
                    symbols=symbols,
                    interfaces=interfaces,
                    calls=calls,
                    evidence=evidence,
                )
            )
        return files

    def _matches_scope(self, relative_path: str) -> bool:
        if self.include and not any(self._match(pattern, relative_path) for pattern in self.include):
            return False
        if self.exclude and any(self._match(pattern, relative_path) for pattern in self.exclude):
            return False
        return True

    @staticmethod
    def _match(pattern: str, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        candidate = pattern.replace("\\", "/")
        return fnmatch(normalized, candidate) or fnmatch(Path(normalized).name, candidate)

    def stats(self, files: list[FileRecord]) -> dict[str, object]:
        languages = Counter(item.language for item in files)
        total_lines = sum(item.lines for item in files)
        return {
            "file_count": len(files),
            "total_lines": total_lines,
            "languages": dict(languages.most_common()),
            "scan": {
                "max_files": self.max_files,
                "include": self.include,
                "exclude": self.exclude,
                "skipped": dict(self.skipped),
            },
        }

    @staticmethod
    def _file_evidence(
        path: str,
        lines: list[str],
        symbols: list[str],
        interfaces: list[str],
    ) -> list[Evidence]:
        evidence: list[Evidence] = []
        targets = set(symbols[:5] + interfaces[:5])
        for interface in interfaces[:5]:
            parts = interface.split(maxsplit=1)
            if len(parts) == 2:
                targets.add(parts[1])
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if any(target in stripped for target in targets):
                evidence.append(Evidence(path=path, line=idx, snippet=stripped[:220]))
            if len(evidence) >= 5:
                break
        return evidence
