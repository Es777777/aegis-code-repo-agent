#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def find_project_root(start: Path) -> Path:
    for path in [start, *start.parents]:
        if (path / "main.py").exists() and (path / "aegis").is_dir():
            return path
    raise SystemExit("Could not find AEGIS project root containing main.py and aegis/.")


def run(args: list[str], *, cwd: Path) -> int:
    command = [sys.executable, "main.py", *args]
    completed = subprocess.run(command, cwd=str(cwd), check=False)
    return completed.returncode


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run AEGIS analysis, RAG Q&A, or CodeGraph tracing.")
    sub = parser.add_subparsers(dest="command", required=True)

    analyze = sub.add_parser("analyze")
    analyze.add_argument("repo")
    analyze.add_argument("--max-files", default="1500")
    analyze.add_argument("--out", default="output/aegis")
    analyze.add_argument("--no-cache", action="store_true")
    analyze.add_argument("--eval", action="store_true")
    analyze.add_argument("--eval-suite")
    analyze.add_argument("--json", action="store_true")

    ask = sub.add_parser("ask")
    ask.add_argument("repo")
    ask.add_argument("question")
    ask.add_argument("--max-files", default="1500")
    ask.add_argument("--out", default="output/aegis")
    ask.add_argument("--top-k", default="8")
    ask.add_argument("--llm", action="store_true")
    ask.add_argument("--no-cache", action="store_true")
    ask.add_argument("--eval", action="store_true")
    ask.add_argument("--eval-suite")
    ask.add_argument("--json", action="store_true")

    trace = sub.add_parser("trace")
    trace.add_argument("repo")
    trace.add_argument("route")
    trace.add_argument("--max-files", default="1500")
    trace.add_argument("--out", default="output/aegis")
    trace.add_argument("--no-cache", action="store_true")
    trace.add_argument("--eval", action="store_true")
    trace.add_argument("--eval-suite")
    trace.add_argument("--json", action="store_true")

    eval_cmd = sub.add_parser("eval")
    eval_cmd.add_argument("repo")
    eval_cmd.add_argument("--max-files", default="1500")
    eval_cmd.add_argument("--out", default="output/aegis")
    eval_cmd.add_argument("--suite")
    eval_cmd.add_argument("--no-cache", action="store_true")
    eval_cmd.add_argument("--json", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).resolve())
    common = [args.repo, "--max-files", str(args.max_files), "--out", args.out]
    if getattr(args, "no_cache", False):
        common.append("--no-cache")
    if getattr(args, "eval", False):
        common.append("--eval")
    if getattr(args, "eval_suite", None):
        common.extend(["--eval-suite", args.eval_suite])
    if getattr(args, "json", False):
        common.append("--json")

    if args.command == "analyze":
        return run(common, cwd=root)
    if args.command == "ask":
        command = [*common, "--ask", args.question, "--top-k", str(args.top_k)]
        if args.llm:
            command.append("--llm")
        return run(command, cwd=root)
    if args.command == "trace":
        return run([*common, "--trace-interface", args.route], cwd=root)
    if args.command == "eval":
        command = [*common, "--eval"]
        if args.suite:
            command.extend(["--eval-suite", args.suite])
        return run(command, cwd=root)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
