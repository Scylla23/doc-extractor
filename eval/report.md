# Invoice Extraction — Accuracy Report

Evaluated **48/50** docs (2 failed extraction and were excluded) — mix: 30 clean-native, 10 scanned, 10 edge.
Field-level metrics are micro-averaged per field across the whole set (PRD §10), normalized before comparison (dates→ISO, amounts→decimal).

## Per-field precision / recall / F1

| Field | Precision | Recall | F1 | n |
|---|---|---|---|---|
| vendor_name | 1.00 | 1.00 | 1.00 | 48 |
| invoice_number | 1.00 | 1.00 | 1.00 | 47 |
| invoice_date | 1.00 | 1.00 | 1.00 | 47 |
| currency | 1.00 | 1.00 | 1.00 | 48 |
| subtotal | 0.93 | 0.90 | 0.91 | 48 |
| tax | 0.93 | 0.89 | 0.91 | 46 |
| total | 0.93 | 0.90 | 0.91 | 48 |
| line_items.description | 1.00 | 1.00 | 1.00 | 116 |
| line_items.quantity | 1.00 | 1.00 | 1.00 | 116 |
| line_items.unit_price | 0.95 | 0.91 | 0.93 | 116 |
| line_items.amount | 0.95 | 0.91 | 0.93 | 116 |

## Headline

- **STP rate: 90%** — 43/48 docs extracted with zero fields needing a human edit (every scored field correct).
- **Coverage @ accuracy: 12% auto-approved; of those, 100% fully correct** — 6/48 docs had no field flagged for review; 6/6 of those were in fact straight-through.
- _Auto-approval is gated by the confidence review threshold (T14, a single tunable knob). At the current setting it is conservative — everything it auto-approves is correct, at the cost of lower coverage; raising coverage means tuning that threshold against this set. Unlike the accuracy metrics above, coverage also shifts with the sample count N (it feeds the confidence signal), so this figure is a floor at N=1._

## STP by category

- **clean-native:** 30/30 STP (100%)
- **scanned:** 5/10 STP (50%)
- **edge:** 8/10 STP (80%)

## Failures & known limitations

- **inv_040** — IncompleteOutputException: The output is incomplete due to a max_tokens length limit.
- **inv_045** — IncompleteOutputException: The output is incomplete due to a max_tokens length limit.

_These are the largest docs (40 line items). Their JSON output exceeds Claude's 8192-token `max_tokens` cap, so the extractor **fails safe** — the truncated output surfaces as an error (Instructor's `IncompleteOutputException`; the pipeline also guards `stop_reason=max_tokens`, PRD §8) rather than returning silently-truncated JSON. Output pagination / chunking for very large invoices is backlog._

---

_Run with `_N_SAMPLES=1` (self-consistency disabled): N is a per-doc confidence signal, not an accuracy lever, so N=1 measures the same accuracy at ~3x lower cost. Final-tier model varies by cascade — clean-native stays on Haiku 4.5; scanned/edge escalate to Opus 4.8._
