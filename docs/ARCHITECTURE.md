# AEGIS 2.0 Architecture

## 分层

1. 用户目标层：用户提供仓库路径、分析范围和输出偏好。
2. 仓库采集层：`RepoScanner` 读取文件、Git Diff、配置和入口线索。
3. 代码知识层：`KnowledgeBuilder` 生成 Repo Map、CodeGraph、接口目录和 Evidence Store。
4. 编排控制层：`AegisWorkflow` 管理事件日志、缓存、Agent 调度和报告生成。
5. 专项分析层：多个 Analyst 基于共享知识层产生结论。
6. 审查交付层：`EvidenceReviewer` 检查证据，`ReportWriter` 生成 Markdown、HTML 和 Mermaid。

## 关键文件

- `aegis/models.py`：核心数据结构。
- `aegis/knowledge/scanner.py`：仓库扫描。
- `aegis/knowledge/parsers.py`：语言启发式解析。
- `aegis/knowledge/indexer.py`：知识层构建。
- `aegis/knowledge/codegraph.py`：统一 CodeGraph 节点/边模型与查询接口。
- `aegis/orchestrator/context.py`：Context Router。
- `aegis/orchestrator/workflow.py`：主编排流程。
- `aegis/agents/`：专项分析 Agent。
- `aegis/reporting/writer.py`：报告生成。
- `aegis/rag/index.py`：RAG chunk 与索引构建。
- `aegis/rag/retriever.py`：离线 BM25/关键词检索。
- `aegis/rag/qa.py`：仓库问答 Agent。

## 输出契约

每次分析生成：

- `knowledge.json`：结构化仓库知识。
- `findings.json`：Agent 结论。
- `events.json`：编排事件日志。
- `report.md`：Markdown 报告。
- `report.html`：可浏览报告。
- `architecture.mmd`：Mermaid 架构图。
- `rag_index.json`：面向 Agent 问答的检索索引。
- `manifest.json`：分析运行清单，记录版本、配置、仓库、统计和产物库存。

## 产物复用

`--from-output` 可以从已有输出目录读取 `knowledge.json`、`findings.json` 和 `rag_index.json`，跳过重新扫描与 Agent 分析，直接执行 RAG 问答、CodeGraph 追踪或评测。这适合大仓库的多轮追问、前端交互和比赛评测脚本。执行 RAG 问答或评测时必须存在保存好的 `rag_index.json`，避免在复用产物时隐式依赖原仓库路径重新构建索引。复用前会校验 `manifest.json` 的 schema、仓库身份、必需产物大小和 SHA256；如果产物被改动、删除或 manifest 过旧，命令会失败而不是继续使用 stale evidence。

CLI 示例：

```powershell
python main.py --from-output output\aegis\sample_repo --ask "用户创建接口在哪里？"
python main.py --from-output output\aegis\sample_repo --trace-interface /users --json
```

## Doctor

`--doctor` 是轻量预检入口，不执行完整仓库分析。它检查 Python 版本、仓库路径、输出目录写权限、Git 可用性和可选 LLM 配置状态，支持 JSON 输出和非零退出码。启用 `--llm` 时，Doctor 会本地校验 API key、绝对 `http(s)` base URL、模型名、timeout 和 LLM 上下文预算，但不会发起联网请求。

CLI 示例：

```powershell
python main.py examples\sample_repo --doctor
python main.py examples\sample_repo --doctor --json
```

## 增量分析

`FileRecordCache` 将每个文件的 hash、imports、symbols、interfaces、calls 和 evidence 缓存在 `.cache/file_records.json`。下次分析时，hash 未变的文件直接复用解析结果。

Git 仓库中，`Git Diff Scanner` 会记录 `git diff --name-only HEAD` 的变更文件。后续可以进一步把这些文件映射到受影响模块，只重跑相关 Agent。

## CodeGraph

CodeGraph 是 AEGIS 的核心知识图谱。它把仓库中的文件、模块、类、函数、接口、配置和数据模型统一成节点，把定义、导入、调用、接口暴露、路由绑定和配置关系统一成边。

节点类型：

- `file`
- `module`
- `class`
- `function`
- `interface`
- `config`
- `data_model`
- `external_module`

边类型：

- `contains_file`
- `defines`
- `imports`
- `calls`
- `calls_file`
- `exposes`
- `routes_to`
- `configured_by`
- `defines_data`

查询能力：

- `trace_interface(route)`：从接口路由追踪到 handler、文件和后续依赖。
- `impacted_by_files(paths)`：从 Git Diff 文件反查受影响节点。

接口节点来自轻量静态解析器，覆盖 FastAPI / Flask-style decorators、
Express `app/router`、Fastify route objects、Hono/Fastify-style method routes、
NestJS `@Controller`、Spring `@*Mapping`、Gin/chi-style `.GET/.POST`、
ASP.NET `Http*` attribute、Laravel `Route::*`，以及 Next.js/SvelteKit
file-based route handlers 等常见形态。

CLI 示例：

```powershell
python main.py examples\sample_repo --trace-interface /users
```

## RAG

RAG 层把 CodeGraph 与源码证据转换成 Agent 可读的检索块：

- `repo_overview`
- `file`
- `source`
- `class`
- `function`
- `interface`
- `data_model`
- `edge:*`

`source` chunk 直接来自仓库真实文件内容，保留文件路径、起止行号和带行号的代码正文。默认每块 120 行、20 行重叠，并限制单文件最大读取量，避免大文件把上下文预算吃光。

检索增强包括：

- 标识符拆分：`CamelCase`、`snake_case`、路径片段会拆成可检索 token。
- 查询扩展：内置常见中文仓库问题和 EDA 术语，例如入口、核心模块、布线、布局、硬宏、Vivado、RTL、DFX、器件资源。
- 邻居补全：命中符号、文件或 CodeGraph 边时，会补同文件 `source` chunk 和相关节点 chunk。
- LLM 上下文补源：给模型的 RAG context 会优先包含真实源码；离线回答也会打印围绕命中行的源码节选。

问答流程：

1. `RAGIndexBuilder` 从 `RepoKnowledge` 构建 `rag_index.json`。
2. `RAGRetriever` 使用离线 BM25/关键词检索返回证据 chunk，并补齐源码 companion。
3. `RepositoryQAAgent` 在无 LLM 时输出证据式回答和源码节选；有 LLM 时把带行号源码的上下文交给模型生成自然语言回答。

CLI 示例：

```powershell
python main.py examples\sample_repo --ask "用户创建接口在哪里，数据写入哪里？"
python main.py examples\sample_repo --ask "用户创建接口在哪里，数据写入哪里？" --json
```

`--json` 会输出稳定的机器可读 payload，包含分析摘要、输出文件路径、RAG answer、检索结果、匹配词、证据和源码节选。接口追踪也支持 `python main.py <repo> --trace-interface /users --json`。

## Evaluation

`aegis/evaluation.py` 提供内置评测层，用于把“是否可用”变成可重复检查的指标。它基于已经构建好的 `RepoKnowledge`、`CodeGraph` 和 `RAGIndex` 运行，不额外依赖外部服务。

评测对象：

- RAG：给定问题和期望文件路径，计算命中情况、Top 结果、匹配路径和源码上下文可用性。
- Trace：给定接口路由和期望路径/节点名，计算 CodeGraph 追踪是否命中。

输出指标：

- `rag_recall`
- `trace_success_rate`
- `source_context_coverage`
- `prompt_context_coverage`
- `complete_file_context_coverage`
- `prompt_context_expected_path_coverage`
- `complete_file_expected_path_coverage`
- `overall_score`

CLI 示例：

```powershell
python main.py examples\eda_repo --eval
python main.py examples\eda_repo --eval --json
python main.py <repo> --eval-suite suite.json --json
python main.py <repo> --eval --eval-fail-under 0.9
```

评测结果会写入 `output/aegis/<repo>/evaluation.json`，也会进入 `--json` payload，方便比赛评测脚本、前端或其他 Agent 消费。

`--eval-fail-under` 是质量门禁：当 `overall_score` 低于阈值时 CLI 返回非零状态码，并在 JSON payload 中写入 `quality_gate`。GitHub Actions 会使用该门禁自动防止示例仓库评测退化。

## LLM 接入

`LLMRepositoryAnalyst` 是可选 Agent。启用后：

1. `ContextRouter` 选择最小必要上下文，并优先放入带行号的真实源码块。
2. `LLMClient` 调用 OpenAI 兼容 `/chat/completions`。
3. LLM 输出作为 Finding 进入 Evidence Reviewer。

`ContextRouter` 会为入口、接口、配置、风险和数据相关文件渲染
`source_context`，包含文件路径、语言、行号、`Complete file: yes/no`
以及源码正文。`LLMRepositoryAnalyst` 会把 architecture/interface/risk
三段上下文控制在 `AEGIS_LLM_MAX_CONTEXT_CHARS` 内，超预算时显式标记
`truncated by LLM context budget`。

`LLMClient` 兼容字符串 content 和部分 OpenAI-compatible 网关返回的
content parts；HTTP 错误会保留状态码和服务端响应体，方便排查 key、
base URL、模型名和额度问题。

环境变量：

- `AEGIS_LLM_ENABLED`
- `AEGIS_LLM_API_KEY`
- `AEGIS_LLM_BASE_URL`
- `AEGIS_LLM_MODEL`
- `AEGIS_LLM_TIMEOUT_SECONDS`
- `AEGIS_LLM_MAX_CONTEXT_CHARS`

## env 配置

启动时 `main.py` 会读取当前目录 `.env`，再合并系统环境变量。命令行参数优先级最高。

基础变量：

- `AEGIS_REPO_PATH`
- `AEGIS_OUTPUT_DIR`
- `AEGIS_MAX_FILES`
- `AEGIS_USE_CACHE`
- `AEGIS_SERVE_DIR`
- `AEGIS_SERVE_HOST`
- `AEGIS_SERVE_PORT`

## RAG Context Pack

The RAG layer exposes a prompt-ready context pack for agents. Retrieval still
uses repository, file, symbol, interface, data, and CodeGraph chunks to decide
what matters, but the final package is file-first: it puts complete
line-numbered source files into context when the budget allows, then falls back
to focused source windows only for large files.

Context pack fields:

- `query`
- `max_chars` and `used_chars`
- `source_paths`, the real source files included in the prompt context
- `complete_file_paths`, files included as complete source files
- `target_context_paths`, files selected by retrieval or required by graph/path hints
- `target_context_budget_chars`, estimated budget for complete target-file source
- `missing_target_context_paths`, target files that did not fit into context
- `incomplete_target_context_paths`, target files present only as partial windows
- `target_context_satisfied`, `false` unless every target file is complete in context
- `required_context_budget_chars`, estimated budget for complete required-file source
- `blocks[*].path`
- `blocks[*].start_line` / `end_line`
- `blocks[*].content` with line-numbered source
- `blocks[*].context_mode`, usually `full_file` or `partial_file`
- `blocks[*].complete_file`, `true` when the whole file is in context
- `blocks[*].retrieved_from` to show which semantic chunk led to the source
- `required_context_paths`, paths that must be represented in source context
- `missing_required_context_paths`, required paths absent from source context
- `required_context_satisfied`, `false` when the prompt is not safe for LLM use
- `source_context_satisfied`, `true` only when real source file content is present
- `complete_file_context_satisfied`, `true` only when at least one complete file is present
- `context_safe_for_llm`, `true` only when the QA prompt is safe to send to an LLM
- `llm_skip_reason`, the reason a configured LLM was not called

For route questions, `RepositoryQAAgent` also emits `qa.graph_context` from
`CodeGraphQuery.trace_interface(route)`. Paths from that trace are passed to
the retriever as required context, so handler, service, repository, data, and
config files can be included in `qa.context_pack.source_paths` even when the
plain keyword score would not rank them high enough.

For non-route questions, RAG relation hits are also expanded through CodeGraph
node IDs before prompt packing. If retrieval matches a relation chunk such as
`A calls B`, both endpoint source files are promoted to target context and
packed as complete line-numbered files when the budget allows. This preserves
the semantic retrieval signal while ensuring the LLM receives the actual files
needed to reason about the relationship.

The QA agent also treats explicit file mentions as required context. A question
that names a real path, unique file name, or unique file stem forces that file
into the context pack before ordinary retrieval candidates are considered.
Unique CodeGraph symbol names in the question also become required context when
they resolve to exactly one source file, which lets class/function questions read
the defining file even when no path is mentioned.
Operators can also pass `--context-file <path>` with `--ask` or `--ready-ask`.
These files bypass ranking and become required context, which is useful when an
external agent already knows the file that must be read. If a forced file cannot
be packed as complete line-numbered source, the LLM path is disabled and the
payload reports the missing or incomplete required path.
If any required path is still missing because the context budget is too small,
the QA agent skips the LLM call and returns an offline diagnostic that asks the
caller to increase `--context-chars` or narrow the question.
The same rule applies to retrieval-selected target files: a file that was chosen
for reasoning must appear in `complete_file_paths`, otherwise
`target_context_satisfied=false` and the configured LLM is not called. In AEGIS,
semantic RAG selects files; prompt RAG supplies real file contents.
The QA agent also skips `--ask --llm` if retrieval produced only repository or
symbol metadata, or if the budget allowed partial source windows but no complete
file. This keeps LLM answers tied to actual file contents instead of summaries.

CLI and skill entrypoints expose the budget through `--context-chars`; `.env`
uses `AEGIS_RAG_CONTEXT_CHARS` and defaults to `48000` so agent prompts can
carry real files instead of only short snippets.

```powershell
python main.py --from-output output\aegis\sample_repo --ask "Where is user creation implemented?" --context-chars 24000 --json
```

The JSON response places this under `qa.context_pack` and records the assembled
prompt under `qa.llm_prompt`, so another agent can consume or replay the exact
code context directly.

Ask commands also write reusable artifacts:

- `qa_answer.json`: the stable QA payload, including `graph_context`,
  `context_pack`, retrieval results, evidence, and excerpts.
- `context_pack.md`: the prompt-ready CodeGraph and source context in a
  human-readable format.
- `llm_prompt.md`: the exact system/user prompt, including the rendered context
  pack, that would be sent to an OpenAI-compatible chat model.

These artifacts are included in `manifest.json` after an ask run.

## Change Impact Analysis

CodeGraph impact analysis starts from changed file nodes and walks reverse graph
edges to find upstream files, symbols, interfaces, modules, and data nodes that
depend on those files. The same `impacted_by_files(paths)` query powers report
summaries, CLI JSON, skill usage, and `impact.json`.

CLI:

```powershell
python main.py examples\sample_repo --impact --impact-file services/user_service.py --json
python main.py --from-output output\aegis\sample_repo --impact --impact-file services/user_service.py --json
```

When `--impact-file` is omitted, AEGIS uses `knowledge.changed_files`, which is
captured from `git diff --name-only HEAD` during analysis.

Output:

- `impact.input_paths`
- `impact.affected_files`
- `impact.affected_symbols`
- `impact.nodes`
- `output/aegis/<repo-name>/impact.json`

## Readiness Gate

Readiness assessment is the final quality wrapper around the analysis system.
It aggregates:

- Doctor environment checks
- Required output artifacts
- Repository knowledge health
- CodeGraph node/edge health
- RAG source context availability
- Evaluation score threshold

CLI:

```powershell
python main.py examples\sample_repo --ready --ready-fail-under 1.0 --json
python main.py --from-output output\aegis\sample_repo --ready --ready-fail-under 1.0 --json
python main.py examples\sample_repo --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json
python main.py examples\eda_repo --ready --ready-fail-under 1.0 --ready-ask "Where is the entrypoint?" --context-file src\timing\timing_model.py --json
```

Output:

- `readiness.passed`
- `readiness.checks`
- `readiness.summary`
- `output/aegis/<repo-name>/readiness.json`

`--ready-ask` adds a QA smoke question to the readiness run. It verifies that
`qa_answer.json`, `context_pack.md`, and `llm_prompt.md` were written and that
the context pack has complete-file source context with required and target paths
satisfied. Use `--context-file` with `--ready-ask` to force important files into
that QA smoke gate.

The command returns exit code `2` when readiness fails.

## Artifact Manifest

`manifest.json` is written for each analysis run and refreshed after post-run
commands such as evaluation, impact analysis, or readiness checks. It records:

- `schema_version`
- `aegis_version`
- repository name/root/git state
- run configuration
- optional `run.post_run` command context for ask, ready smoke, trace, impact, or evaluation commands
- repository, CodeGraph, RAG, and finding statistics
- artifact paths, existence, byte sizes, and SHA256 digests

For `--from-output` post-run commands, AEGIS preserves the original analysis
configuration in `run` and refreshes only `run.post_run` plus artifact hashes.

Readiness treats the manifest as a required artifact and verifies that it
matches the current repository analysis. It also verifies the recorded size and
SHA256 for required artifacts, so stale or modified outputs fail the gate.
