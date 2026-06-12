# AEGIS Demo Results

This file records a real local run of the current repository state and can be
used as a compact evidence bundle for reviews, demos, and release notes.

Run date:

- 2026-06-13

Workspace:

- `examples/sample_repo`
- output directory: `output/demo_release/sample_repo`

## Commands Executed

```powershell
python main.py examples/sample_repo --out output/demo_release --max-files 100 --no-cache --doctor --json
python main.py examples/sample_repo --out output/demo_release --max-files 100 --no-cache --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json
```

## Doctor Result

- `passed=true`
- `errors=0`
- `warnings=0`
- Python: supported
- repository path: readable
- output directory: writable
- git: available
- LLM mode: disabled, offline pipeline available

## Ready Result

- repository: `sample_repo`
- final status: `ready`
- readiness passed: `true`
- readiness errors: `0`
- readiness warnings: `0`
- evaluation overall score: `1.0`
- RAG recall: `1.0`
- trace success rate: `1.0`
- prompt context coverage: `1.0`
- complete-file context coverage: `1.0`

## CodeGraph / RAG Summary

- scanned files: `4`
- total lines: `36`
- frameworks: `FastAPI`
- entrypoints: `app.py`
- CodeGraph nodes: `21`
- CodeGraph edges: `38`
- RAG chunks: `33`

## Handoff Summary

From `output/demo_release/sample_repo/handoff_card.json`:

- `status=ready`
- `recommended_action.command=none`
- `primary_task.source=qa`
- `primary_task.recommended_command=ask`
- `qa.context_safe_for_llm=true`
- `readiness.passed=true`
- reusable commands:
  - `trace`
  - `impact`
  - `ask`
  - `eval`
  - `ready`

Primary task replay command:

```powershell
python main.py --from-output output/demo_release/sample_repo --ask "POST /users call chain" --json --context-chars 2269 --context-file app.py --context-file services/user_service.py --context-file pyproject.toml --context-file repositories/user_repository.py
```

## Produced Artifacts

Observed in `output/demo_release/sample_repo/`:

- `knowledge.json`
- `findings.json`
- `rag_index.json`
- `report.md`
- `report.html`
- `architecture.mmd`
- `manifest.json`
- `evaluation.json`
- `readiness.json`
- `qa_answer.json`
- `context_pack.md`
- `llm_prompt.md`
- `run_summary.json`
- `handoff_card.json`

## Notes

- This run used the offline path; no external LLM was required.
- `impact.json` was not produced because no impact analysis command was run.
- The handoff card contains a replayable QA command with explicit context files,
  which is the current recommended downstream-agent entrypoint behavior.
