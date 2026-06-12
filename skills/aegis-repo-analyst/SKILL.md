---
name: aegis-repo-analyst
description: Analyze local code repositories with the AEGIS multi-agent repository analysis system. Use when Codex needs to inspect a codebase, generate architecture/interface/risk reports, build or query CodeGraph, create RAG context, trace an API route, or answer repository questions with file/line evidence using AEGIS.
---

# AEGIS Repo Analyst

## Overview

Use this skill to run AEGIS against a local repository and answer questions from its generated CodeGraph, Evidence Store, and RAG index.

AEGIS is located in the parent project that contains `main.py` and the `aegis/` package. It is offline-capable by default; optional LLM answering requires `AEGIS_LLM_*` environment variables.

## Workflow

1. Identify the target repository path.
2. Run AEGIS analysis.
3. Read the generated report or ask a RAG question.
4. Cite evidence from `report.md`, `knowledge.json`, `rag_index.json`, or CLI output.

Prefer the bundled script:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py analyze <repo-path>
```

Equivalent direct command from the AEGIS project root:

```powershell
python main.py <repo-path>
```

## Tasks

### Analyze A Repository

Before analysis, use doctor when setup or environment health is uncertain:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py doctor <repo-path>
python skills\aegis-repo-analyst\scripts\run_aegis.py doctor <repo-path> --json
```

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py analyze <repo-path> --max-files 1500
python skills\aegis-repo-analyst\scripts\run_aegis.py analyze <repo-path> --include "src/**/*.py" --exclude "*_test.py"
```

Outputs are written to:

```text
output/aegis/<repo-name>/
  knowledge.json
  rag_index.json
  findings.json
  events.json
  report.md
  report.html
  architecture.mmd
  manifest.json
  qa_answer.json
  context_pack.md
  llm_prompt.md
```

Summarize the important output paths for the user.

### Ask A Question With RAG

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ask <repo-path> "Where is user creation implemented and where is data written?"
```

Use this when the user asks repository questions such as:

- Where is this API implemented?
- What is the call chain for this feature?
- Which repository/model writes the data?
- What is the architecture of this repository?
- What are the risk hotspots?

The answer is evidence-first. If no LLM is configured, it returns retrieved chunks and file/line evidence. With `--llm`, AEGIS asks the configured text model to synthesize from the retrieved context.

RAG answers also include a prompt-ready `context_pack`. Downstream agents should
first inspect `qa.graph_context`, `qa.required_context_satisfied`, and
`qa.context_pack.target_context_satisfied`, then read
`qa.context_pack.blocks[*].content` because it contains real line-numbered
source files or focused source windows, not just summaries. Prefer blocks where
`complete_file=true`; `qa.context_pack.complete_file_paths` lists every whole
file packed into the prompt context. For route questions and explicit file
mentions, AEGIS uses required RAG context so downstream service/repository files
are placed into the prompt when budget allows. Ordinary retrieval-selected files
are listed in `qa.context_pack.target_context_paths` and must also appear in
`qa.context_pack.complete_file_paths` before an LLM answer is safe. If
`qa.missing_required_context_paths`, `qa.incomplete_required_context_paths`,
`qa.missing_target_context_paths`, or `qa.incomplete_target_context_paths` is
non-empty, do not ask an LLM to answer from that payload; increase the budget
with `--context-chars` or narrow the question until
`qa.required_context_satisfied=true` and `qa.target_context_satisfied=true`:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ask <repo-path> "Where is the entrypoint?" --context-chars 48000 --json
```

Each ask also writes `qa_answer.json`, `context_pack.md`, and `llm_prompt.md`
in the output directory. Prefer these files when another agent needs to reuse
the exact answer payload or replay the exact prompt-ready source context.

Use `--json` when another tool, evaluator, or UI needs a stable payload with retrieved chunks, evidence, matched terms, and source excerpts:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ask <repo-path> "Where is the entrypoint?" --json
```

For repeated questions, reuse existing artifacts without rescanning:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ask "Where is the entrypoint?" --from-output output\aegis\<repo-name> --json
```

`--from-output` verifies `manifest.json` before reuse, including schema,
repository identity, required artifact sizes, and SHA256 hashes. Ask/eval also
requires `rag_index.json` in that output directory. If integrity fails or an
artifact is missing/corrupt, rerun `analyze` instead of relying on an implicit
rebuild.

### Trace An Interface

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py trace <repo-path> /users
python skills\aegis-repo-analyst\scripts\run_aegis.py trace <repo-path> /users --json
python skills\aegis-repo-analyst\scripts\run_aegis.py trace /users --from-output output\aegis\<repo-name> --json
```

This uses CodeGraph `trace_interface(route)` to follow route -> handler -> file -> downstream imports/calls/data nodes.
Interface extraction covers common FastAPI/Flask-style decorators, Express
routers, Fastify route objects, Hono/Fastify-style method routes, NestJS
controllers, Spring mappings, Gin/chi-style method routes, ASP.NET `Http*`
attributes, Laravel `Route::*` declarations, and Next.js/SvelteKit file-based
route handlers.

### Analyze Change Impact

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py impact <repo-path> --path services/user_service.py --json
python skills\aegis-repo-analyst\scripts\run_aegis.py impact --from-output output\aegis\<repo-name> --path services/user_service.py --json
```

Use this when the user asks what a changed file may affect. If `--path` is not
provided, AEGIS uses the Git diff files recorded in `knowledge.changed_files`.
The JSON payload contains `impact.affected_files`, `impact.affected_symbols`,
and all impacted `nodes`. Results are also written to `impact.json`.

### Evaluate Retrieval And Trace Quality

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path>
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --json
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --suite suite.json --json
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --fail-under 0.9
```

Use this before claiming the system is ready. The evaluation reports RAG recall, CodeGraph trace success, source context coverage, prompt context coverage, complete-file context coverage, expected-path prompt coverage, expected-path complete-file coverage, and an overall score. Results are also written to `output/aegis/<repo-name>/evaluation.json`. `--fail-under` turns the score into a hard quality gate for CI or competition scripts.

### Run Readiness Gate

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ready <repo-path> --fail-under 1.0 --json
python skills\aegis-repo-analyst\scripts\run_aegis.py ready --from-output output\aegis\<repo-name> --fail-under 1.0 --json
python skills\aegis-repo-analyst\scripts\run_aegis.py ready <repo-path> --fail-under 1.0 --ask "POST /users call chain" --json
```

Use this before demos or submissions. It aggregates doctor checks, required
artifacts, knowledge/CodeGraph/RAG health, and evaluation score into
`readiness.json`. Add `--ask` to also verify QA artifacts, prompt-ready
complete-file context, and required/target-context safety. Treat
`readiness.passed=false` as not ready.

AEGIS also writes `manifest.json` for each run. Use it to verify the AEGIS
version, run configuration, repository identity, summary stats, and artifact
inventory for a delivered analysis. Artifact entries include byte sizes and
SHA256 hashes; readiness verifies required artifact integrity.

### Serve HTML Report

```powershell
python main.py --serve output\aegis\<repo-name>
```

Then open:

```text
http://127.0.0.1:8765/report.html
```

## Environment

AEGIS loads `.env` from the current project root. CLI arguments override env values.

Core variables:

```env
AEGIS_REPO_PATH=examples/sample_repo
AEGIS_OUTPUT_DIR=output/aegis
AEGIS_MAX_FILES=1500
AEGIS_RAG_CONTEXT_CHARS=48000
AEGIS_USE_CACHE=true
```

Optional text LLM variables:

```env
AEGIS_LLM_ENABLED=true
AEGIS_LLM_API_KEY=your-key
AEGIS_LLM_BASE_URL=https://api.openai.com/v1
AEGIS_LLM_MODEL=gpt-4o-mini
AEGIS_LLM_TIMEOUT_SECONDS=120
AEGIS_LLM_MAX_CONTEXT_CHARS=14000
```

Do not confuse these with image-generation variables such as `MM_IMAGE_API_KEY`; AEGIS RAG Q&A uses text chat completions.
The skill wrapper accepts `--llm` on commands that may perform analysis or ask
questions, and passes it through to the main AEGIS CLI.
When `--llm` is used for full analysis, the optional LLM analyst receives
source-backed ContextRouter sections with line-numbered code and
`Complete file: yes/no` markers, bounded by `AEGIS_LLM_MAX_CONTEXT_CHARS`.
Run `doctor --llm --json` before a demo to validate the local text LLM
configuration. It checks that the key is present, the base URL is an absolute
`http(s)` URL, the model is not empty, timeout is positive, and the context
budget is not too small. It does not make a network request.

## Evidence Discipline

When answering the user:

- Prefer claims backed by file paths and line numbers.
- Mention whether the answer came from offline RAG or LLM synthesis.
- Check `qa.required_context_satisfied`; if false, report missing or incomplete files and ask for a larger context budget.
- If retrieval is weak, say what evidence is missing.
- For route questions, include the CodeGraph trace when available.
- For architecture questions, cite `report.md` sections and `knowledge.json`/`rag_index.json` when useful.

## References

Read `references/aegis-outputs.md` if you need the output schema and common files.
