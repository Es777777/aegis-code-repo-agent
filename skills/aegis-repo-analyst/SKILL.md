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

Use `--json` when another tool, evaluator, or UI needs a stable payload with retrieved chunks, evidence, matched terms, and source excerpts:

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py ask <repo-path> "Where is the entrypoint?" --json
```

### Trace An Interface

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py trace <repo-path> /users
python skills\aegis-repo-analyst\scripts\run_aegis.py trace <repo-path> /users --json
```

This uses CodeGraph `trace_interface(route)` to follow route -> handler -> file -> downstream imports/calls/data nodes.

### Evaluate Retrieval And Trace Quality

```powershell
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path>
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --json
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --suite suite.json --json
python skills\aegis-repo-analyst\scripts\run_aegis.py eval <repo-path> --fail-under 0.9
```

Use this before claiming the system is ready. The evaluation reports RAG recall, CodeGraph trace success, source context coverage, and an overall score. Results are also written to `output/aegis/<repo-name>/evaluation.json`. `--fail-under` turns the score into a hard quality gate for CI or competition scripts.

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

## Evidence Discipline

When answering the user:

- Prefer claims backed by file paths and line numbers.
- Mention whether the answer came from offline RAG or LLM synthesis.
- If retrieval is weak, say what evidence is missing.
- For route questions, include the CodeGraph trace when available.
- For architecture questions, cite `report.md` sections and `knowledge.json`/`rag_index.json` when useful.

## References

Read `references/aegis-outputs.md` if you need the output schema and common files.
