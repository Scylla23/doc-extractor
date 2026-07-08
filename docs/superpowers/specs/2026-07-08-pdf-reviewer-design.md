# PDF Reviewer — design

**Date:** 2026-07-08
**Status:** Approved, ready for implementation plan

## Goal

Turn the single-shot storefront into a two-pane **reviewer**: after an invoice is
extracted, show the uploaded PDF on the left and the cited-field ledger on the
right. Let the user (a) click a citation to jump to and flash its page, (b) select
text on the PDF to set an existing field or add a new one, and (c) edit values
inline and save the corrected record back to the API.

This is a UI/reviewer feature layered on the existing MVP. It does not change the
extraction pipeline, schema, or scope (invoices only).

## Non-goals

- No exact-quote highlight boxes. Citation flash is **page-level only** (scroll +
  flash the page). Text-region highlighting via the pdf.js text layer is out.
- No PDF storage server-side. The reviewer renders the **local File** the user
  dropped; on reload the PDF is gone (single-session reviewer).
- No durable persistence. Saved corrections overwrite the **in-memory** job dict
  and are lost on restart — same ceiling as the existing job store.
- No new schema fields, no doc types beyond invoices.

## Architecture

Grow the existing static storefront (`web/`) rather than add a second page. State
lives in one page; `body[data-state]` drives the view:

- `idle` / `loading` — unchanged drop-zone flow.
- `review` — **new**: two-pane split, drop-zone collapses to a small "new file"
  button.

The reviewer logic is split into its own file `web/review.js` to keep `app.js`
focused on upload + polling. `app.js` hands the raw `File` and the extracted
record to `review.js` on success.

```
app.js:  drop → POST /extract → poll /jobs/{id} → on done: enterReview(file, result)
review.js: render PDF (pdf.js) | render editable ledger | flash | select-popover | save
main.py:  + PATCH /jobs/{job_id}/result  (overwrite in-memory record)
```

### Renderer

pdf.js (Mozilla) loaded via CDN — the storefront is static with no bundler, and a
native `<iframe>`/`<embed>` gives no text-selection API or page coordinates.
pdf.js renders the local File (from the drop / file input) as stacked page
canvases, each wrapped in an element tagged `data-page="N"` (1-based, matching the
`page` field in the schema). A text layer is rendered per page so browser text
selection works.

## Components

### `web/review.js` (new)

- `enterReview(file, record)` — set `data-state="review"`, render PDF from `file`,
  render the ledger from `record`, wire flash + selection + save.
- **PDF render** — `pdfjsLib.getDocument(arrayBuffer)`, loop pages, render canvas +
  text layer into a `.pdf-page[data-page=N]` container.
- **Ledger render** — reuse the existing field-row shape from `app.js` (label /
  value / confidence meter / cite). Moved or shared, not duplicated. Each cite is
  now a `<button>`; each value is inline-editable.
- **Citation flash** — `flashPage(n)`: `scrollIntoView` the `.pdf-page[data-page=n]`
  + toggle a `.flash` class driving a CSS keyframe (border/tint pulse).
- **Selection popover** — on `mouseup` inside the PDF pane with a non-empty
  selection: show a small popover near the selection with **Set value of…**
  (dropdown of existing schema fields) and **Add new field** (prompt for a key).
  Resolve the selection's page from the enclosing `.pdf-page[data-page]`.
- **Editing** — click a value → inline input/`contenteditable`; on commit, update
  the in-memory record and mark the field manual.
- **Save / Download** — Save → `PATCH /jobs/{id}/result`; Download → client-side
  Blob of the current record JSON.

### Field model for manual edits

Every field keeps the existing shape (`value`, `confidence`, `source_quote`,
`page`, `review_required`). A manual edit or a selection-created field sets:

- `value` = entered / selected text (numbers coerced for money/qty fields where
  the existing formatters expect numbers; leave as string otherwise)
- `source_quote` = selected text (for selection-created; unchanged for inline edit)
- `page` = selection's page (for selection-created; unchanged for inline edit)
- `review_required` = false
- a `manual: true` marker so the UI shows "manual" instead of a confidence meter.

New keys created via **Add new field** land in a **Custom Fields** group on the
right, kept distinct from the fixed invoice schema. They are stored under a
`custom_fields` object on the record so the PATCH payload round-trips them.

### `app/main.py` (edit)

`PATCH /jobs/{job_id}/result` — body is the full corrected record (same JSON the
UI holds). Overwrites `jobs[job_id].result` in the in-memory dict; 404 if the job
is unknown, 409 if it is not yet `done`. Returns the stored record.

`# ponytail: in-memory job dict; corrections lost on restart — swap for Supabase
in backlog when multi-instance` (same ceiling already noted on the job store).

### `web/index.html`, `web/styles.css` (edit)

- `index.html` — add the two-pane review container and the pdf.js CDN `<script>`;
  add Save / Download / new-file controls.
- `styles.css` — `data-state="review"` split layout (PDF pane scrolls; ledger pane
  scrolls independently), the flash keyframe, the selection popover, the
  Custom Fields group, and the "manual" field styling.

## Data flow

1. Upload + poll unchanged. On `done`, `app.js` calls
   `enterReview(theFile, job.result)`.
2. review.js renders PDF + editable ledger.
3. Click cite → flash its page. Select text → popover → set/add field (marked
   manual, page captured). Click value → inline edit.
4. Save → PATCH the record to the API; Download → save JSON locally.

## Error handling

- pdf.js fails to load / parse → show an inline error in the PDF pane, keep the
  ledger usable ("Couldn't render the PDF; data is still editable").
- PATCH fails → non-blocking error near the Save button; edits remain in memory so
  the user can retry or Download.
- Selection with no resolvable page (shouldn't happen) → popover still allows
  add/set but omits `page`.

## Testing / verification

- `app/main.py`: extend the module self-check (`python -m app.main` or existing
  test) to cover PATCH — unknown job 404, not-done 409, happy-path overwrite +
  round-trip of a `custom_fields` key.
- Frontend is static/vanilla with no test harness; verify manually per the
  checklist below. Keep any pure helper (e.g. record-merge / number coercion) in a
  small function with an inline assert-based check if logic is non-trivial.

### Manual verification checklist

- Extract an invoice → reviewer opens with PDF left, ledger right.
- Click several citations → correct page scrolls into view and flashes.
- Select PDF text → **Set value of…** updates the chosen field (marked manual,
  page shown); **Add new field** creates a Custom Fields entry.
- Inline-edit a value → persists in the record, marked manual.
- Save → 200; re-fetch `/jobs/{id}` shows the corrected record incl. custom fields.
- Download → JSON matches the on-screen record.

## Files touched

- `web/index.html` — panes, pdf.js CDN script, controls.
- `web/styles.css` — split layout, flash, popover, custom-fields + manual styles.
- `web/review.js` — **new**: render, flash, selection, edit, save.
- `web/app.js` — hand off to reviewer on success; share field-row rendering.
- `app/main.py` — `PATCH /jobs/{job_id}/result`.
