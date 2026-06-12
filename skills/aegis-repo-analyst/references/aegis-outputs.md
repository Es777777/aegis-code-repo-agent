# AEGIS Output Reference

Generated directory:

```text
output/aegis/<repo-name>/
```

Files:

- `knowledge.json`: repository facts, `code_graph`, `dependency_graph`, `call_graph`, `interface_catalog`, `evidence_store`, stats.
- `rag_index.json`: retrieval chunks used by the QA agent.
- `findings.json`: findings produced by specialist agents and reviewers.
- `events.json`: workflow event log.
- `report.md`: human-readable report.
- `report.html`: browsable report.
- `architecture.mmd`: Mermaid diagram.
- `impact.json`: optional CodeGraph impact analysis output created by `--impact`.
- `readiness.json`: optional readiness gate output created by `--ready`.
- `qa_answer.json`: optional ask payload created by `--ask`.
- `context_pack.md`: optional prompt-ready source context created by `--ask`.
- `llm_prompt.md`: optional exact system/user prompt created by `--ask`.
- `manifest.json`: analysis run manifest with version, config, stats, and artifact inventory.
- `run_summary.json`: compact downstream-agent handoff with status, artifact availability, QA/RAG safety, evaluation, readiness, impact, and next actions.

CodeGraph node kinds:

- `file`
- `module`
- `class`
- `function`
- `interface`
- `config`
- `data_model`
- `external_module`

CodeGraph edge kinds:

- `contains_file`
- `defines`
- `defined_in`
- `imports`
- `calls`
- `calls_file`
- `exposes`
- `routes_to`
- `declared_in`
- `configured_by`
- `defines_data`

Useful CLI forms:

```powershell
python main.py <repo-path>
python main.py <repo-path> --trace-interface /users
python main.py <repo-path> --impact --impact-file services/user_service.py --json
python main.py <repo-path> --ready --ready-fail-under 1.0 --json
python main.py <repo-path> --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json
python main.py <repo-path> --ask "Where is user creation implemented?"
python main.py <repo-path> --ask "Where is the entrypoint?" --context-file src/timing/timing_model.py --json
python main.py <repo-path> --ask "Explain /users" --llm
```

Ask JSON fields:

- `qa.graph_context`: CodeGraph route/call-chain trace when a route is detected.
- `qa.required_context_paths`: CodeGraph trace paths, explicit file mentions, and `--context-file` paths forced into prompt context.
- `qa.target_context_paths`: retrieval-selected and required files that should be complete in prompt context.
- `qa.context_pack.required_context_budget_chars`: estimated budget for complete required-file source.
- `qa.context_pack.target_context_budget_chars`: estimated budget for complete target-file source.
- `qa.missing_target_context_paths`: target files absent from prompt context because of budget or missing source.
- `qa.incomplete_target_context_paths`: target files present only as partial source windows.
- `qa.unsatisfied_target_context_paths`: target files absent from prompt context or present only partially.
- `qa.target_context_satisfied`: whether every target file is present as complete line-numbered source.
- `qa.missing_required_context_paths`: required paths absent from prompt context because of budget or missing source.
- `qa.incomplete_required_context_paths`: required paths present only as partial source windows.
- `qa.unsatisfied_required_context_paths`: required paths absent from prompt context or present only partially.
- `qa.required_context_satisfied`: whether every required file is present as complete line-numbered source.
- `qa.source_context_satisfied`: whether real source file content entered the prompt context.
- `qa.complete_file_context_satisfied`: whether at least one complete source file entered the prompt context.
- `qa.context_safe_for_llm`: whether it is safe to let an LLM answer from this prompt.
- `qa.llm_skip_reason`: why AEGIS skipped a configured LLM when context was unsafe.
- `qa.llm_prompt`: exact system/user prompt assembled for an OpenAI-compatible chat model.
- `qa.context_pack.source_paths`: real source files included in the prompt context.
- `qa.context_pack.complete_file_paths`: files included as complete line-numbered source.
- `qa.context_pack.required_context_satisfied`: same contract at context-pack level.
- `qa.context_pack.target_context_satisfied`: same target-file contract at context-pack level.
- `qa.context_pack.source_context_satisfied`: same source-content contract at context-pack level.
- `qa.context_pack.complete_file_context_satisfied`: same complete-file contract at context-pack level.
- `qa.context_pack.blocks[*].content`: line-numbered source code for the LLM.
- `qa.context_pack.blocks[*].complete_file`: `true` when the block contains the whole file.
- `qa.results[*].source_excerpt`: short evidence excerpts for display or citations.

Evaluation JSON fields:

- `evaluation.metrics.rag_recall`: retrieval hit rate for expected files.
- `evaluation.metrics.trace_success_rate`: CodeGraph route trace success rate.
- `evaluation.metrics.source_context_coverage`: whether retrieved results have source companions.
- `evaluation.metrics.prompt_context_coverage`: whether prompt-ready source context was built.
- `evaluation.metrics.prompt_context_expected_path_coverage`: expected files included in prompt context.
- `evaluation.metrics.complete_file_context_coverage`: whether complete-file source context was built.
- `evaluation.metrics.complete_file_expected_path_coverage`: expected files included as complete files.
- `evaluation.metrics.overall_score`: weighted quality score used by `--eval-fail-under`.
- `evaluation.rag[*].prompt_context_matched_paths`: expected paths present in the prompt context.
- `evaluation.rag[*].complete_file_matched_paths`: expected paths present as complete files.
- `evaluation.rag[*].required_context_paths`: CodeGraph trace paths forced into context for route questions.

Impact JSON fields:

- `source`: `explicit` or `git_diff`.
- `input_paths`: files used as impact roots.
- `affected_files`: unique file paths reached by reverse CodeGraph traversal.
- `affected_symbols`: affected class/function/interface/data nodes.
- `nodes`: full impacted CodeGraph node payloads.

Readiness JSON fields:

- `passed`: final readiness verdict.
- `threshold`: evaluation score threshold used by the gate.
- `checks`: named checks with `ok`, `warning`, or `error` status.
- `checks[name=qa]`: QA smoke status when `--ready-ask` was provided.
- `checks[*].detail`: includes evaluation coverage metrics for the evaluation check.
- `summary`: names grouped by status.

Manifest JSON fields:

- `schema_version`: manifest schema version.
- `aegis_version`: installed AEGIS package version.
- `repo`: analyzed repository name, root, and git state when available.
- `run`: scan and LLM configuration used for the analysis.
- `run.post_run`: ask/ready/eval/trace/impact command context when post-run artifacts were produced.
- `stats`: file, CodeGraph, RAG, and finding summaries.
- `artifacts`: artifact paths, existence, byte sizes, and SHA256 digests.
