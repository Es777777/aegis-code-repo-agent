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
- `handoff_card.json`: unified downstream-agent task card with primary action, reusable evidence pointers, and the best available investigation brief.

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
python main.py --from-output output\aegis\<repo-name> --status --json
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
- `qa.investigation_brief`: structured reading brief for downstream agents, including required files, supporting files, reading order, and guardrails.
- `qa.required_context_paths`: CodeGraph trace paths, explicit file mentions, and `--context-file` paths forced into prompt context.
- `qa.target_context_paths`: retrieval-selected and required files that should be complete in prompt context.
- `qa.supporting_context_paths`: target files that are useful but not mandatory evidence roots.
- `qa.reading_order`: prioritized required/supporting file groups another agent should read in order.
- `qa.context_pack.required_context_budget_chars`: estimated budget for complete required-file source.
- `qa.context_pack.target_context_budget_chars`: estimated budget for complete target-file source.
- `qa.context_pack.supporting_context_paths`: same supporting-file split at context-pack level.
- `qa.context_pack.reading_order`: same prioritized file groups at context-pack level.
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

Status JSON fields:

- `summary`: trusted `run_summary.json` when manifest integrity passes, otherwise a rebuilt live summary.
- `handoff_card`: trusted `handoff_card.json` when present, otherwise a rebuilt live handoff card.
- `handoff_card.primary_task.recommended_args`: replayable CLI args for the current primary task. QA, repair-plan, and summary-driven tasks all expose the same machine-friendly field.
- `handoff_card.primary_task.recommended_command_line`: ready-to-run CLI command for the current primary task; QA tasks include the resolved `--from-output`, `--context-chars`, and `--context-file` contract when available.
- `orchestration`: top-level echo of `summary.orchestration`.
- `orchestration.repair_ready`: whether structured remediation steps are available.
- `orchestration.blocking_issues_count`: number of current blocking remediation items.
- `orchestration.primary_repair_step`: first remediation item another agent should execute.
- `orchestration.repair_plan[*]`: ordered remediation steps with category, severity, affected commands, and suggested CLI command.
- `orchestration.repair_plan[*].failing_rag_cases`: failing evaluation questions with missing expected/prompt/complete-file paths when the evaluation gate failed.
- `orchestration.repair_plan[*].failing_trace_cases`: failing trace cases when the evaluation gate failed on route tracing.
- `orchestration.repair_plan[*].failing_checks`: readiness checks still in `warning` or `error` when the readiness gate failed.
- `orchestration.repair_plan[*].investigation_brief`: structured remediation card for downstream agents, including question, focus files, reading order, guardrails, and failure evidence.
- `orchestration.repair_plan[*].focus_paths`: likely source files that should be inspected first.
- `orchestration.repair_plan[*].suggested_context_files`: paths that can be passed back into `--context-file`.
- `orchestration.repair_plan[*].suggested_context_chars`: suggested `--context-chars` budget for the investigation ask command, estimated from saved source chunks in `rag_index.json` when available and falling back to heuristics otherwise.
- `orchestration.repair_plan[*].investigation_command_line`: a ready-to-run ask command for investigating the failing area.
- `status_report.manifest_integrity`: current manifest/hash verification result for the output directory.
- `status_report.run_summary_artifact`: whether `run_summary.json` exists, is readable, and is trusted.
- `status_report.handoff_card_artifact`: whether `handoff_card.json` exists and is trusted under current manifest integrity.
- `status_report.handoff_card_validation`: schema and required-field validation result for `handoff_card.json`.
- `status_report.artifact_contracts`: artifact dependency-contract summary.
- `status_report.repair_plan`: status-level echo of the current remediation steps.
- `status_report.primary_repair_step`: status-level echo of the first remediation step.
- `status_report.primary_repair_step.investigation_brief`: status-level echo of the structured remediation card another agent can execute directly.
- `status_report.reuse_by_command.can_reuse_for`: commands that can safely use `--from-output` now.
- `status_report.reuse_by_command.blocked_by`: commands currently blocked by missing roots or dangling artifacts.
