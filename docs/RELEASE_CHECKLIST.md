# AEGIS Release Checklist

Use this checklist before publishing the repository, tagging a release, or
submitting AEGIS to a competition.

## Product Integrity

- [ ] `python -m unittest discover -s tests -v`
- [ ] `python -m compileall aegis main.py tests skills/aegis-repo-analyst/scripts/run_aegis.py`
- [ ] `git diff --check`
- [ ] `python main.py examples\sample_repo --doctor --json`
- [ ] `python main.py examples\sample_repo --max-files 100 --no-cache --ready --ready-fail-under 1.0 --ready-ask "POST /users call chain" --json`
- [ ] `python main.py --from-output output\aegis\sample_repo --handoff --json`

## Artifact Contract Checks

- [ ] `manifest.json` is written and passes `--from-output` integrity validation.
- [ ] `run_summary.json` includes `artifact_contracts` and `orchestration`.
- [ ] `handoff_card.json` includes a valid `primary_task`.
- [ ] `handoff_card.primary_task.recommended_command_line` is present when a concrete next action exists.
- [ ] `status_report.handoff_card_validation.ok` is `true`.

## Documentation

- [ ] `README.md` explains Quick Start, env setup, `doctor`, `ready`, `status`, and `handoff`.
- [ ] `README.md` points downstream agents to `handoff_card.json`.
- [ ] `skills/aegis-repo-analyst/SKILL.md` matches the current CLI surface.
- [ ] `docs/ARCHITECTURE.md` still reflects the implemented architecture.
- [ ] `docs/DEMO.md` matches the current recommended walkthrough.
- [ ] `docs/DEMO_RESULTS.md` is refreshed if the demo flow or expected outputs changed.

## Packaging

- [ ] `pyproject.toml` version matches `aegis.__version__`.
- [ ] `LICENSE` exists and matches the declared license.
- [ ] `python -m pip install -e .` succeeds.
- [ ] `aegis --help` succeeds after editable install.

## Competition Readiness

- [ ] Built-in sample repositories run without hidden external dependencies.
- [ ] Offline RAG behavior works without any LLM configuration.
- [ ] Optional LLM configuration is documented as OpenAI-compatible.
- [ ] `--doctor --llm` can validate configuration before a live demo.
- [ ] The default demo flow can be completed from a clean checkout.

## Recommended Evidence Bundle

For a competition submission or public release note, capture:

- [ ] exact commands used,
- [ ] final test result summary,
- [ ] one `report.html` screenshot or exported excerpt,
- [ ] one `handoff --json` payload excerpt,
- [ ] one `ask --json` payload excerpt showing real source context,
- [ ] one `ready --json` success payload.
