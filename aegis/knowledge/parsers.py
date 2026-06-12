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
    interfaces: list[str] = []
    interfaces.extend(_python_web_interfaces(text) if language == "Python" else [])
    interfaces.extend(_js_web_interfaces(text) if language in {"JavaScript", "TypeScript"} else [])
    interfaces.extend(_spring_interfaces(text) if language in {"Java", "Kotlin"} else [])
    interfaces.extend(_generic_route_interfaces(text))
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


def _python_web_interfaces(text: str) -> list[str]:
    prefixes = {"app": "", "router": ""}
    for match in re.finditer(
        r"\b([A-Za-z_]\w*)\s*=\s*(?:APIRouter|Blueprint)\([^)]*(?:prefix|url_prefix)\s*=\s*['\"]([^'\"]+)['\"]",
        text,
    ):
        prefixes[match.group(1)] = match.group(2)

    interfaces: list[str] = []
    for match in re.finditer(
        r"@([A-Za-z_]\w*)\.(get|post|put|patch|delete)\(\s*['\"]([^'\"]+)",
        text,
        flags=re.I,
    ):
        receiver, method, route = match.groups()
        interfaces.append(f"{method.upper()} {_join_route(prefixes.get(receiver, ''), route)}")
    return interfaces


def _js_web_interfaces(text: str) -> list[str]:
    router_vars = set(re.findall(r"\b([A-Za-z_]\w*)\s*=\s*(?:express\.)?Router\(\s*\)", text))
    router_vars.add("router")
    prefixes: dict[str, str] = {}
    for match in re.finditer(
        r"\b(?:app|server)\.use\(\s*['\"]([^'\"]+)['\"]\s*,\s*([A-Za-z_]\w*)\s*\)",
        text,
    ):
        prefixes[match.group(2)] = match.group(1)

    interfaces: list[str] = []
    for match in re.finditer(
        r"\b([A-Za-z_]\w*)\.(get|post|put|patch|delete)\(\s*['\"`]([^'\"`]+)",
        text,
        flags=re.I,
    ):
        receiver, method, route = match.groups()
        prefix = prefixes.get(receiver, "") if receiver in router_vars else ""
        interfaces.append(f"{method.upper()} {_join_route(prefix, route)}")

    current_prefix = ""
    for line in text.splitlines():
        decorator = re.search(r"@(Get|Post|Put|Patch|Delete)\(\s*['\"`]([^'\"`]*)['\"`]\s*\)", line)
        if decorator:
            method, route = decorator.groups()
            interfaces.append(f"{method.upper()} {_join_route(current_prefix, route)}")
            continue
        controller = re.search(r"@Controller\(\s*['\"`]([^'\"`]*)['\"`]\s*\)", line)
        if controller:
            current_prefix = controller.group(1)
    return interfaces


def _spring_interfaces(text: str) -> list[str]:
    class_prefix = ""
    class_mapping = re.search(r"@RequestMapping\(\s*(?:value\s*=\s*)?['\"]([^'\"]*)", text)
    if class_mapping:
        class_prefix = class_mapping.group(1)

    method_map = {
        "GetMapping": "GET",
        "PostMapping": "POST",
        "PutMapping": "PUT",
        "PatchMapping": "PATCH",
        "DeleteMapping": "DELETE",
    }
    interfaces: list[str] = []
    for annotation, method in method_map.items():
        for match in re.finditer(
            rf"@{annotation}\(\s*(?:(?:value|path)\s*=\s*)?['\"]([^'\"]*)",
            text,
        ):
            interfaces.append(f"{method} {_join_route(class_prefix, match.group(1))}")
    for match in re.finditer(
        r"@RequestMapping\(\s*[^)]*method\s*=\s*RequestMethod\.(GET|POST|PUT|PATCH|DELETE)[^)]*(?:(?:value|path)\s*=\s*)?['\"]([^'\"]*)",
        text,
    ):
        method, route = match.groups()
        interfaces.append(f"{method.upper()} {_join_route(class_prefix, route)}")
    return interfaces


def _generic_route_interfaces(text: str) -> list[str]:
    patterns = [
        r"\b(?:GET|POST|PUT|PATCH|DELETE)\s+(/[^\s'\"`]+)",
        r"\bpath\(\s*['\"]([^'\"]+)",
        r"\bRoute\(\s*['\"]([^'\"]+)",
    ]
    interfaces: list[str] = []
    for pattern in patterns:
        interfaces.extend(_regex_unique(pattern, text))
    method_patterns = [
        r"\.(GET|POST|PUT|PATCH|DELETE)\(\s*['\"`]([^'\"`]+)",
        r"\[(HttpGet|HttpPost|HttpPut|HttpPatch|HttpDelete)\(\s*['\"`]([^'\"`]+)",
        r"\bRoute::(get|post|put|patch|delete)\(\s*['\"`]([^'\"`]+)",
    ]
    for pattern in method_patterns:
        for match in re.finditer(pattern, text):
            method, route = match.groups()
            method = method.removeprefix("Http").upper()
            interfaces.append(f"{method} {_join_route('', route)}")
    return interfaces


def _join_route(prefix: str, route: str) -> str:
    prefix = prefix.strip()
    route = route.strip()
    if not prefix:
        combined = route
    elif not route or route == "/":
        combined = prefix
    else:
        combined = f"{prefix.rstrip('/')}/{route.lstrip('/')}"
    if not combined.startswith("/"):
        combined = f"/{combined}"
    return combined or "/"


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
