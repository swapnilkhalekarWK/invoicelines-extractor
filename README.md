# Invoice Lines Extractor MVP

This project extracts invoice line items from OCR text and coordinates, then gives reviewers a small local interface for inspecting extraction results and saving future upgrade ideas.

It currently targets Textract-derived OCR wrappers and also accepts standard Amazon Textract `DetectDocumentText` JSON containing `WORD` blocks.

## Run The Web App

```powershell
python .\app.py
```

Then open:

```text
http://127.0.0.1:8000
```

The web app stores local runtime data in `data/`, including uploaded OCR files and the SQLite database.

## OpenAI Setup

The "Understand why?" flow uses the OpenAI Responses API. No API key is stored in the app.

Create a local `.env` file from the committed template:

```powershell
Copy-Item .\.env.example .\.env
```

Then edit `.env`:

```text
OPENAI_API_KEY=your-key-here
OPENAI_MODEL=gpt-4.1-mini
OPENAI_MAX_OCR_CHARS=120000
```

The `.env` file is ignored by git and should not be committed.

The OpenAI call is wrapped in `openai_agent.py`. Every agent invocation includes the contents of `AI_PROJECT_CONTEXT.md` in the prompt.

If `OPENAI_API_KEY` is not configured in `.env`, the interface will show a clear configuration error when "Understand why?" is clicked.

## Run The CLI

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty
```

Optional diagnostics:

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty --debug-rows --debug-columns
```

## What It Does

1. Uploads OCR files through the local web interface.
2. Parses OCR words and normalized coordinate boxes.
3. Reconstructs visual rows by clustering words on vertical center position.
4. Detects the line-item table header.
5. Infers field anchors from header word positions.
6. Extracts product/service rows.
7. Extracts discount or adjustment rows.
8. Uses arithmetic validation, such as `quantity * unit_price ~= amount`, as a confidence signal.
9. Flags files below the 97% confidence threshold.
10. Sends low-confidence OCR content to OpenAI for failure analysis when requested.
11. Saves selected LLM responses and fix proposals in the "Saved Future Upgrades" tab.

The parser is still template-free, but it now handles multiple column orders, including:

- `Code Omschrijving Aantal Prijs Bedrag incl.`
- `CODE DESCRIPTION PACK SIZE QTY PRICE VALUE VAT DRS`
- `Code QTY DESCRIPTION VAT Price Nett`

## Output Fields

Each extracted line includes:

- `line_type`
- `tax_code`
- `item_code`
- `description`
- `quantity`
- `unit_price`
- `amount`
- `confidence`
- `page`
- `bbox`
- `raw_text`

`line_type`, `page`, `bbox`, and `raw_text` are prototype diagnostics beyond the requested core fields. They make it easier to inspect and tune extraction behavior.

## Current Prototype Limits

- Field inference is still header-driven and tuned for clean tabular layouts.
- Multi-line descriptions are not merged yet.
- Adjustment rows return the visible amount without inventing a sign.
- The first implementation is rule-based; it does not train or use a classifier.
- Tax-inclusive totals may be valid line amounts even when `quantity * unit_price` does not match, so they receive lower confidence than arithmetically verified rows.
- Authentication, multi-user access, and deployment packaging are not included yet.
