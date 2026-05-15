# AI Project Context: invoicelines-extractor

**Repository:** `swapnilkhalekarWK/invoicelines-extractor`  
**Primary runtime:** Python CLI prototype  
**Current status:** Narrow prototype / proof of concept for invoice line extraction from OCR text and coordinates  
**Last reviewed:** 2026-05-15

---

## 1. Project purpose

`invoicelines-extractor` is a prototype for extracting **invoice line items only** from OCR output.

The upstream PDF-to-OCR step is assumed to already exist, primarily through Amazon Textract or a Textract-derived wrapper. This project should not focus on extracting full invoice metadata such as addresses, supplier identity, totals, tax summaries, payment instructions, or invoice headers unless those fields are needed to identify or validate the line-item table.

The core problem this project solves is:

> Convert OCR words plus normalized coordinate boxes into structured invoice line records.

The project’s working thesis is that many invoice line-item tables can be extracted with a **template-free, geometry-first, rule-assisted parser** before resorting to expensive APIs, trained ML models, or manual review.

---

## 2. Current repository shape

At the time of review, the repository is intentionally small:

```text
invoicelines-extractor/
├── README.md
├── PROJECT_CONTEXT.md
├── MANAGEMENT_PRESENTATION.md
└── extract_invoice_lines.py
```

### File roles

| File | Role |
|---|---|
| `extract_invoice_lines.py` | Main executable Python CLI and all extraction logic. |
| `README.md` | User-facing run instructions, supported OCR formats, output fields, and current limitations. |
| `PROJECT_CONTEXT.md` | Original project concept and extraction strategy. |
| `MANAGEMENT_PRESENTATION.md` | Business/strategic narrative for moving from prototype to production. |

There is currently no package structure, no tests, no dependency file, no CI configuration, and no separate modules.

---

## 3. Product scope

### In scope

The parser should extract structured invoice lines such as:

- Product/service rows
- Discount or adjustment rows when visible in the line-item region
- Optional diagnostics useful for tuning, such as bounding boxes and raw OCR row text

### Out of scope unless needed for validation

- Supplier/vendor metadata
- Buyer metadata
- Invoice date and invoice number
- Payment terms
- Tax summary tables
- Subtotals and grand totals
- Full PDF OCR
- Training a classifier or ML model in the first prototype

---

## 4. Supported input formats

The parser currently supports three OCR JSON shapes:

### 4.1 Project Textract-derived wrapper

```json
{
  "DocumentUniqueId": "...",
  "Serialized": "{\"Pages\": [...]}"
}
```

The `Serialized` field may be a JSON string or already-parsed payload.

### 4.2 Serialized pages payload

```json
{
  "Pages": [...]
}
```

This format contains pages, regions, paragraphs, lines, text blocks, and words with normalized coordinates.

### 4.3 Standard Amazon Textract `DetectDocumentText` output

```json
{
  "Blocks": [
    {
      "BlockType": "WORD",
      "Text": "...",
      "Geometry": {
        "BoundingBox": {
          "Left": 0.0,
          "Top": 0.0,
          "Width": 0.0,
          "Height": 0.0
        }
      }
    }
  ]
}
```

Only `WORD` blocks are used.

---

## 5. Output contract

Each extracted row currently emits a JSON object with these fields:

```json
{
  "line_type": "product",
  "tax_code": "C",
  "item_code": "5354",
  "description": "VEDETT 6 X 33 cl extra blond bier 5,2 %",
  "quantity": "3",
  "unit_price": "4,537",
  "amount": "13,611",
  "confidence": 0.96,
  "page": 1,
  "bbox": {
    "left": 0.016,
    "top": 0.448,
    "right": 0.687,
    "bottom": 0.457
  },
  "raw_text": "C 5354 VEDETT 6 X 33 cl extra blond bier 5,2 % 3 4,537 13,611"
}
```

### Core business fields

These are the primary fields future work should preserve:

- `tax_code`
- `item_code`
- `description`
- `quantity`
- `unit_price`
- `amount`
- `confidence`

### Diagnostic fields

These are intentionally included for inspection, debugging, and tuning:

- `line_type`
- `page`
- `bbox`
- `raw_text`

---

## 6. CLI usage

The project is currently run as a single-file Python script:

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty
```

Diagnostics:

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty --debug-rows --debug-columns
```

### CLI flags

| Flag | Purpose |
|---|---|
| `ocr_file` | Required path to OCR JSON or project `.txt` wrapper. |
| `--pretty` | Pretty-print JSON output. |
| `--debug-rows` | Print reconstructed OCR rows to `stderr`. |
| `--debug-columns` | Print inferred column anchors and boundaries to `stderr`. |

---

## 7. Current extraction pipeline

The current script implements this pipeline:

```text
OCR JSON
  ↓
Parse words and normalized coordinates
  ↓
Represent each word as a geometry-aware Word object
  ↓
Cluster words into visual rows by vertical center position
  ↓
Sort words within each row from left to right
  ↓
Find the line-item table header row
  ↓
Infer column anchors and boundaries from header word positions
  ↓
Scan rows after the header
  ↓
Try product-line extraction
  ↓
Fallback to adjustment-line extraction
  ↓
Stop at totals/tax/payment summary signals
  ↓
Emit structured JSON lines with confidence scores
```

The important design principle is that this is **not plain text parsing**. The parser depends on the physical layout recovered from OCR coordinates.

---

## 8. Key data structures

### `Word`

Immutable dataclass representing a single OCR word.

Important fields:

- `text`
- `page`
- `left`
- `top`
- `right`
- `bottom`
- `center_x`
- `center_y`

Derived behavior:

- `height`
- `as_bbox()`

### `Row`

Mutable dataclass representing a reconstructed visual row.

Important fields:

- `page`
- `words`
- `index`

Derived behavior:

- `text`
- `center_y`
- `bbox`

### `ColumnLayout`

Immutable dataclass representing inferred table layout.

Important fields:

- `header_row_index`
- `page`
- `anchors`
- `boundaries`
- `explicit_anchors`
- `has_tax_code`

---

## 9. Header anchors and vocabulary

Header detection is driven by keyword anchors. Current conceptual groups include:

| Field | Anchor examples |
|---|---|
| `tax_code` | `f` |
| `item_code` | `art.nr`, `artnr`, `article`, `artikel`, `item`, `sku`, `code` |
| `description` | `benaming`, `description`, `omschrijving`, `product`, `service` |
| `deposit` | `leeggoed`, `deposit` |
| `pack_size` | `pack`, `size` |
| `quantity` | `hoev`, `qty`, `quantity`, `aantal` |
| `unit_price` | `eenhprijs`, `unit price`, `unitprice`, `prijs`, `price` |
| `amount` | `bedrag`, `amount`, `total`, `totaal`, `value`, `nett`, `net` |
| `vat` | `vat`, `btw`, `tax` |
| `drs` | `drs` |

A candidate header row currently needs at least three recognized header anchor groups.

---

## 10. Table stop signals

After extraction has begun, scanning stops when a subsequent row appears to enter invoice summary or payment territory.

Current stop-signal examples:

- `subtotal`
- `subtot`
- `totaal`
- `total`
- `vat`
- `btw`
- `tax`
- `te betalen`
- `balance due`

Important nuance: these signals stop extraction only after at least one line has already been extracted.

---

## 11. Adjustment-line support

The parser supports adjustment/discount rows using keyword anchors such as:

- `discount`
- `adjustment`
- `korting`
- `voordeel`
- `hoeveelheidsvoordeel`
- `toegekend`
- `verrekend`
- `rebate`

Adjustment rows are emitted with:

- `line_type = "adjustment"`
- `tax_code = null`
- `item_code = null`
- `quantity = null`
- `unit_price = null`
- `amount = visible amount`
- a confidence score based on adjustment keywords

The current design intentionally does **not** invent a negative sign for adjustment amounts unless the sign is visible in OCR.

---

## 12. Number parsing and validation

The parser recognizes common numeric formats including:

- integers
- decimal commas
- decimal points
- thousands separators
- optional currency symbols
- optional percent signs in contexts where percent is allowed

Arithmetic validation is used as a confidence signal:

```text
quantity * unit_price ~= amount
```

The current tolerance is:

```text
max(0.02, abs(amount) * 0.01)
```

So the parser accepts small rounding differences and 1% relative differences.

Arithmetic validation increases confidence but is not required for all lines because tax-inclusive amounts, deposits, or invoice-specific pricing rules may cause visible line amounts to differ from `quantity × unit_price`.

---

## 13. Confidence scoring

Product-line confidence currently starts from a base score and adds points for field completeness:

- tax code present
- item code present
- description present
- quantity present
- unit price present
- amount present
- arithmetic validation passed

The score is capped at `0.99`.

This is a heuristic confidence score, not a calibrated probability. Future production use should treat it as a ranking/triage signal until validated against labeled data.

---

## 14. Supported layout patterns

The README states that the current parser is intended to handle multiple column orders, including:

```text
Code Omschrijving Aantal Prijs Bedrag incl.
CODE DESCRIPTION PACK SIZE QTY PRICE VALUE VAT DRS
Code QTY DESCRIPTION VAT Price Nett
```

The code supports this partly through a broader header vocabulary and anchor-based numeric selection, rather than relying entirely on fixed column positions.

---

## 15. Current prototype limitations

Known limitations from the repository and code review:

1. **Single-file implementation**
   - All parsing, extraction, scoring, CLI, and debugging logic lives in `extract_invoice_lines.py`.

2. **No automated tests**
   - There are no checked-in unit tests, regression fixtures, or sample OCR files in the current repository snapshot.

3. **Header-driven inference**
   - The parser needs a recognizable table header with at least three header anchors.

4. **Single table focus**
   - The script finds one best header row and extracts rows after it on the same page.

5. **No multi-line description merge**
   - Continuation rows are not yet merged into prior line items.

6. **No trained classifier**
   - Extraction is rule-based; there is no ML model.

7. **Heuristic confidence**
   - Confidence values are useful for ranking but not yet statistically calibrated.

8. **Limited schema formalization**
   - Output is raw dictionaries, not Pydantic models or typed schema classes.

9. **No dependency management**
   - There is no `requirements.txt`, `pyproject.toml`, or lockfile.

10. **No package/module boundaries**
    - The project is not yet structured as an importable Python package.

11. **No explicit CI/CD**
    - No linting, formatting, type checking, or regression suite is currently configured.

12. **No PDF ingestion**
    - The project expects OCR JSON, not raw PDF input.

---

## 16. Important implementation observations

### 16.1 Row reconstruction

Rows are reconstructed by grouping words with nearby vertical center positions. The tolerance is derived from median word height and bounded between a lower and upper threshold.

This is central to the geometry-first approach. Changes here can affect every downstream extraction result.

### 16.2 Header detection

`find_header_row()` selects rows with enough recognized header anchors and chooses the best-scoring candidate.

Changes to `HEADER_ANCHORS` can improve format coverage but may also increase false header detection.

### 16.3 Column inference

`infer_columns()` starts from default anchors and overrides anchors when matching header words are found.

Boundaries are derived from midpoints between anchors. Some boundaries are clamped to avoid unreasonable description/numeric splits.

### 16.4 Product-line parsing

`parse_product_line()` expects an item-code-like token, optionally preceded by a one-letter tax code. It then chooses numeric tokens nearest quantity, unit price, and amount anchors.

This means the parser is partly layout-aware and partly token-shape-aware.

### 16.5 Adjustment-line parsing

`parse_adjustment_line()` is keyword-first and picks the last numeric-looking token as the amount.

This is simpler than product parsing and may need hardening if adjustment rows contain multiple numbers.

### 16.6 `split_columns()` currently appears unused

There is a `split_columns()` helper that assigns words into columns using inferred boundaries, but the main product parser currently relies more on nearest numeric words and used-word exclusion.

Future refactors should either use this helper intentionally or remove it if dead.

---

## 17. Suggested near-term engineering roadmap

### Phase 1: Stabilize the prototype

1. Add a `tests/fixtures/` directory with representative OCR JSON samples.
2. Add regression tests for known successful invoice layouts.
3. Add tests for:
   - project wrapper input
   - serialized pages input
   - Textract `Blocks` input
   - row reconstruction
   - header detection
   - arithmetic validation
   - adjustment rows
4. Add `pyproject.toml` with tooling for:
   - pytest
   - ruff
   - mypy or pyright
   - optional coverage
5. Preserve the CLI behavior while moving logic into modules.

Suggested future structure:

```text
invoicelines_extractor/
├── __init__.py
├── cli.py
├── models.py
├── parse_ocr.py
├── rows.py
├── headers.py
├── columns.py
├── extraction.py
├── scoring.py
└── normalization.py

tests/
├── fixtures/
├── test_parse_ocr.py
├── test_rows.py
├── test_headers.py
├── test_extraction.py
└── test_cli.py
```

### Phase 2: Improve extraction robustness

1. Support multi-line descriptions.
2. Support multiple tables and multiple pages.
3. Improve amount selection for rows with deposits, VAT, discounts, and DRS columns.
4. Introduce explicit output schemas.
5. Track extraction notes/reasons for confidence scoring.
6. Add layout diagnostics to help understand failures.

### Phase 3: Prepare for production evaluation

1. Build a labeled invoice-line evaluation set.
2. Measure precision, recall, field-level accuracy, and row-level accuracy.
3. Use confidence thresholds for accept/review/fallback routing.
4. Add observability:
   - extracted line count
   - average confidence
   - missing field rates
   - arithmetic pass rate
   - table header detection failures
5. Add safe fallback logic for low-confidence extraction.

---

## 18. Preferred assistant behavior for this project

When answering future questions inside this project, assume the following unless the user says otherwise:

1. The project goal is **invoice line extraction**, not full invoice extraction.
2. The upstream OCR layer already exists; focus on OCR JSON to structured lines.
3. Favor geometry-first, template-free, rule-assisted approaches before ML.
4. Preserve the current CLI behavior unless explicitly refactoring.
5. Treat arithmetic validation as a confidence signal, not a mandatory rule.
6. Do not invent hidden data such as negative adjustment signs unless visible or explicitly defined by business rules.
7. Prioritize testability and regression safety before adding more heuristics.
8. Ask whether a change affects production goals only when the tradeoff is material; otherwise make a safe best-effort recommendation.
9. Keep output schemas stable or propose versioning if fields change.
10. For code changes, prefer small, well-tested refactors over broad rewrites.

---

## 19. Coding conventions to apply going forward

Recommended conventions for future contributions:

- Python 3.11+ style typing.
- Keep dataclasses or move to Pydantic only when validation/serialization justifies it.
- Keep pure functions for parsing and extraction logic.
- Avoid global mutable state.
- Separate OCR parsing from extraction logic.
- Keep CLI thin.
- Add tests before changing anchor logic or confidence scoring.
- Use clear fixture names that describe the layout or failure mode.
- Add comments only where layout assumptions are non-obvious.
- Preserve Unicode handling for euro symbols and European decimal commas.

---

## 20. Risk register

| Risk | Impact | Mitigation |
|---|---:|---|
| Header vocabulary grows into brittle keyword soup | High | Version anchors, group by field, add regression tests. |
| New rules fix one layout but break another | High | Require fixture-based regression tests before accepting changes. |
| Confidence scores are mistaken for probabilities | Medium | Document as heuristic until calibrated. |
| Multi-line descriptions are dropped | Medium | Add continuation-row detection. |
| Summary/tax rows are misclassified as product lines | High | Strengthen stop signals and negative examples. |
| Adjustment rows with multiple numbers choose wrong amount | Medium | Improve amount-selection logic using geometry and keyword positions. |
| No packaging/CI slows iteration | Medium | Add `pyproject.toml`, pytest, linting, type checks. |

---

## 21. Definition of done for future improvements

A future change should ideally include:

1. A clear description of the invoice layout or failure mode.
2. One or more fixtures reproducing the behavior.
3. Tests proving the new behavior.
4. Tests proving existing layouts still pass.
5. A short explanation of any new header anchors or scoring changes.
6. No breaking change to the CLI or JSON schema unless explicitly approved.
7. Updated README/context docs when behavior changes.

---

## 22. Key source-derived facts

- The README defines the project as a narrow first prototype for extracting only invoice line items from OCR text and coordinates.
- The README documents support for the project Textract-derived OCR wrapper and standard Amazon Textract `DetectDocumentText` JSON containing `WORD` blocks.
- The script is a single-file CLI and contains the OCR parsing, row reconstruction, header detection, column inference, product extraction, adjustment extraction, confidence scoring, and CLI handling.
- The existing `PROJECT_CONTEXT.md` frames the approach as layout reconstruction rather than plain text extraction.
- The management presentation frames the strategic direction as a tiered, self-optimizing extraction pipeline with regression safety and fallback tiers.

---

## 23. One-sentence project summary

`invoicelines-extractor` is a Python prototype that reconstructs invoice table structure from Textract-style OCR word coordinates and extracts only product/service and adjustment line items into structured JSON using geometry, header anchors, numeric alignment, and arithmetic confidence checks.

---

## 24. MVP web interface and agent flow

The project now includes a local MVP web app around the extractor.

The web app should:

1. Allow uploading OCR files.
2. Show extraction output per uploaded file.
3. Treat files below 97% confidence as failed extraction reviews.
4. Show this message for low-confidence files:

```text
We could not extract invoice lines for this file
```

5. Show an `Understand why?` button for low-confidence files.
6. Invoke the OpenAI analysis agent when that button is clicked.
7. Display the LLM analysis plus a fix proposal.
8. Let the user either ignore the LLM analysis or save it for future upgrades.
9. Store saved analyses and fix proposals in SQLite.
10. Show saved analyses in a separate `Saved Future Upgrades` tab.

The OpenAI API key should be read from local `.env` as `OPENAI_API_KEY` and not committed or stored in the app.

Agent invocation code should live outside the web server handler. The current convention is:

- `openai_agent.py` owns OpenAI request construction and response parsing.
- `.env.example` documents required local settings.
- `.env` is ignored by git.
- `AI_PROJECT_CONTEXT.md` is loaded into every agent prompt.

## MVP Review Rule

The web app flags a file for review when:

- no invoice lines are extracted, or
- the minimum extracted line confidence is below `0.97`.

When a file is flagged, the interface shows:

```text
We could not extract invoice lines for this file
```

The user can click `Understand why?` to invoke the OpenAI analysis agent.

## Agent Goal

The OpenAI analysis agent should diagnose why the current regex and geometry based extractor struggled with a specific OCR file.

The agent should compare:

- the raw OCR file content
- reconstructed rows
- inferred layout anchors
- extraction output
- confidence scores
- extraction errors, if any

The response should help future engineering work. It must include:

- a concise explanation suitable for a product user
- a concrete fix proposal suitable for a developer

The fix proposal should focus on improvements to the current extractor, such as:

- header vocabulary additions
- column anchor inference
- row reconstruction
- multi-line description handling
- tax-inclusive amount handling
- pack-size or VAT column handling
- discount or adjustment row handling
- confidence scoring
- fallback strategies when headers are missing

## Current Known Limits

- Multi-line descriptions are not merged yet.
- Tax-inclusive totals may be valid even when arithmetic validation fails.
- Header-driven inference may miss uncommon column names.
- Tables with missing or weak headers need stronger fallback logic.
- The project does not yet train or use a classifier.
- The app is local-only and does not include authentication or deployment packaging.
