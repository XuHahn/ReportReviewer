# Online Cases Corpus

Local root: `/Users/ethan/Downloads/线上案件`

This corpus is split by folder prefix:

- `HC_`: high-confidence full document set for end-to-end regression.
- No prefix: unresolved case, usually because of multiple reports/order forms, missing files, legacy `.doc`, or unclear plan-vs-standard role.

The machine-readable manifest is `online_cases_high_confidence.json`.

Run local corpus regression with:

```bash
RUN_ONLINE_CASES=1 python -m pytest backend/tests/test_online_cases_corpus.py -q
```

Current full-pipeline cases:

- `HC_E202509103924`
- `HC_E202509246315`
- `HC_E202512042558`
- `HC_E202601060767`
- `HC_E202603254184`
- `HC_E202604213234`
- `HC_E202605287495`

Excluded from current training:

- `E202603161753`: no test plan; the PDF is a standard.
- `E202605097370`: no test plan; the PDF is a standard.

Important findings:

- `E202509103924` exposed the alias problem: plan uses `RF Conducted/Radiated Emissions`, while report/raw use `传导发射 CE` / `辐射发射 RE`.
- `E202509103924` is now used as confirmed alias evidence for:
  - `RF Conducted Emissions` <-> `传导发射 CE`
  - `RF Radiated Emissions` <-> `辐射发射 RE`
- PDF order forms are present in the corpus, but deterministic order-form extraction currently focuses on `.xls`. Cover comparison will need a PDF order-form extraction path.
