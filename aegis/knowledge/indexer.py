from __future__ import annotations

from collections import Counter, defaultdict
from pathlib import Path
import subprocess

from aegis.cache import FileRecordCache
from aegis.models import Evidence, FileRecord, RepoKnowledge

from .codegraph import CodeGraphBuilder
from .scanner import RepoScanner


CONFIG_NAMES = {
    "package.json",
    "pyproject.toml",
    "requirements.txt",
    "poetry.lock",
    "uv.lock",
    "Cargo.toml",
    "go.mod",
    "pom.xml",
    "build.gradle",
    "settings.gradle",
    "Dockerfile",
    "docker-compose.yml",
    "docker-compose.yaml",
    ".env.example",
    ".github/workflows",
}

ENTRYPOINT_HINTS = {
    "main.py",
    "app.py",
    "server.py",
    "manage.py",
    "index.js",
    "server.js",
    "app.js",
    "main.ts",
    "index.ts",
    "main.go",
    "cmd",
    "src/main",
}

FRAMEWORK_HINTS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "express": "Express",
    "next": "Next.js",
    "react": "React",
    "vue": "Vue",
    "nestjs": "NestJS",
    "spring": "Spring",
    "gin-gonic": "Gin",
    "actix": "Actix",
    "rocket": "Rocket",
    "pytest": "pytest",
    "jest": "Jest",
    "vitest": "Vitest",
}


class KnowledgeBuilder:
    def __init__(
        self,
        root: Path,
        *,
        max_files: int = 1500,
        cache_dir: Path | None = None,
        use_cache: bool = True,
    ) -> None:
        self.root = root.resolve()
        self.max_files = max_files
        self.cache = FileRecordCache(cache_dir) if cache_dir and use_cache else None

    def build(self) -> RepoKnowledge:
        scanner = RepoScanner(self.root, max_files=self.max_files)
        cached = self.cache.load() if self.cache else {}
        files = scanner.scan(cached)
        if self.cache:
            self.cache.save(files)
        evidence = [item for record in files for item in record.evidence]
        stats = scanner.stats(files)
        stats["cache_hits"] = sum(1 for item in files if item.cached)
        stats["cache_misses"] = sum(1 for item in files if not item.cached)
        dependency_graph = self._dependency_graph(files)
        call_graph = self._call_graph(files)
        interface_catalog = self._interface_catalog(files)
        configs = self._configs(files)
        frameworks = self._frameworks(files)
        entrypoints = self._entrypoints(files)
        changed_files = self._changed_files()
        repo_map = self._repo_map(files)
        code_graph = CodeGraphBuilder(
            files,
            dependency_graph=dependency_graph,
            call_graph=call_graph,
            entrypoints=entrypoints,
            configs=configs,
        ).build()
        stats["code_graph"] = code_graph.stats
        return RepoKnowledge(
            root=str(self.root),
            repo_name=self.root.name,
            files=files,
            frameworks=frameworks,
            entrypoints=entrypoints,
            configs=configs,
            changed_files=changed_files,
            repo_map=repo_map,
            dependency_graph=dependency_graph,
            call_graph=call_graph,
            code_graph=code_graph,
            interface_catalog=interface_catalog,
            evidence_store=evidence,
            stats=stats,
        )

    @staticmethod
    def _dependency_graph(files: list[FileRecord]) -> dict[str, list[str]]:
        graph: dict[str, list[str]] = {}
        local_modules: dict[str, str] = {}
        for item in files:
            path = Path(item.path)
            without_suffix = path.with_suffix("").as_posix()
            dotted = without_suffix.replace("/", ".")
            local_modules[path.stem] = item.path
            local_modules[dotted] = item.path
            if dotted.endswith(".__init__"):
                local_modules[dotted.removesuffix(".__init__")] = item.path
        for record in files:
            deps: list[str] = []
            for imported in record.imports:
                normalized = imported.replace("/", ".")
                root_name = normalized.split(".")[0]
                if normalized in local_modules and local_modules[normalized] != record.path:
                    deps.append(local_modules[normalized])
                elif root_name in local_modules and local_modules[root_name] != record.path:
                    deps.append(local_modules[root_name])
                else:
                    deps.append(imported)
            graph[record.path] = sorted(set(deps))[:50]
        return graph

    @staticmethod
    def _call_graph(files: list[FileRecord]) -> dict[str, list[str]]:
        symbol_to_file: dict[str, str] = {}
        for record in files:
            for symbol in record.symbols:
                symbol_to_file.setdefault(symbol, record.path)
        graph: dict[str, list[str]] = {}
        for record in files:
            targets = []
            for call in record.calls:
                if call in symbol_to_file and symbol_to_file[call] != record.path:
                    targets.append(symbol_to_file[call])
            graph[record.path] = sorted(set(targets))[:80]
        return graph

    @staticmethod
    def _interface_catalog(files: list[FileRecord]) -> dict[str, list[str]]:
        catalog: dict[str, list[str]] = {}
        for record in files:
            if record.interfaces:
                catalog[record.path] = record.interfaces
        return catalog

    @staticmethod
    def _configs(files: list[FileRecord]) -> list[str]:
        configs: list[str] = []
        for record in files:
            path = record.path
            name = Path(path).name
            if name in CONFIG_NAMES or any(marker in path for marker in CONFIG_NAMES):
                configs.append(path)
        return sorted(set(configs))

    @staticmethod
    def _frameworks(files: list[FileRecord]) -> list[str]:
        scores: Counter[str] = Counter()
        for record in files:
            joined = " ".join(record.imports + record.symbols + [record.path]).lower()
            for hint, framework in FRAMEWORK_HINTS.items():
                if hint in joined:
                    scores[framework] += 1
        return [name for name, _ in scores.most_common()]

    @staticmethod
    def _entrypoints(files: list[FileRecord]) -> list[str]:
        entries: list[str] = []
        for record in files:
            lowered = record.path.lower()
            if any(hint in lowered for hint in ENTRYPOINT_HINTS):
                entries.append(record.path)
            if record.interfaces and record.path not in entries:
                entries.append(record.path)
        return sorted(set(entries))[:40]

    @staticmethod
    def _repo_map(files: list[FileRecord]) -> list[str]:
        scored: list[tuple[int, str]] = []
        for record in files:
            score = 0
            score += min(record.lines, 500) // 20
            score += len(record.symbols) * 3
            score += len(record.interfaces) * 6
            if Path(record.path).name.lower() in {"readme.md", "package.json", "pyproject.toml"}:
                score += 30
            if "test" in record.path.lower():
                score -= 5
            scored.append((score, record.path))
        return [path for _, path in sorted(scored, reverse=True)[:80]]

    def _changed_files(self) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "-C", str(self.root), "diff", "--name-only", "HEAD"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                check=False,
            )
        except (OSError, ValueError):
            return []
        if result.returncode != 0:
            return []
        return sorted({line.strip().replace("\\", "/") for line in result.stdout.splitlines() if line.strip()})


def summarize_dependencies(knowledge: RepoKnowledge) -> dict[str, int]:
    fanout: dict[str, int] = {}
    for path, deps in knowledge.dependency_graph.items():
        fanout[path] = len(deps)
    return dict(sorted(fanout.items(), key=lambda item: item[1], reverse=True))


def evidence_for_file(record: FileRecord) -> list[Evidence]:
    return record.evidence[:3]
