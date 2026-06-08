from __future__ import annotations

from collections import Counter
from pathlib import Path

from aegis.models import Evidence, FileRecord
from aegis.utils import file_sha256, is_ignored, is_probably_binary, read_text, relpath

from .language import detect_language
from .parsers import extract_calls, extract_imports, extract_interfaces, extract_symbols


class RepoScanner:
    def __init__(self, root: Path, *, max_files: int = 1500) -> None:
        self.root = root.resolve()
        self.max_files = max_files

    def scan(self, cached_records: dict[str, FileRecord] | None = None) -> list[FileRecord]:
        cached_records = cached_records or {}
        files: list[FileRecord] = []
        for path in self.root.rglob("*"):
            if len(files) >= self.max_files:
                break
            if not path.is_file() or is_ignored(path) or is_probably_binary(path):
                continue
            relative = relpath(path, self.root)
            try:
                content_hash = file_sha256(path)
            except OSError:
                continue
            cached = cached_records.get(relative)
            if cached and cached.content_hash == content_hash and cached.size == path.stat().st_size:
                cached.cached = True
                files.append(cached)
                continue
            try:
                text = read_text(path)
            except (OSError, UnicodeDecodeError):
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

    @staticmethod
    def stats(files: list[FileRecord]) -> dict[str, object]:
        languages = Counter(item.language for item in files)
        total_lines = sum(item.lines for item in files)
        return {
            "file_count": len(files),
            "total_lines": total_lines,
            "languages": dict(languages.most_common()),
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
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not stripped:
                continue
            if any(target in stripped for target in targets):
                evidence.append(Evidence(path=path, line=idx, snippet=stripped[:220]))
            if len(evidence) >= 5:
                break
        return evidence
