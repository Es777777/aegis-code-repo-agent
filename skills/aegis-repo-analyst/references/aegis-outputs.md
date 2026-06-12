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
- `manifest.json`: analysis run manifest with version, config, stats, and artifact inventory.

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
python main.py <repo-path> --ask "Where is user creation implemented?"
python main.py <repo-path> --ask "Explain /users" --llm
```

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
- `summary`: names grouped by status.

Manifest JSON fields:

- `schema_version`: manifest schema version.
- `aegis_version`: installed AEGIS package version.
- `repo`: analyzed repository name, root, and git state when available.
- `run`: scan and LLM configuration used for the analysis.
- `stats`: file, CodeGraph, RAG, and finding summaries.
- `artifacts`: artifact paths, existence, and byte sizes.
