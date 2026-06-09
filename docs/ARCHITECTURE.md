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

CLI 示例：

```powershell
python main.py examples\sample_repo --trace-interface /users
```

## RAG

RAG 层把 CodeGraph 与源码证据转换成 Agent 可读的检索块：

- `repo_overview`
- `file`
- `class`
- `function`
- `interface`
- `data_model`
- `edge:*`

问答流程：

1. `RAGIndexBuilder` 从 `RepoKnowledge` 构建 `rag_index.json`。
2. `RAGRetriever` 使用离线 BM25/关键词检索返回证据 chunk。
3. `RepositoryQAAgent` 在无 LLM 时输出证据式回答；有 LLM 时把上下文交给模型生成自然语言回答。

CLI 示例：

```powershell
python main.py examples\sample_repo --ask "用户创建接口在哪里，数据写入哪里？"
```

## LLM 接入

`LLMRepositoryAnalyst` 是可选 Agent。启用后：

1. `ContextRouter` 选择最小必要上下文。
2. `LLMClient` 调用 OpenAI 兼容 `/chat/completions`。
3. LLM 输出作为 Finding 进入 Evidence Reviewer。

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
