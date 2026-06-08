from __future__ import annotations

import ast
import re


def extract_imports(text: str, language: str) -> list[str]:
    if language == "Python":
        return _python_imports(text)
    if language in {"JavaScript", "TypeScript", "Vue", "Svelte"}:
        return _js_imports(text)
    if language in {"Java", "Kotlin"}:
        return _regex_unique(r"^\s*import\s+([\w.*]+)", text)
    if language == "Go":
        return _go_imports(text)
    if language == "Rust":
        return _regex_unique(r"^\s*use\s+([^;]+);", text)
    return []


def extract_symbols(text: str, language: str) -> list[str]:
    if language == "Python":
        return _python_symbols(text)
    patterns = [
        r"\bclass\s+([A-Za-z_]\w*)",
        r"\bfunction\s+([A-Za-z_]\w*)",
        r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(",
        r"\b(?:export\s+)?(?:async\s+)?function\s+([A-Za-z_]\w*)",
        r"\bfunc\s+([A-Za-z_]\w*)",
        r"\bstruct\s+([A-Za-z_]\w*)",
        r"\binterface\s+([A-Za-z_]\w*)",
    ]
    symbols: list[str] = []
    for pattern in patterns:
        symbols.extend(_regex_unique(pattern, text))
    return _dedupe(symbols)[:80]


def extract_interfaces(text: str, language: str) -> list[str]:
    patterns = [
        r"@(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
        r"\b(?:app|router)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
        r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s'\"`]+)",
        r"\bpath\(\s*['\"]([^'\"]+)",
        r"\bRoute\(\s*['\"]([^'\"]+)",
        r"\b@RequestMapping\(\s*[^)]*['\"]([^'\"]+)",
        r"\b@GetMapping\(\s*['\"]([^'\"]+)",
        r"\b@PostMapping\(\s*['\"]([^'\"]+)",
        r"\bPutMapping\(\s*['\"]([^'\"]+)",
        r"\bDeleteMapping\(\s*['\"]([^'\"]+)",
    ]
    interfaces: list[str] = []
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.MULTILINE):
            groups = [item for item in match.groups() if item]
            if len(groups) == 2 and groups[0].lower() in {"get", "post", "put", "patch", "delete"}:
                interfaces.append(f"{groups[0].upper()} {groups[1]}")
            elif groups:
                interfaces.append(groups[-1])
    return _dedupe(interfaces)[:80]


def extract_calls(text: str, language: str) -> list[str]:
    if language == "Python":
        return _python_calls(text)
    patterns = [
        r"\b([A-Za-z_]\w*)\s*\(",
        r"\.([A-Za-z_]\w*)\s*\(",
    ]
    calls: list[str] = []
    for pattern in patterns:
        calls.extend(_regex_unique(pattern, text))
    keywords = {"if", "for", "while", "switch", "return", "function", "class"}
    return [item for item in _dedupe(calls) if item not in keywords][:120]


def _python_imports(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_unique(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", text)
    imports: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append(node.module)
    return _dedupe(imports)[:80]


def _python_symbols(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_unique(r"^\s*(?:class|def)\s+([A-Za-z_]\w*)", text)
    symbols: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            symbols.append(node.name)
    return _dedupe(symbols)[:80]


def _python_calls(text: str) -> list[str]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _regex_unique(r"\b([A-Za-z_]\w*)\s*\(", text)[:120]
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name):
                calls.append(node.func.id)
            elif isinstance(node.func, ast.Attribute):
                calls.append(node.func.attr)
    return _dedupe(calls)[:120]


def _js_imports(text: str) -> list[str]:
    imports = _regex_unique(r"\bfrom\s+['\"]([^'\"]+)['\"]", text)
    imports.extend(_regex_unique(r"\brequire\(\s*['\"]([^'\"]+)['\"]\s*\)", text))
    return _dedupe(imports)[:80]


def _go_imports(text: str) -> list[str]:
    imports = _regex_unique(r"^\s*import\s+\"([^\"]+)\"", text)
    block = re.search(r"import\s*\((.*?)\)", text, flags=re.S)
    if block:
        imports.extend(re.findall(r"\"([^\"]+)\"", block.group(1)))
    return _dedupe(imports)[:80]


def _regex_unique(pattern: str, text: str) -> list[str]:
    values: list[str] = []
    for match in re.finditer(pattern, text, flags=re.MULTILINE):
        groups = [group for group in match.groups() if group]
        if groups:
            values.append(groups[0])
    return _dedupe(values)


def _dedupe(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        cleaned = value.strip()
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            result.append(cleaned)
    return result
