from __future__ import annotations

import argparse
from pathlib import Path

from aegis.config import AegisConfig, LLMConfig, load_env_file
from aegis.orchestrator.workflow import AegisWorkflow
from aegis.server import serve


def parse_args() -> argparse.Namespace:
    load_env_file(".env")
    config = AegisConfig.from_env()
    parser = argparse.ArgumentParser(
        prog="aegis",
        description="AEGIS 2.0 MVP: multi-agent repository reading and analysis.",
    )
    parser.add_argument("repo", nargs="?", default=config.repo_path, help="要分析的本地代码仓库路径")
    parser.add_argument("--out", default=config.output_dir, help="输出目录，默认 output/aegis")
    parser.add_argument("--max-files", type=int, default=config.max_files, help="最大扫描文件数")
    parser.add_argument("--no-cache", action="store_true", default=not config.use_cache, help="禁用文件解析缓存")
    parser.add_argument("--llm", action="store_true", default=bool(config.llm and config.llm.enabled), help="启用可选 LLM 综合分析")
    parser.add_argument(
        "--serve",
        nargs="?",
        const=config.serve_dir or config.output_dir,
        default=None,
        help="启动静态报告服务器，传入要服务的目录；不传目录时读取 AEGIS_SERVE_DIR",
    )
    parser.add_argument("--host", default=config.serve_host, help="报告服务器 host")
    parser.add_argument("--port", type=int, default=config.serve_port, help="报告服务器 port")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.serve:
        serve(Path(args.serve), host=args.host, port=args.port)
        return 0
    if not args.repo:
        raise SystemExit("缺少仓库路径。用法：python main.py <repo-path> 或 python main.py --serve <report-dir>")
    repo = Path(args.repo)
    if not repo.exists() or not repo.is_dir():
        raise SystemExit(f"仓库路径不存在或不是目录：{repo}")
    workflow = AegisWorkflow(
        repo,
        output_root=Path(args.out),
        max_files=args.max_files,
        use_cache=not args.no_cache,
        llm_config=LLMConfig.from_env(enabled=args.llm),
    )
    result = workflow.run()
    print(f"AEGIS analysis complete: {result.output_dir}")
    print(f"- report: {result.output_dir / 'report.md'}")
    print(f"- html: {result.output_dir / 'report.html'}")
    print(f"- mermaid: {result.output_dir / 'architecture.mmd'}")
    print(f"- knowledge: {result.output_dir / 'knowledge.json'}")
    print(f"- findings: {result.output_dir / 'findings.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
