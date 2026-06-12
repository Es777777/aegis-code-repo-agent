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
    analyze.add_argument("--include", action="append", default=[])
    analyze.add_argument("--exclude", action="append", default=[])
    analyze.add_argument("--no-cache", action="store_true")
    analyze.add_argument("--llm", action="store_true")
    analyze.add_argument("--eval", action="store_true")
    analyze.add_argument("--eval-suite")
    analyze.add_argument("--eval-fail-under")
    analyze.add_argument("--json", action="store_true")

    ask = sub.add_parser("ask")
    ask.add_argument("repo", nargs="?")
    ask.add_argument("question")
    ask.add_argument("--from-output")
    ask.add_argument("--max-files", default="1500")
    ask.add_argument("--out", default="output/aegis")
    ask.add_argument("--include", action="append", default=[])
    ask.add_argument("--exclude", action="append", default=[])
    ask.add_argument("--top-k", default="8")
    ask.add_argument("--context-file", action="append", default=[])
    ask.add_argument("--context-chars", default="48000")
    ask.add_argument("--llm", action="store_true")
    ask.add_argument("--no-cache", action="store_true")
    ask.add_argument("--eval", action="store_true")
    ask.add_argument("--eval-suite")
    ask.add_argument("--eval-fail-under")
    ask.add_argument("--json", action="store_true")

    trace = sub.add_parser("trace")
    trace.add_argument("repo", nargs="?")
    trace.add_argument("route")
    trace.add_argument("--from-output")
    trace.add_argument("--max-files", default="1500")
    trace.add_argument("--out", default="output/aegis")
    trace.add_argument("--include", action="append", default=[])
    trace.add_argument("--exclude", action="append", default=[])
    trace.add_argument("--no-cache", action="store_true")
    trace.add_argument("--llm", action="store_true")
    trace.add_argument("--eval", action="store_true")
    trace.add_argument("--eval-suite")
    trace.add_argument("--eval-fail-under")
    trace.add_argument("--json", action="store_true")

    impact = sub.add_parser("impact")
    impact.add_argument("repo", nargs="?")
    impact.add_argument("--from-output")
    impact.add_argument("--path", action="append", default=[])
    impact.add_argument("--depth", default="3")
    impact.add_argument("--max-files", default="1500")
    impact.add_argument("--out", default="output/aegis")
    impact.add_argument("--include", action="append", default=[])
    impact.add_argument("--exclude", action="append", default=[])
    impact.add_argument("--no-cache", action="store_true")
    impact.add_argument("--llm", action="store_true")
    impact.add_argument("--json", action="store_true")

    eval_cmd = sub.add_parser("eval")
    eval_cmd.add_argument("repo", nargs="?")
    eval_cmd.add_argument("--from-output")
    eval_cmd.add_argument("--max-files", default="1500")
    eval_cmd.add_argument("--out", default="output/aegis")
    eval_cmd.add_argument("--include", action="append", default=[])
    eval_cmd.add_argument("--exclude", action="append", default=[])
    eval_cmd.add_argument("--suite")
    eval_cmd.add_argument("--fail-under")
    eval_cmd.add_argument("--no-cache", action="store_true")
    eval_cmd.add_argument("--llm", action="store_true")
    eval_cmd.add_argument("--json", action="store_true")

    ready = sub.add_parser("ready")
    ready.add_argument("repo", nargs="?")
    ready.add_argument("--from-output")
    ready.add_argument("--max-files", default="1500")
    ready.add_argument("--out", default="output/aegis")
    ready.add_argument("--include", action="append", default=[])
    ready.add_argument("--exclude", action="append", default=[])
    ready.add_argument("--suite")
    ready.add_argument("--fail-under", default="0.75")
    ready.add_argument("--ask")
    ready.add_argument("--top-k", default="8")
    ready.add_argument("--context-file", action="append", default=[])
    ready.add_argument("--context-chars", default="48000")
    ready.add_argument("--no-cache", action="store_true")
    ready.add_argument("--llm", action="store_true")
    ready.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor")
    doctor.add_argument("repo", nargs="?")
    doctor.add_argument("--out", default="output/aegis")
    doctor.add_argument("--llm", action="store_true")
    doctor.add_argument("--json", action="store_true")

    status = sub.add_parser("status")
    status.add_argument("repo", nargs="?")
    status.add_argument("--from-output")
    status.add_argument("--max-files", default="1500")
    status.add_argument("--out", default="output/aegis")
    status.add_argument("--include", action="append", default=[])
    status.add_argument("--exclude", action="append", default=[])
    status.add_argument("--no-cache", action="store_true")
    status.add_argument("--json", action="store_true")

    handoff = sub.add_parser("handoff")
    handoff.add_argument("repo", nargs="?")
    handoff.add_argument("--from-output")
    handoff.add_argument("--max-files", default="1500")
    handoff.add_argument("--out", default="output/aegis")
    handoff.add_argument("--include", action="append", default=[])
    handoff.add_argument("--exclude", action="append", default=[])
    handoff.add_argument("--no-cache", action="store_true")
    handoff.add_argument("--json", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = find_project_root(Path(__file__).resolve())
    if args.command == "doctor":
        command = ["--doctor", "--out", args.out]
        if args.repo:
            command.insert(0, args.repo)
        if args.llm:
            command.append("--llm")
        if args.json:
            command.append("--json")
        return run(command, cwd=root)

    if getattr(args, "from_output", None):
        common = ["--from-output", args.from_output, "--out", args.out]
    else:
        if not args.repo:
            raise SystemExit(f"{args.command} requires <repo> unless --from-output is provided")
        common = [args.repo, "--max-files", str(args.max_files), "--out", args.out]
    for pattern in getattr(args, "include", []):
        common.extend(["--include", pattern])
    for pattern in getattr(args, "exclude", []):
        common.extend(["--exclude", pattern])
    if getattr(args, "no_cache", False):
        common.append("--no-cache")
    if getattr(args, "llm", False):
        common.append("--llm")
    if getattr(args, "eval", False):
        common.append("--eval")
    if getattr(args, "eval_suite", None):
        common.extend(["--eval-suite", args.eval_suite])
    if getattr(args, "eval_fail_under", None):
        common.extend(["--eval-fail-under", args.eval_fail_under])
    if getattr(args, "json", False):
        common.append("--json")

    if args.command == "analyze":
        return run(common, cwd=root)
    if args.command == "status":
        return run([*common, "--status"], cwd=root)
    if args.command == "handoff":
        return run([*common, "--handoff"], cwd=root)
    if args.command == "ask":
        command = [
            *common,
            "--ask",
            args.question,
            "--top-k",
            str(args.top_k),
            "--context-chars",
            str(args.context_chars),
        ]
        for path in args.context_file:
            command.extend(["--context-file", path])
        return run(command, cwd=root)
    if args.command == "trace":
        return run([*common, "--trace-interface", args.route], cwd=root)
    if args.command == "impact":
        command = [*common, "--impact", "--impact-depth", str(args.depth)]
        for path in args.path:
            command.extend(["--impact-file", path])
        return run(command, cwd=root)
    if args.command == "eval":
        command = [*common, "--eval"]
        if args.suite:
            command.extend(["--eval-suite", args.suite])
        if args.fail_under:
            command.extend(["--eval-fail-under", args.fail_under])
        return run(command, cwd=root)
    if args.command == "ready":
        command = [*common, "--ready", "--ready-fail-under", str(args.fail_under)]
        if args.suite:
            command.extend(["--eval-suite", args.suite])
        if args.ask:
            command.extend(
                [
                    "--ready-ask",
                    args.ask,
                    "--top-k",
                    str(args.top_k),
                    "--context-chars",
                    str(args.context_chars),
                ]
            )
            for path in args.context_file:
                command.extend(["--context-file", path])
        return run(command, cwd=root)
    raise SystemExit(f"Unknown command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
