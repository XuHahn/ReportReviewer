# Extraction Debug Logging Requirements

> Status: active observability contract; reviewed against the extraction pipeline on 2026-07-16.

## Background

During investigation of `EMC-20260709-apjf8u`, the current `logs/app.jsonl`
did not contain the active document-set extraction failure. The database kept
the final `extraction_error`, but the request-level and extraction-stage logs
were not sufficient to reconstruct the full failure path.

## Status

Implemented on 2026-07-10.

## Implemented Events

- `document_extract_start`: include `set_id`, `doc_id`, `doc_type`, `filename`,
  `file_size_kb`, `version`, `employee_id`.
- `document_extract_done`: include `set_id`, `doc_id`, `doc_type`, extracted
  item counts, quality, duration, and warning count.
- `document_extract_failed`: include `set_id`, `doc_id`, `doc_type`, sanitized
  error message, duration, retryable flag, and extraction stage.
- `document_extract_retry`: include retry count, previous error, and next action.
- `llm_extract_response_summary`: include model, token budget, elapsed time,
  empty-content flag, parse-repair flag, and top-level JSON keys only.

## Persistence

- Per-document metrics are stored in `set_documents.extraction_meta`.
- The single-document extraction API returns the same object as
  `extraction_meta`.
- LLM response summaries are emitted to application logs only and intentionally
  do not store raw document text or raw model output.

## Notes

- Do not log raw customer document content.
- Keep source text, file bytes, customer names, device IDs, phone numbers, and
  emails out of logs.
- Bind the existing request `reqId` and document `set_id` to all extraction
  events so a failed upload can be traced from frontend action to stored error.

## Gap Found On 2026-07-10

During investigation of `EMC-20260710-3au951`, the logs were enough to see:

- report-driven modules built: `瞬态抗扰度试验`, `系统12V电源电压波动试验`
- raw-record modules built with the same two keys
- deterministic issue count before/after legacy supplement
- LLM raw response head for `R10-NAME:瞬态抗扰度试验`

However, the logs did not persist the sanitized module evidence that was sent
to the module-level LLM audit. The false positive was caused by a report module
context containing only table-of-contents lines instead of the structured
result row evidence. Reconstructing that required joining `backend/review.db`
`__structured__` rows with application logs.

Recommended follow-up logging:

- `module_audit_context_summary`: `set_id`, `module_key`, report/raw/plan text
  lengths, included evidence sections, SHA-256 hash of each evidence block, and
  first 120 sanitized characters of headings only.
- `module_audit_issue_summary`: issue count, issue categories, field names, and
  source labels returned by the LLM.
- `legacy_issue_filter_summary`: number of legacy issues dropped by reason
  (`coverage_owned_by_report_driven`, `toc_owned_by_report_driven`,
  `cross_doc_owned_by_report_driven`, duplicate key).
