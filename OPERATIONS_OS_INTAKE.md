# Operations OS — supplier email intake

Phase 1 of the B2B Collection Management roadmap. A forwarded supplier email
becomes a **Collection Job**: attachments classified, brand and season detected,
volume and data gaps reported — before anything is generated.

Nothing here writes to SAP. It is the front door only.

---

## What it does

```
supplier email  ──▶  n8n  ──▶  POST /api/intake/email  ──▶  Collection Job
                                                             ├─ attachments classified
                                                             ├─ brand + season detected
                                                             ├─ order sheet / price list parsed
                                                             └─ intake report + gaps
```

Visible at **`/collections`**, detail at `/collections/{id}`.

### Attachment classification

| Kind | Recognised from |
|---|---|
| Order sheet | `.xlsx/.xls/.csv` named *order*, *ordersheet*, *buy*, *booking*, *preorder*, *reorder* — and any unnamed spreadsheet |
| Price list | named *price*, *pricelist*, *RRP*, *MSRP*, *wholesale*, *cost* |
| Catalog | `.pdf`, or named *lookbook*, *catalogue*, *linesheet* |
| Image package | `.zip/.rar/.7z`, or a bare image file |
| Other | anything else — reported, never guessed into a package |

A wrong guess is corrected on the collection page; the correction is flagged so
the rules can be reviewed against the supplier files that defeated them, and the
job is re-analysed immediately.

### What the report tells you

- Which of the four inputs arrived and which are missing
- Styles / colour styles / SKUs
- Lines missing a barcode, purchase price, RRP or size
- Brand or season that could not be detected
- **More than one brand named in the same drop** — suppliers do send these, and
  filing them under one brand silently would be wrong

---

## Configuration

```bash
INTAKE_API_KEY=           # required — endpoint 404s until set
INTAKE_OWNER_EMAIL=       # account that owns emailed collections
INTAKE_MAX_FILE_MB=500    # per attachment
```

Generate the key with `openssl rand -hex 32`. Leaving it empty keeps the
endpoint disabled, so a deploy that has not configured it cannot be posted to.

---

## Wiring n8n

1. **Trigger** — IMAP / Gmail node on the operations mailbox, "download
   attachments" enabled.
2. **HTTP Request** node:
   - Method `POST`, URL `https://ordersheet.flendergroup.com/api/intake/email`
   - Header `X-Intake-Key: <INTAKE_API_KEY>` (a `Bearer` token also works)
   - Body type **multipart/form-data**
   - Fields: `sender`, `subject` — and optionally `brand`, `season`, `supplier`
     to override detection
   - Every attachment as a repeated binary part named **`files`**

The response is the intake report, ready to feed straight into the reply email:

```json
{
  "ok": true, "job_id": 12,
  "brand": "Carhartt WIP", "season": "SS27", "status": "needs_input",
  "styles": 1383, "skus": 8819,
  "received": ["order_sheet"],
  "missing": ["price_list", "catalog", "images"],
  "warnings": ["42 lines without a barcode"],
  "summary": "Carhartt WIP — SS27\n\nFiles received:\n  OK  Order sheet\n...",
  "url": "/collections/12"
}
```

`summary` is preformatted plain text — send it as the reply body.

### Testing without a mailbox

```bash
curl -X POST https://ordersheet.flendergroup.com/api/intake/email \
  -H "X-Intake-Key: $INTAKE_API_KEY" \
  -F "subject=Carhartt WIP SS27 order sheet" \
  -F "sender=sales@supplier.com" \
  -F "files=@SS27_ORDER_SHEET.xlsx" \
  -F "files=@SS27_LOOKBOOK.pdf"
```

Or skip the API entirely and drop the files in at `/collections`. Same pipeline,
same analysis — it exists so the intake can be exercised before the mailbox is live.

---

## Status model

`received → analysed → needs_input` (`error` on failure).

`needs_input` means an expected input is missing, the brand or season could not
be determined, or nothing parseable arrived. The next phases extend this list
rather than replacing it.

---

## What is deliberately not here yet

Reference comparison against SAP and Portal, the four output packages, the
review engine, approval gates, Dropbox delivery and reconciliation. Each needs
inputs from Flender that intake does not — see the build plan.

Supplier files are never modified. A PDF line sheet is converted to a sibling
`.xlsx` for parsing and the original is kept, so a job can always be re-analysed
from the source of record.
