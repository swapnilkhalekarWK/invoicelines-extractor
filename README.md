# Invoice Line Extraction Prototype

This is a narrow first prototype for extracting only invoice line items from OCR text and coordinates.

It currently targets the sample Textract-derived OCR wrapper in this project and also accepts standard Amazon Textract `DetectDocumentText` JSON containing `WORD` blocks.

## Run

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty
```

Optional diagnostics:

```powershell
python .\extract_invoice_lines.py .\path\to\ocr-output.txt --pretty --debug-rows --debug-columns
```

## What It Does

1. Parses OCR words and normalized coordinate boxes.
2. Reconstructs visual rows by clustering words on vertical center position.
3. Detects the line-item table header.
4. Infers field anchors from header word positions.
5. Extracts product/service rows.
6. Extracts discount or adjustment rows.
7. Uses arithmetic validation, such as `quantity * unit_price ~= amount`, as a confidence signal.

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
