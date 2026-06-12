# Public Repo Copy

This file contains ready-to-use public-facing copy for the AEGIS repository.

## Short Description

AEGIS 2.0: multi-agent code repository analysis system with CodeGraph,
source-grounded RAG, and agent handoff interfaces for the Volcano Cup
intelligent agent competition.

## Repo Introduction

AEGIS turns a source repository into a reusable machine-readable knowledge
layer, then lets multiple agents collaborate on architecture reading, interface
tracing, implementation inspection, risk review, and repository Q&A.

It is designed for competition and real engineering workflows:

- offline-first analysis,
- CodeGraph-based route and dependency tracing,
- source-grounded RAG with real line-numbered code,
- artifact reuse via `--from-output`,
- downstream agent handoff through `handoff_card.json`,
- evaluation and readiness gates for repeatable demos.

## Suggested GitHub About Text

Multi-agent repository analysis for Volcano Cup. CodeGraph, real-source RAG,
handoff cards, evaluation gates, and offline-first repo QA.

## Suggested Topics

- multi-agent
- code-analysis
- repository-analysis
- codegraph
- rag
- agentic-rag
- developer-tools
- openai-compatible
- volcano-cup

## Suggested Release Title

AEGIS 2.0 competition-ready baseline

## Suggested Release Notes

### Highlights

- Added a stable `handoff_card.json` machine interface for downstream agents.
- Upgraded RAG to pack real line-numbered source files into prompt context.
- Integrated CodeGraph route tracing and impact analysis into the agent workflow.
- Added `status` and `handoff` commands for reuse-safe artifact inspection.
- Added artifact contracts and `--from-output` validation gates.
- Added readiness, evaluation, doctor, and demo/release documentation.

### Demo-Ready Workflow

```powershell
python main.py examples\sample_repo --doctor --json
python main.py examples\sample_repo --max-files 100 --no-cache --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json
python main.py --from-output output\aegis\sample_repo --handoff --json
```

### Validation

- Full test suite passes locally.
- Built-in sample repositories pass evaluation and readiness gates.
- Demo artifacts and release checklist are included in `docs/`.
