# AEGIS Demo Flow

This document is the shortest end-to-end demo path for AEGIS in a competition,
review, or repository handoff setting.

## Goal

Show that AEGIS can:

1. validate the environment,
2. analyze a repository,
3. answer repository questions with real source context,
4. expose a stable machine handoff interface, and
5. prove readiness with explicit gates.

## Demo 1: Fastest Happy Path

Use the built-in sample repository:

```powershell
python main.py examples\sample_repo --doctor --json
python main.py examples\sample_repo --max-files 100 --no-cache --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json
python main.py --from-output output\aegis\sample_repo --handoff --json
```

What this demonstrates:

- `--doctor`: local environment and optional LLM configuration can be checked without a full run.
- `--ready`: analysis, QA, evaluation, and readiness gates can complete in one reproducible command.
- `--handoff --json`: downstream agents can continue from a compact trusted interface instead of rescanning.

## Demo 2: Agent-to-Agent Handoff

Run analysis and let the next agent continue from the output directory:

```powershell
python main.py examples\sample_repo --max-files 100 --no-cache
python main.py --from-output output\aegis\sample_repo --status --json
python main.py --from-output output\aegis\sample_repo --handoff --json
```

Inspect these fields first:

- `handoff_card.primary_task.recommended_command_line`
- `handoff_card.primary_task.investigation_brief`
- `status_report.manifest_integrity.ok`
- `status_report.handoff_card_artifact.trusted`
- `status_report.reuse_by_command.can_reuse_for`

If `primary_task.recommended_command_line` is present, use it as the default
next step.

## Demo 3: Source-Grounded RAG

Ask a repository question and inspect the packed code context:

```powershell
python main.py examples\sample_repo --ask "Where is user creation implemented?" --json
python main.py --from-output output\aegis\sample_repo --ask "Explain the /users call chain" --json
```

Success signals:

- `qa.context_safe_for_llm` reflects whether the prompt is safe for model use.
- `qa.context_pack.blocks[*].content` contains real line-numbered source.
- `qa.required_context_satisfied` and `qa.target_context_satisfied` are both `true` before trusting LLM synthesis.

## Demo 4: EDA Repository Evaluation

Use the larger built-in EDA sample to show retrieval and evaluation behavior:

```powershell
python main.py examples\eda_repo --max-files 100 --no-cache --eval --eval-fail-under 1.0 --json
python main.py examples\eda_repo --ask "Where is the entrypoint?" --context-file src\timing\timing_model.py --json
```

## Artifacts To Show

After a successful run, these files are usually enough for a review:

- `report.md`
- `report.html`
- `knowledge.json`
- `rag_index.json`
- `manifest.json`
- `run_summary.json`
- `handoff_card.json`

For QA and replayable prompt inspection:

- `qa_answer.json`
- `context_pack.md`
- `llm_prompt.md`

## Acceptance Checklist

The demo is in a strong state when all of the following are true:

- `python -m unittest discover -s tests -v` passes.
- `--doctor --json` returns `passed=true` for the demo repository.
- `--ready --ready-fail-under 1.0` returns exit code `0`.
- `handoff_card.json` exists and `status_report.handoff_card_validation.ok` is `true`.
- `run_summary.json` includes a non-empty `orchestration` block.
- `manifest.json` integrity checks pass during `--from-output` reuse.

## Suggested Live Narration

Use this order during a live walkthrough:

1. Show `--doctor` to establish environment health.
2. Run `--ready` once to prove the integrated pipeline.
3. Open `report.html` for human review.
4. Show `--handoff --json` and point to `primary_task.recommended_command_line`.
5. Ask one targeted repository question and open `context_pack.md`.
