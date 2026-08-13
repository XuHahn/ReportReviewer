# Pipeline Observability Requirements

> Status: active pipeline logging contract; reviewed on 2026-07-16.

## Context

End-to-end run set: `EMC-20260708-m3aa88`.

The current logs are enough to locate slow and failed stages, but several failures are not visible in document status or saved validation issues. A user can see `done` while a report extraction pass or module LLM audit has failed internally.

## Status

Implemented on 2026-07-10.

## Implemented Runtime Signals

- Persist per-document extraction metrics:
  - `doc_type`, `doc_id`, `duration_ms`, `text_length`
  - `extraction_quality`: `complete`, `partial`, `failed`
  - `failed_passes`, `json_parse_retries`, `empty_response_retries`
- Persist report extraction module metrics:
  - module code/name
  - input length, output length
  - retry count
  - parse status
  - extracted rows/counts
- Persist module audit metrics:
  - check id, module id
  - context length
  - duration
  - retry count
  - final parse status
  - issue count
- Emit a system validation issue when a module audit or extraction pass fails after retries.
- Surface partial extraction state through the document-set API instead of only writing it to logs.
- Add progress events suitable for frontend polling:
  - uploaded
  - extracting text
  - extracting structure
  - waiting for model
  - validating
  - saving issues
  - completed or partial

## Observed Gaps

- `final_report` was marked `done` even though `item/EQ/MR02` failed after all retries.
- `R10-EQMC04` failed JSON parsing after four attempts and produced no saved issue about the audit failure.
- Very large module contexts, such as `EQ/IR01` and `EQ/MR01`, caused long waits or empty model responses, but the API did not expose the specific blocking module while running.
- Raw-record table extraction emitted many `empty_table` warnings, but those warnings are not summarized in the final review result.
- Instrument serial parsing warnings include software/tool rows and create noise without a clear classification.

## Frontend Expectations

- Show per-file extraction quality, not just `done`.
- Show currently running module during review.
- Show partial result warnings before the user opens the final issue list.
- Distinguish deterministic issues, LLM semantic issues, and system reliability issues.

## Implementation Notes

- Per-document extraction metrics are stored in `set_documents.extraction_meta`.
- Pipeline/module metrics are stored in `pipeline_run_metrics`.
- Latest metrics are exposed by `GET /api/sets/{set_id}/metrics`.
- The reviewer UI displays a compact "运行观测" panel with metric count,
  module audit count, failed stage count, cumulative duration, and the slowest
  recorded stages.

## Follow-Up Enhancements

- The current UI shows a compact observability summary; a future admin page can
  expose the full metric table with filters by `run_id`, `metric_type`, and
  `module_key`.
- Retry counters are persisted at the extraction orchestration layer. Provider
  internals such as JSON-repair attempt counts are currently logged, while the
  persisted counters stay conservative unless the provider exposes exact values.

## Rule Input Accountability Gap Found On 2026-08-03

During investigation of graph `graph-71ce739f1b6944f9b305`, the saved rule
execution audit only exposed `input_count`, `finding_count`, and a derived
`not_applicable` state. It could not explain why a rule had no input. The same
state therefore covered four materially different conditions:

- the rule was proven not applicable to the reviewed scope;
- a required source field was visibly absent from the submitted document;
- structured extraction or source anchoring was incomplete;
- the current adapter did not map available structured data into the rule.

The structured result bridge also did not persist its expected-versus-anchored
row count per rule. Multiple identical verdicts on the same page could collapse
to one observation while the run still displayed the affected rule as passed.

Required runtime and persisted signals:

- extend every rule audit item with `state`, `reason_code`, `applicability`,
  `expected_input_count`, `anchored_input_count`, and affected document types;
- use distinct states for `passed`, `issues_found`, `not_applicable`,
  `source_missing`, and `system_incomplete`;
- log `rule_input_audit` once per rule without customer text, including only
  counts, field identifiers, document types, hashes, and reason codes;
- log and persist `structured_bridge_coverage` per observation family with
  expected, anchored, rejected, and deduplicated row counts;
- log every decision to reuse a partial structured extraction with its sanitized
  quality-gate reasons and the generic checks that depend on that reuse;
- force the graph run to `machine_incomplete` whenever a required rule is in
  `source_missing` or `system_incomplete`; only evidence-backed applicability
  gates may produce `not_applicable`.

No raw source text, prompts, customer identifiers, or complete model responses
may be written to these events.
